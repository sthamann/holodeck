"""Serve a compiled product mask and the installed Shopware API experts."""
from argparse import ArgumentParser
from functools import partial
from hashlib import sha256
import gzip
from http.server import HTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'absorption'))
from serve import Deck, Handler, Unsupported, read, require_offline
from forward_vm import Lowering
from actions import Actions


class MaskDeck(Deck):
    def __init__(self, model, catalog, db, mask, actions=None, action_products=()):
        super().__init__(model, catalog, db)
        self.actions = Actions(self.db, actions, action_products) if actions else None
        self.tabs = {tab['key']: tab for tab in mask['tabs']}
        self.slots = Lowering().compile(['record', {key: ['ref', 'record', key]
                                                   for key in ('name', 'stock', 'productNumber', 'id')}]).forward

    def mask(self, key, identity):
        if key not in self.tabs:
            raise Unsupported('This tab has no learned component tree')
        slots = self.slots({'record': self.record(identity)})
        action = self.project('/admin#/sw/product/detail/' + identity + '/base')['action']
        result = {'tab': self.tabs[key], 'slots': slots, 'action': action, 'tabs': list(self.tabs)}
        if self.actions and (product := self.actions.product(identity)):
            result.update(product=product, actionPrograms=self.actions.request_templates())
        return result


class MaskHandler(Handler):
    def product_request(self, url):
        actions = self.deck.actions
        if not actions:
            return False
        if self.command == 'GET' and url.path.startswith('/mask-media/'):
            picture = actions.picture(url.path.rsplit('/', 1)[-1])
            self.respond(200, picture, 'image/jpeg') if picture else self.respond(404, {'error': 'Media not found'})
            return True
        if self.command != 'POST' or (url.path not in ('/api/_action/sync', '/api/media')
                                     and not url.path.startswith('/api/_action/media/')):
            return False
        try:
            if (self.headers.get('Authorization') != 'Bearer absorption-admin' or
                    self.headers.get('Origin') not in (None, f'http://127.0.0.1:{self.server.server_port}')):
                raise PermissionError('Local lab access required')
            binary = url.path.startswith('/api/_action/media/')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= (8_000_000 if binary else 32768):
                raise ValueError('Request exceeds local budget')
            if binary and self.headers.get_content_type() != 'image/jpeg':
                raise ValueError('Expected a JPEG upload')
            raw = self.rfile.read(size)
            body = sha256(raw).hexdigest() if binary else json.loads(raw)
            query = {key: value[0] for key, value in parse_qs(url.query).items()}
            result = actions.execute(self.command, url.path, query, body, raw if binary else None)
            if result is None and url.path == '/api/_action/sync' and not url.query:
                result = self.deck.execute(self.command, url.path, self.headers, body)
            if result is None:
                raise ValueError('No matching learned action')
            self.respond(*result)
        except PermissionError as error:
            self.respond(401, {'error': str(error)})
        except (KeyError, ValueError, TypeError, IndexError) as error:
            self.respond(422, {'error': str(error)})
        return True

    def dispatch(self):
        url = urlsplit(self.path)
        if self.product_request(url):
            return
        if self.command == 'GET' and url.path in ('/', '/admin'):
            return self.respond(200, (HERE / 'surface.html').read_bytes(), 'text/html; charset=utf-8')
        if self.command == 'GET' and url.path == '/mask':
            try:
                query = parse_qs(url.query)
                return self.respond(200, self.deck.mask(query['tab'][0], query['product'][0]))
            except (KeyError, Unsupported) as error:
                return self.respond(404, {'error': str(error)})
        return super().dispatch()

    do_GET = do_POST = do_PATCH = dispatch


def main():
    cli = ArgumentParser(description=__doc__)
    for field in ('model', 'mask', 'catalog', 'database'):
        cli.add_argument(field, type=Path)
    cli.add_argument('--actions', type=Path)
    cli.add_argument('--action-products', type=Path)
    cli.add_argument('--port', type=int, default=55749)
    cli.add_argument('--teacher-port', type=int, default=55748)
    args = cli.parse_args()
    require_offline(args.teacher_port)
    mask = json.loads(gzip.decompress(args.mask.read_bytes())) if args.mask.suffix == '.gz' else read(args.mask)
    if bool(args.actions) != bool(args.action_products):
        cli.error('--actions and --action-products must be supplied together')
    products = read(args.action_products) if args.action_products else []
    catalog = read(args.catalog) + [item['record'] for item in products]
    deck = MaskDeck(read(args.model), catalog, args.database, mask,
                    read(args.actions) if args.actions else None, products)
    with HTTPServer(('127.0.0.1', args.port), partial(MaskHandler, deck=deck)) as server:
        print(f'http://127.0.0.1:{args.port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
