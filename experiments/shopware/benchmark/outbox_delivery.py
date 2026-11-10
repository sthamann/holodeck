"""Deliver already committed workflow effects to a local, idempotent receiver."""
from argparse import ArgumentParser
from contextlib import contextmanager
from hashlib import sha256
from hmac import compare_digest
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time


MAX_BODY = 65_536
ID = re.compile(r'[0-9a-f]{64}')
TENANT = re.compile(r'[A-Za-z0-9_-]{1,128}')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def open_db(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    return db


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


class Outbox:
    def __init__(self, path):
        self.db = open_db(path)
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS effect_delivery (
                tenant TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','leased','acked','failed')),
                available_at REAL NOT NULL DEFAULT 0, lease_until REAL, lease TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, receipt TEXT, error TEXT,
                PRIMARY KEY (tenant,id)
            )
        ''')

    def close(self):
        self.db.close()

    def refresh(self):
        with transaction(self.db):
            rows = self.db.execute("""
                SELECT g.tenant,g.root,e.value
                FROM graphs AS g, json_each(g.value,'$.workflow_effects') AS e
            """).fetchall()
            for tenant, root, raw in rows:
                effect = json.loads(raw)
                if effect['root'] != root or not TENANT.fullmatch(tenant) or not ID.fullmatch(effect['id']):
                    raise ValueError('Invalid committed effect identity')
                payload = canonical({'tenant': tenant, 'effect': effect})
                self.db.execute('INSERT OR IGNORE INTO effect_delivery(tenant,id,payload) VALUES (?,?,?)',
                                (tenant, effect['id'], payload))
                stored = self.db.execute('SELECT payload FROM effect_delivery WHERE tenant=? AND id=?',
                                         (tenant, effect['id'])).fetchone()[0]
                if payload != stored:
                    raise ValueError('Committed effect identity was reused with a different payload')

    def claim(self, now, lease_seconds):
        with transaction(self.db):
            selected = self.db.execute("""
                SELECT tenant,id,payload,attempts FROM effect_delivery
                WHERE (state='pending' AND available_at<=?) OR (state='leased' AND lease_until<=?)
                ORDER BY tenant,id LIMIT 1
            """, (now, now)).fetchone()
            if selected is None:
                return None
            job = dict(selected)
            lease = secrets.token_hex(16)
            self.db.execute("""
                UPDATE effect_delivery SET state='leased', lease_until=?, lease=?, attempts=attempts+1
                WHERE tenant=? AND id=?
            """, (now + lease_seconds, lease, job['tenant'], job['id']))
            return {**job, 'lease': lease, 'attempts': job['attempts'] + 1}

    def ack(self, job, receipt):
        return self.db.execute("""
            UPDATE effect_delivery SET state='acked', receipt=?, lease=NULL, lease_until=NULL, error=NULL
            WHERE tenant=? AND id=? AND state='leased' AND lease=?
        """, (canonical(receipt), job['tenant'], job['id'], job['lease'])).rowcount == 1

    def fail(self, job, now, reason, *, permanent=False):
        state = 'failed' if permanent else 'pending'
        available = now + min(60, 2 ** min(6, job['attempts'] - 1))
        return self.db.execute("""
            UPDATE effect_delivery SET state=?, available_at=?, error=?, lease=NULL, lease_until=NULL
            WHERE tenant=? AND id=? AND state='leased' AND lease=?
        """, (state, available, reason, job['tenant'], job['id'], job['lease'])).rowcount == 1


def request(port, payload, token, *, path_tenant=None, identity=None):
    tenant = path_tenant if path_tenant is not None else payload['tenant']
    effect_id = identity if identity is not None else payload['effect']['id']
    if not TENANT.fullmatch(tenant) or not ID.fullmatch(effect_id):
        raise ValueError('Invalid effect route')
    body = canonical(payload).encode()
    headers = {'Content-Type': 'application/json', 'Idempotency-Key': effect_id,
               'Authorization': 'Bearer ' + token}
    conn = HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        conn.request('POST', '/v1/effects/' + tenant, body, headers)
        response = conn.getresponse()
        raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise ValueError('Oversized receipt')
        return response.status, json.loads(raw)
    finally:
        conn.close()


def expected_receipt(payload):
    return {'tenant': payload['tenant'], 'effect_id': payload['effect']['id'],
            'payload_sha256': digest(payload), 'applied': True}


def deliver_once(outbox, routes, now, lease_seconds, crash=False):
    job = outbox.claim(now, lease_seconds)
    if job is None:
        return {'status': 'empty'}
    key = {'tenant': job['tenant'], 'id': job['id'], 'attempt': job['attempts']}
    route = routes.get(job['tenant'])
    if route is None:
        outbox.fail(job, now, 'Missing tenant endpoint', permanent=True)
        return {**key, 'status': 'failed'}
    payload = json.loads(job['payload'])
    try:
        status, receipt = request(route['port'], payload, route['token'])
        if status == 200:
            if receipt != expected_receipt(payload):
                raise ValueError('Receiver acknowledgement does not match the committed effect')
            if crash:
                os._exit(73)
            return {**key, 'status': 'acked' if outbox.ack(job, receipt) else 'lease-lost'}
        permanent = 400 <= status < 500 and status not in (408, 429)
        changed = outbox.fail(job, now, f'HTTP {status}', permanent=permanent)
        return {**key, 'status': ('failed' if permanent else 'retry') if changed else 'lease-lost'}
    except (OSError, HTTPException, ValueError) as error:
        changed = outbox.fail(job, now, type(error).__name__)
        return {**key, 'status': 'retry' if changed else 'lease-lost'}


class Receiver:
    def __init__(self, path):
        self.db = open_db(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS applied_marks (
                tenant TEXT NOT NULL, effect_id TEXT NOT NULL, root TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL, receipt TEXT NOT NULL,
                PRIMARY KEY (tenant,effect_id)
            );
            CREATE TABLE IF NOT EXISTS requests (
                seq INTEGER PRIMARY KEY, tenant TEXT NOT NULL, effect_id TEXT NOT NULL,
                outcome TEXT NOT NULL
            );
        """)

    def apply(self, payload):
        tenant, effect = payload['tenant'], payload['effect']
        receipt = expected_receipt(payload)
        checksum = receipt['payload_sha256']
        with transaction(self.db):
            prior = self.db.execute('SELECT payload_sha256,receipt FROM applied_marks WHERE tenant=? AND effect_id=?',
                                    (tenant, effect['id'])).fetchone()
            if prior is None:
                self.db.execute('INSERT INTO applied_marks VALUES (?,?,?,?,?)',
                                (tenant, effect['id'], effect['root'], checksum, canonical(receipt)))
                outcome, status = 'applied', 200
            elif prior['payload_sha256'] == checksum:
                outcome, status = 'duplicate', 200
                receipt = json.loads(prior['receipt'])
            else:
                outcome, status = 'conflict', 409
                receipt = {'error': 'Idempotency key already has another payload'}
            self.db.execute('INSERT INTO requests(tenant,effect_id,outcome) VALUES (?,?,?)',
                            (tenant, effect['id'], outcome))
        return status, receipt, outcome


def serve(path, routes, port, drop_effect):
    receiver = Receiver(path)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, value):
            encoded = canonical(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            prefix = '/v1/effects/'
            tenant = self.path.removeprefix(prefix)
            token = routes.get(tenant, {}).get('token')
            header = self.headers.get('Authorization', '')
            if not self.path.startswith(prefix) or token is None or not compare_digest(
                    header.encode(), ('Bearer ' + token).encode()):
                self.reply(403, {'error': 'Unknown tenant or invalid credentials'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_BODY:
                    raise ValueError('Invalid payload size')
                payload = json.loads(self.rfile.read(size))
                effect = payload['effect']
                identity = effect['id']
                if (payload['tenant'] != tenant or type(identity) is not str or not ID.fullmatch(identity)
                        or identity != self.headers.get('Idempotency-Key') or effect['kind'] != 'order.mark'
                        or type(effect['root']) is not str or not effect['root']):
                    raise ValueError('Invalid effect')
            except (ValueError, KeyError, TypeError):
                self.reply(422, {'error': 'Invalid effect'})
                return
            status, receipt, outcome = receiver.apply(payload)
            if identity == drop_effect and outcome == 'applied':
                # Real ambiguity: the receiver has committed, but no HTTP
                # response reaches the delivery process.
                self.close_connection = True
                return
            self.reply(status, receipt)

        def log_message(self, *_):
            pass

    try:
        with HTTPServer(('127.0.0.1', port), Handler) as server:
            print(canonical({'port': server.server_port, 'pid': os.getpid()}), flush=True)
            server.serve_forever()
    finally:
        receiver.db.close()


def main():
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    receiver = commands.add_parser('receiver')
    receiver.add_argument('--port', type=int, default=0)
    receiver.add_argument('--drop-effect')
    worker = commands.add_parser('worker')
    worker.add_argument('--now', type=float)
    worker.add_argument('--lease', type=float, default=10)
    worker.add_argument('--crash-after-reply', action='store_true')
    worker.add_argument('--drain', action='store_true')
    for command in (receiver, worker):
        command.add_argument('db', type=Path)
        command.add_argument('routes', type=Path)
    args = parser.parse_args()
    routes = json.loads(args.routes.read_text())
    if args.command == 'receiver':
        serve(args.db, routes, args.port, args.drop_effect)
        return
    if args.lease <= 0:
        parser.error('Lease must be positive')
    outbox = Outbox(args.db)
    try:
        outbox.refresh()
        results = []
        while True:
            result = deliver_once(outbox, routes, time.time() if args.now is None else args.now,
                                  args.lease, args.crash_after_reply)
            results.append(result)
            if not args.drain or result['status'] in ('empty', 'lease-lost', 'retry'):
                break
        print(canonical({'pid': os.getpid(), 'results': results}))
    finally:
        outbox.close()


if __name__ == '__main__':
    main()
