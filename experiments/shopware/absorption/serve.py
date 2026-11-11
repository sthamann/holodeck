"""Serve installed Shopware-compatible operation profiles from model forwards."""
from argparse import ArgumentParser
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import parse_qs, urlsplit

from learn import read, request_context, route_pattern

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'benchmark'))
from forward_vm import Lowering
from learn_policy import require_offline

HERE = Path(__file__).resolve().parent


class Unsupported(ValueError):
    pass


class Deck:
    def __init__(self, model, catalog, db):
        self.ops = [(op, route_pattern(op['path']), {key: Lowering().compile(op[key]).forward
                    for key in ('record_id', 'body_contract', 'status', 'response', 'writes') if op[key] is not None})
                    for op in model['operations']]
        self.views = [(route_pattern(view['route']), compile_view(view)) for view in model['views']]
        self.db = sqlite3.connect(db)
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            self.db.executemany('INSERT OR IGNORE INTO records VALUES (?,?)',
                                [(item['id'], json.dumps(item)) for item in catalog])

    def record(self, identity):
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity):
            raise Unsupported('Unsupported record reference')
        row = self.db.execute('SELECT document FROM records WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return json.loads(row[0])

    def project(self, url):
        for pattern, forward in self.views:
            if match := pattern.fullmatch(url):
                state = self.record(match['record'])
                result = forward({'record': state})
                if 'action' in result:
                    result['action'] = {**result['action'], 'route': re.sub(r'\{\w+\}', state['id'], result['action']['route'])}
                return result
        raise Unsupported('No learned UI profile for this route')

    def execute(self, method, path, headers, body):
        for op, pattern, graph in self.ops:
            if method != op['method'] or not (match := pattern.fullmatch(path)):
                continue
            header, token = (('Authorization', 'Bearer absorption-admin') if op['scope'] == 'admin'
                             else ('sw-access-key', 'absorption-store'))
            if headers.get(header) != token:
                raise PermissionError('Local lab access key required')
            env = {'body': body, 'params': match.groupdict()}
            identity = graph['record_id'](env)
            state = self.record(identity)
            contract = request_context(body, identity, op['body_slots'])
            if json.dumps(graph['body_contract'](contract), sort_keys=True) != json.dumps(body, sort_keys=True):
                raise Unsupported('Request is outside the learned OpenAPI operation profile')
            env['record'] = state
            status, response = graph['status'](env), graph['response'](env)
            if 'writes' in graph:
                proposed = graph['writes'](env)
                if (proposed.keys() != state.keys() or proposed['id'] != identity or
                        any(type(proposed[key]) is not type(value) for key, value in state.items())):
                    raise Unsupported('Typed write is outside the installed record contract')
                with self.db:
                    self.db.execute('UPDATE records SET document=? WHERE id=?', (json.dumps(proposed), identity))
            return status, response
        raise Unsupported('Operation has no installed model profile')


def compile_view(view):
    fields = {'surface': ['literal', view['surface']], 'title': ['ref', 'record', view['heading']]}
    fields['controls'] = ['vector', *(['record', {
        **{key: ['literal', value] for key, value in control.items()},
        'value': ['ref', 'record', control['field']],
    }] for control in view['controls'])]
    if 'action' in view:
        fields['action'] = ['record', {**{key: ['literal', value] for key, value in view['action'].items()
                                         if key != 'body'}, 'body': view['action']['body']}]
    return Lowering().compile(['record', fields]).forward


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, deck, **kwargs):
        self.deck = deck
        super().__init__(*args, **kwargs)

    def respond(self, status, body, mime='application/json'):
        data = (b'' if status == 204 else json.dumps(body, ensure_ascii=False).encode()) if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self):
        url = urlsplit(self.path)
        try:
            if self.command == 'GET' and url.path == '/__project':
                return self.respond(200, self.deck.project(parse_qs(url.query)['url'][0]))
            if self.command == 'GET' and not url.path.startswith(('/api/', '/store-api/')):
                return self.respond(200, (HERE / 'surface.html').read_bytes(), 'text/html; charset=utf-8')
            if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{self.server.server_port}'):
                raise PermissionError('Invalid lab origin')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 32768:
                raise Unsupported('Body exceeds lab budget')
            body = json.loads(self.rfile.read(size)) if size else {}
            if not isinstance(body, (dict, list)) or url.query:
                raise Unsupported('Unsupported request envelope')
            self.respond(*self.deck.execute(self.command, url.path, self.headers, body))
        except PermissionError as error:
            self.respond(401, {'error': str(error)})
        except (KeyError, IndexError):
            self.respond(404, {'error': 'Unrecognized record or request binding'})
        except (ValueError, TypeError) as error:
            self.respond(501, {'error': str(error)})

    do_GET = do_POST = do_PATCH = dispatch


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('model', type=Path)
    cli.add_argument('catalog', type=Path)
    cli.add_argument('database', type=Path)
    cli.add_argument('--port', type=int, default=55746)
    cli.add_argument('--teacher-port', type=int, default=55745)
    args = cli.parse_args()
    require_offline(args.teacher_port)
    deck = Deck(read(args.model), read(args.catalog), args.database)
    with HTTPServer(('127.0.0.1', args.port), partial(Handler, deck=deck)) as server:
        print(f'http://127.0.0.1:{server.server_port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
