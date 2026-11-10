"""Serve the learned collection deck and its live projections on loopback."""
from argparse import ArgumentParser
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from admin_projection import AdminProjection, FULL
from composition import TextFeatures
from flow_learning import FIELDS, READY
from flow_transfer import verified_model
from graph_contract import Store, graph, root_of
from joined_runtime import JoinedDeck
from joint_effects import JointEffects
from learn_policy import json_lines, require_offline
from mixed_projection import Projector
from mixed_transfer import MixedDeck, Programs, sources


HERE = Path(__file__).resolve().parent
SHOPS = {
    'uferkauf': ('Uferkauf', 1, 'UFER18', ('Hafenbecher', 'Steingutkanne', 'Küstenteller',
                                          'Leinenservietten', 'Teesieb', 'Korkuntersetzer', 'Schale')),
    'heidewerk': ('Heidewerk', 5, 'HEIDE855', ('Werkbankschürze', 'Messwinkel', 'Holzöl',
                                            'Schleifklotz', 'Handbürste', 'Leinenschnur', 'Schraubzwinge')),
}
PATHS = {'/': ('index.html', 'text/html'), '/shop.css': ('shop.css', 'text/css'),
         '/shop.js': ('shop.js', 'text/javascript'), '/showcase.css': ('showcase.css', 'text/css'),
         **{f'/showcase/{i}.webp': (f'showcase/{i}.webp', 'image/webp') for i in range(1, 6)}}


