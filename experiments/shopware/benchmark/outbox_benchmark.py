"""Exercise a real loopback receiver, lost replies and restarted outbox workers."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
from selectors import DefaultSelector, EVENT_READ
import secrets
import socket
import sqlite3
import subprocess
import sys

from outbox_delivery import Outbox, canonical, digest, expected_receipt, request


RUNNER = Path(__file__).with_name('outbox_delivery.py')


def query(path, sql, values=()):
    with closing(sqlite3.connect(path)) as db:
        return db.execute(sql, values).fetchall()


def clone(source, target):
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as existing:
        with closing(sqlite3.connect(target)) as output:
            existing.backup(output)


def ledger_signature(path):
    rows = query(path, 'SELECT tenant,root,revision,value FROM graphs ORDER BY tenant,root')
    receipts = query(path, 'SELECT tenant,event_id,request_hash,value FROM receipts ORDER BY tenant,event_id')
    return sha256(canonical((rows, receipts)).encode()).hexdigest()


def offline(port):
    try:
        connected = socket.create_connection(('127.0.0.1', port), timeout=1)
    except ConnectionRefusedError:
        return
    with connected:
        raise RuntimeError('Shopware teacher must be offline')


@contextmanager
def receiver(db, config, port=0, drop=None):
    args = [sys.executable, str(RUNNER), 'receiver', str(db), str(config), '--port', str(port)]
    if drop:
        args.extend(('--drop-effect', drop))
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with DefaultSelector() as selector:
            selector.register(process.stdout, EVENT_READ)
            if not selector.select(timeout=10):
                raise RuntimeError('Receiver startup timed out')
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(process.stderr.read())
            yield json.loads(line)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()


def worker(db, config, now, *, crash=False, drain=False):
    args = [sys.executable, str(RUNNER), 'worker', str(db), str(config),
            '--now', str(now), '--lease', '10']
    if crash:
        args.append('--crash-after-reply')
    if drain:
        args.append('--drain')
    result = subprocess.run(args, text=True, capture_output=True, timeout=60)
    if crash:
        assert result.returncode == 73, (result.returncode, result.stderr, result.stdout)
        return {'hard_exit': result.returncode}
    result.check_returncode()
    return json.loads(result.stdout)


def payloads(db):
    journal = Outbox(db)
    try:
        journal.refresh()
        return [json.loads(row[0]) for row in journal.db.execute(
            'SELECT payload FROM effect_delivery ORDER BY tenant,id')]
    finally:
        journal.close()


def state(db, payload):
    return query(db, 'SELECT state,attempts,lease,lease_until,receipt FROM effect_delivery WHERE tenant=? AND id=?',
                 (payload['tenant'], payload['effect']['id']))[0]


def mark_count(db, payload):
    return query(db, 'SELECT COUNT(*) FROM applied_marks WHERE tenant=? AND effect_id=?',
                 (payload['tenant'], payload['effect']['id']))[0][0]


def receiver_record(db, payload):
    row = query(db, 'SELECT receipt FROM applied_marks WHERE tenant=? AND effect_id=?',
                (payload['tenant'], payload['effect']['id']))[0][0]
    return json.loads(row)


def verify_drained(db, sink, effects):
    for payload in effects:
        delivery = state(db, payload)
        assert delivery[0] == 'acked'
        assert mark_count(sink, payload) == 1
        assert json.loads(delivery[4]) == receiver_record(sink, payload) == expected_receipt(payload)


def bind_config(config, routes, port):
    for route in routes.values():
        route['port'] = port
    descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        target.write(canonical(routes) + '\n')


def first_result(process):
    return process['results'][0]


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('joined', type=Path)
    cli.add_argument('capture', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--teacher-port', type=int, required=True)
    args = cli.parse_args()
    offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.joined / 'both.sqlite'
    commerce = args.output / 'commerce.sqlite'
    clone(source, commerce)
    signature = ledger_signature(commerce)
    effects = payloads(commerce)
    if len(effects) < 3:
        raise ValueError('Need at least three committed effects for recovery cases')
    rows = [json.loads(line) for line in args.capture.read_text().splitlines()]
    merchants = {row['channel']: row['shop'] for row in rows}
    if {entry['tenant'] for entry in effects} != merchants.keys() or len(merchants) != 2:
        raise ValueError('Expected committed effects from two independent test shops')
    route_file = args.output / 'local-routes.json'
    routes = {tenant: {'token': secrets.token_urlsafe(32)} for tenant in merchants}
    bind_config(route_file, routes, 0)
    sink = args.output / 'recipient.sqlite'
    first, second = effects[:2]
    workers = []
    with receiver(sink, route_file, drop=first['effect']['id']) as started:
        bind_config(route_file, routes, started['port'])
        lost = worker(commerce, route_file, 1000)
        workers.append(lost)
        assert first_result(lost)['status'] == 'retry'
        assert first_result(lost)['id'] == first['effect']['id']
        assert state(commerce, first)[:2] == ('pending', 1)
        assert mark_count(sink, first) == 1
        assert receiver_record(sink, first) == expected_receipt(first)
    # No listener is present during this retry.
    outage = worker(commerce, route_file, 1001)
    workers.append(outage)
    assert first_result(outage)['status'] == 'retry'
    assert first_result(outage)['id'] == first['effect']['id']
    assert state(commerce, first)[:2] == ('pending', 2)

    browser = []
    with receiver(sink, route_file, port=started['port']) as restarted:
        recovered = worker(commerce, route_file, 1003)
        workers.append(recovered)
        assert first_result(recovered)['status'] == 'acked'
        assert first_result(recovered)['id'] == first['effect']['id']
        assert state(commerce, first)[:2] == ('acked', 3)
        hard_exit = worker(commerce, route_file, 1004, crash=True)
        pending = state(commerce, second)
        assert pending[:2] == ('leased', 1)
        assert mark_count(sink, second) == 1
        assert receiver_record(sink, second) == expected_receipt(second)
        other = worker(commerce, route_file, 1005)
        workers.append(other)
        assert first_result(other)['id'] != second['effect']['id']
        assert state(commerce, second) == pending
        resumed = worker(commerce, route_file, 1014)
        workers.append(resumed)
        assert first_result(resumed)['id'] == second['effect']['id']
        assert state(commerce, second)[:2] == ('acked', 2)
        journal = Outbox(commerce)
        try:
            assert not journal.ack({'tenant': second['tenant'], 'id': second['effect']['id'],
                                    'lease': pending[2]}, {'wrong': 'stale lease'})
        finally:
            journal.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = [job.result() for job in [
                pool.submit(worker, commerce, route_file, 1015, drain=True) for _ in range(2)]]
        workers.extend((left, right))
        verify_drained(commerce, sink, effects)
        assert ledger_signature(commerce) == signature
        empty = worker(commerce, route_file, 1016)
        workers.append(empty)
        assert first_result(empty)['status'] == 'empty'

        wrong_tenant = next(tenant for tenant in merchants if tenant != first['tenant'])
        forbidden, _ = request(restarted['port'], first, routes[first['tenant']]['token'], path_tenant=wrong_tenant)
        conflict = {**first, 'effect': {**first['effect'], 'root': first['effect']['root'] + '-different'}}
        rejected, _ = request(restarted['port'], conflict, routes[first['tenant']]['token'])
        assert (forbidden, rejected) == (403, 409)
        verify_drained(commerce, sink, effects)

    # The Chrome runs replay two of the same order fixtures with different
    # command IDs. Keep their recipient state separate from the main replay.
    browser_sink = args.output / 'browser-recipient.sqlite'
    with receiver(browser_sink, route_file) as browser_endpoint:
        bind_config(route_file, routes, browser_endpoint['port'])
        for n in (1, 2):
            target = args.output / f'browser-{n}.sqlite'
            clone(args.joined / f'browser-{n}/same-order.sqlite', target)
            original = ledger_signature(target)
            effects_from_browser = payloads(target)
            assert len(effects_from_browser) == 1
            payload = effects_from_browser[0]
            assert ':browser:' in payload['effect']['caused_by']
            sent = worker(target, route_file, 1020, drain=True)
            workers.append(sent)
            verify_drained(target, browser_sink, effects_from_browser)
            assert ledger_signature(target) == original
            browser.append({'shop': merchants[payload['tenant']], 'effect_id': payload['effect']['id'],
                            'root': payload['effect']['root'], 'external_receipt': receiver_record(browser_sink, payload)})

    route_file.unlink()
    totals = dict(query(sink, 'SELECT outcome,COUNT(*) FROM requests GROUP BY outcome'))
    browser_totals = dict(query(browser_sink, 'SELECT outcome,COUNT(*) FROM requests GROUP BY outcome'))
    counts = {}
    for recipient in (sink, browser_sink):
        assert not query(recipient, 'SELECT tenant,root FROM applied_marks GROUP BY tenant,root HAVING COUNT(*)>1')
        for tenant, count in query(recipient, 'SELECT tenant,COUNT(*) FROM applied_marks GROUP BY tenant'):
            counts[tenant] = counts.get(tenant, 0) + count
    assert sum(counts.values()) == len(effects) + len(browser)
    assert totals == {'applied': len(effects), 'duplicate': 2, 'conflict': 1}
    assert browser_totals == {'applied': len(browser)}
    offline(args.teacher_port)
    report = {
        'shopware_port_closed': args.teacher_port, 'source_ledger_sha256': signature,
        'commerce_effects': len(effects), 'browser_effects': len(browser),
        'external_effects_by_shop': {merchants[tenant]: count for tenant, count in counts.items()},
        'received_attempts': totals, 'browser_received_attempts': browser_totals,
        'lost_reply_after_commit': {'effect_id': first['effect']['id'], 'attempts': 3,
                                    'outage_between_attempts': True, 'same_receipt': True},
        'worker_crash_after_reply': {'effect_id': second['effect']['id'], 'attempts': 2,
                                     'exit_code': hard_exit['hard_exit'], 'lease_expiry_verified': True,
                                     'stale_ack_rejected': True},
        'receiver_pids': [started['pid'], restarted['pid']], 'workers': workers,
        'wrong_tenant_status': forbidden, 'changed_payload_status': rejected,
        'browser': browser,
        'source_payload_hashes': [digest(payload) for payload in effects],
        'committed_business_state_unchanged': True,
    }
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(canonical({key: report[key] for key in ('commerce_effects', 'browser_effects',
          'external_effects_by_shop', 'received_attempts', 'wrong_tenant_status', 'changed_payload_status')}))


if __name__ == '__main__':
    main()
