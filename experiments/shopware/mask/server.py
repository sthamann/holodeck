"""Serve a compiled product mask and the installed Shopware API experts."""
from argparse import ArgumentParser
from functools import partial
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


class MaskDeck(Deck):
    def __init__(self, model, catalog, db, mask):
        super().__init__(model, catalog, db)
        self.tabs = {tab['key']: tab for tab in mask['tabs']}
        self.slots = Lowering().compile(['record', {key: ['ref', 'record', key]
                                                   for key in ('name', 'stock', 'productNumber', 'id')}]).forward

    def mask(self, key, identity):
        if key not in self.tabs:
            raise Unsupported('This tab has no learned component tree')
        slots = self.slots({'record': self.record(identity)})
        action = self.project('/admin#/sw/product/detail/' + identity + '/base')['action']
        return {'tab': self.tabs[key], 'slots': slots, 'action': action, 'tabs': list(self.tabs)}


class MaskHandler(Handler):
    def dispatch(self):
        url = urlsplit(self.path)
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
    cli.add_argument('--port', type=int, default=55749)
    cli.add_argument('--teacher-port', type=int, default=55748)
    args = cli.parse_args()
    require_offline(args.teacher_port)
    mask = json.loads(gzip.decompress(args.mask.read_bytes())) if args.mask.suffix == '.gz' else read(args.mask)
    deck = MaskDeck(read(args.model), read(args.catalog), args.database, mask)
    with HTTPServer(('127.0.0.1', args.port), partial(MaskHandler, deck=deck)) as server:
        print(f'http://127.0.0.1:{args.port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