class App:
    def __init__(self, args):
        lab = args.lab
        self.store = Store(args.db)
        checkpoint = lab / 'mixed-weights-v2'
        if json.loads((checkpoint / 'training.json').read_text())['runtime'] != sources():
            raise ValueError('Collection runtime changed after learning')
        self.programs = Programs(checkpoint / 'programs.json', checkpoint / 'learned.npz')
        flow_dir = lab / 'flow-weights'
        self.flow = verified_model(flow_dir, 'both', json.loads((flow_dir / 'training.json').read_text()))
        commerce = JointEffects.restore(json.loads((lab / 'joined-result/commerce.json').read_text()),
                                        lab / 'joined-result/commerce.safetensors')
        self.deck = MixedDeck(self.programs, JoinedDeck(commerce.select, self.flow))
        self.shops = json.loads(args.catalog.read_text())
        self.projector = Projector(args.base, args.weights, admin_weights=args.admin_weights)
        self.projections = {}
        self.audit = args.audit

    def shop_for(self, session):
        row = self.store.db.execute('SELECT tenant FROM graphs WHERE root=?', (session,)).fetchone()
        if row is not None:
            shop = next((shop for shop in self.shops.values() if shop['tenant'] == row[0]), None)
            if shop is not None:
                return shop
        raise ValueError('Sitzung nicht gefunden. Bitte den Shop neu laden.')

    def log(self, entry):
        if self.audit:
            with self.audit.open('a') as file:
                file.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def new_session(self, body):
        slug = body.get('shop') or next(iter(self.shops))
        shop = self.shops[slug]
        profile = {'name': shop['name'], 'shop': slug, 'demoCode': shop['code'],
                   'shops': [{'key': key, 'name': item['name']} for key, item in self.shops.items()]}
        if self.projector.admin is not None:
            profile['adminIntent'] = FULL
        existing = body.get('resume')
        if existing is not None:
            try:
                if self.shop_for(existing)['tenant'] == shop['tenant']:
                    return {'session': existing, **profile}
            except ValueError:
                pass
        root = uuid4().hex
        initial = graph(shop['tenant'], root, {'mixed.catalog': shop['catalog'], **dict.fromkeys(FIELDS, 0)},
                        {**shop['config'], 'mixed.checkpoint': self.programs.identity,
                         'flow.checkpoint': self.flow.identity})
        initial['workflow_effects'] = []
        self.store.seed(initial)
        return {'session': root, **profile}

    def orders(self, tenant):
        rows = self.store.db.execute('SELECT value FROM graphs WHERE tenant=? ORDER BY rowid DESC', (tenant,))
        return [state for (value,) in rows if 'mixed.order' in root_of(state := json.loads(value))['fields']]

    def order_row(self, state):
        fields = root_of(state)['fields']
        return {'id': state['root'], 'number': state['root'][:8].upper(), 'revision': state['revision'],
                **{key: fields[f'order.{key}'] for key in ('gross', 'payment', 'delivery', 'stage')}}

    def view(self, session, scope, nodes, selected=None):
        shop = self.shop_for(session)
        tenant = shop['tenant']
        state = self.store.read(tenant, session)
        fields = root_of(state)['fields']
        orders = self.orders(tenant) if scope == 'admin' else []
        data = {'orders': [self.order_row(order) for order in orders]}
        if scope == 'admin':
            if selected is not None:
                state = next((order for order in orders if order['root'] == selected), None)
                if state is None:
                    raise ValueError('Bestellung nicht gefunden.')
                fields = root_of(state)['fields']
                data['selection'] = {**fields['mixed.order'],
                                     **self.order_row(state), 'items': fields['mixed.order']['lines']}
                data['workflow'] = {'ready': bool(fields[READY]), 'records': state['workflow_effects']}
        else:
            data.update(catalog=fields['mixed.catalog'], cart=fields.get('mixed.quote'),
                        quantities={item['id']: item['quantity'] for item in fields.get('mixed.items', [])},
                        receipt=fields.get('mixed.order'))
        # Return read models by the role, with the presentation labels kept
        # separate from the numeric fields used by the learned programs.
        return {'nodes': nodes, 'data': data, 'names': shop['names'], 'revision': state['revision'],
                'session': session}

    def project(self, body):
        session, scope, request = (body[key] for key in ('session', 'scope', 'intent'))
        if not isinstance(request, str) or not 0 < len(request) <= 450:
            raise ValueError('Bitte eine kurze Anfrage eingeben.')
        nodes = self.projector.emit(scope, request)
        if not nodes:
            raise ValueError('Für diese Anfrage wurde keine Ansicht erzeugt.')
        view = self.view(session, scope, nodes, body.get('selected'))
        projection = uuid4().hex
        # One active projection per scope and session. Bind command calls to
        # the selected row as well as to the particular emitted action.
        self.projections[session, scope] = (projection, nodes, body.get('selected'))
        self.log({'projection': projection, 'shop': self.shop_for(session)['name'], 'scope': scope,
                  'intent': request, 'nodes': nodes})
        return {**view, 'projection': projection}

    def action(self, body):
        session, scope = body['session'], body['scope']
        projection, nodes, selected = self.projections[session, scope]
        if body['projection'] != projection:
            raise ValueError('Ansicht abgelaufen. Bitte neu erzeugen.')
        action = next((entry for node in nodes for entry in node['actions'] if entry['id'] == body['action']), None)
        if action is None:
            raise ValueError('Aktion ist in dieser Ansicht nicht verfügbar.')
        shop = self.shop_for(session)
        tenant, root = shop['tenant'], session
        state = self.store.read(tenant, session)
        fields = root_of(state)['fields']
        args = {}
        binding = action['binding']
        if binding == 'row.id':
            if selected is None or body.get('row') != selected:
                raise ValueError('Bitte zuerst eine Bestellung auswählen.')
            root = selected
        elif 'mixed.order' in fields and action['event'] != 'checkout.place':
            raise ValueError('Dieser Warenkorb wurde bereits bestellt.')
        elif binding == 'quantities':
            quantities = body['quantities']
            if not isinstance(quantities, dict) or not quantities or any(
                    key not in shop['names'] or type(value) is not int or not 1 <= value <= 999
                    for key, value in quantities.items()):
                raise ValueError('Bitte gültige Mengen für mindestens einen Artikel wählen.')
            args = {'items': [{'id': key, 'quantity': value} for key, value in quantities.items()]}
        elif not fields.get('mixed.quote'):
            raise ValueError('Bitte zuerst den Warenkorb aktualisieren.')
        elif binding == 'code' and body.get('code', '').strip().upper() != shop['code']:
            raise ValueError('Dieser Gutscheincode ist ungültig.')
        event = {'id': body['request'], 'root': root, 'kind': action['event'], 'args': args}
        receipt = self.store.apply(tenant, event, self.deck, body['revision'])
        view = self.view(session, scope, nodes, selected)
        self.log({'shop': shop['name'], 'action': action, 'root': root, 'receipt': receipt})
        return {**view, 'projection': projection, 'receipt': receipt}


def serve(app, port):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, mime):
            self.send_response(status)
            encoded = body if isinstance(body, bytes) else body.encode()
            self.send_header('Content-Type', mime if isinstance(body, bytes) else mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            entry = PATHS.get(urlsplit(self.path).path)
            if entry is None:
                self.send(404, 'Not found', 'text/plain')
                return
            name, mime = entry
            source = HERE / 'mixed_ui' / name
            if mime.startswith('image/'):
                if not source.is_file():
                    self.send(404, 'Not found', 'text/plain')
                    return
                self.send(200, source.read_bytes(), mime)
            else:
                self.send(200, source.read_text(), mime)

        def do_POST(self):
            routes = {'/api/session': app.new_session, '/api/project': app.project, '/api/action': app.action}
            if hasattr(app, 'activate'):
                routes['/api/activate'] = app.activate
            try:
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('JSON erforderlich.')
                if self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}':
                    raise ValueError('Lokaler Browser erforderlich.')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('Ungültige Eingabegröße.')
                payload = json.loads(self.rfile.read(size))
                result = routes[self.path](payload)
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                self.send(400, json.dumps({'error': str(error)}, ensure_ascii=False), 'application/json')
                return
            self.send(200, json.dumps(result, ensure_ascii=False), 'application/json')

        def log_message(self, *_):
            pass

    try:
        with HTTPServer(('127.0.0.1', port), Handler) as server:
            print(f'http://127.0.0.1:{server.server_port}/', flush=True)
            server.serve_forever()
    finally:
        app.store.close()


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('import', 'fit', 'fit-admin', 'serve'))
    parser.add_argument('--base', type=Path)
    parser.add_argument('--lab', type=Path, required=True)
    parser.add_argument('--weights', type=Path)
    parser.add_argument('--admin-weights', type=Path)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--db', type=Path, default=Path(':memory:'))
    parser.add_argument('--audit', type=Path)
    parser.add_argument('--port', type=int, default=55701)
    parser.add_argument('--teacher-port', type=int, default=55697)
    args = parser.parse_args()
    require_offline(args.teacher_port)
    if args.mode == 'import':
        if not args.catalog:
            parser.error('import requires --catalog')
        shops = {}
        rows = json_lines(args.lab / 'mixed-novel.jsonl')
        for slug, (shop, case, code, names) in SHOPS.items():
            row = next(row for row in rows if row['shop'] == shop and row['case'] == case)
            shops[slug] = {'name': shop, 'tenant': row['channel'], 'config': row['config'],
                           'catalog': row['catalog'], 'code': code,
                           'names': {item['id']: name for item, name in zip(row['catalog'], names)}}
        args.catalog.write_text(json.dumps(shops, ensure_ascii=False, indent=2) + '\n')
    elif args.mode == 'fit-admin':
        if not args.base or not args.admin_weights:
            parser.error('fit-admin requires --base and --admin-weights')
        AdminProjection(TextFeatures(args.base, (), [(('admin',), '')]), args.admin_weights,
                        HERE.parent / 'workspace/ui.jsonl', fit=True)
    elif not args.base or not args.weights:
        parser.error('fit and serve require --base and --weights')
    elif args.mode == 'fit':
        Projector(args.base, args.weights, fit=True)
    elif not args.catalog:
        parser.error('serve requires --catalog')
    else:
        serve(App(args), args.port)
