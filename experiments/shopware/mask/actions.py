"""Fit Admin API request and effect graphs, then execute them through the same FX lowerer."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'absorption'))
from learn import induce, read


def encoded(request):
    return {**request, 'path': request['path'].split('/')}


def fit(episodes):
    if len(episodes) < 3 or any(row['split'] != 'train' for row in episodes):
        raise ValueError('Expected three or more native training products')
    names = list(dict.fromkeys(step['action'] for row in episodes for step in row['steps']))
    actions = {}
    for name in names:
        rows = [step for row in episodes for step in row['steps'] if step['action'] == name]
        envs = [{'input': row['inputs']} for row in rows]
        actions[name] = {
            'request': induce(envs, [encoded(row['request']) for row in rows]),
            'effect': induce(envs, [row['observed'] for row in rows]),
            'status': induce(envs, [row['status'] for row in rows]),
            'examples': len(rows),
        }
    return {'channels': sorted({row['channel'] for row in episodes}), 'actions': actions}


def collect_inputs(graph, value, inputs):
    op, *args = graph
    if op == 'ref':
        if len(args) != 2 or args[0] != 'input':
            raise ValueError('Unexpected request operand')
        inputs[args[1]] = value
    elif op == 'record':
        for key, child in args[0].items():
            collect_inputs(child, value[key], inputs)
    elif op == 'vector':
        for child, item in zip(args, value, strict=True):
            collect_inputs(child, item, inputs)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


class Actions:
    def __init__(self, db, checkpoint, products):
        from forward_vm import Lowering

        self.db = db
        self.profiles = checkpoint['actions']
        self.forwards = {name: {key: Lowering().compile(profile[key]).forward
                                for key in ('request', 'effect', 'status')}
                         for name, profile in self.profiles.items()}
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS action_records '
                       '(collection TEXT, id TEXT, document TEXT NOT NULL, blob BLOB, PRIMARY KEY(collection, id))')
            for product in products:
                identity = product['record']['id']
                if self.get('product', identity) is None:
                    self.store('product', identity, {key: product[key] for key in ('options', 'price', 'version')})

    def store(self, collection, identity, values, blob=None):
        original = self.get(collection, identity)
        merged = {**(original or {}), **values}
        self.db.execute('INSERT INTO action_records VALUES (?, ?, ?, ?) '
                        'ON CONFLICT(collection,id) DO UPDATE SET document=excluded.document, '
                        'blob=coalesce(excluded.blob,action_records.blob)',
                        (collection, identity, json.dumps(merged), blob))

    def get(self, collection, identity):
        row = self.db.execute('SELECT document FROM action_records WHERE collection=? AND id=?',
                              (collection, identity)).fetchone()
        return json.loads(row[0]) if row else None

    def picture(self, identity):
        row = self.db.execute('SELECT blob FROM action_records WHERE collection=? AND id=?',
                              ('media', identity)).fetchone()
        return row[0] if row else None

    def product(self, identity):
        result = self.get('product', identity)
        if result is None:
            return None
        related = {'variant': ('parentId', 'variants'), 'product_media': ('productId', 'media')}
        for collection, (foreign_key, output) in related.items():
            rows = self.db.execute('SELECT id,document FROM action_records WHERE collection=? '
                                   'AND json_extract(document, ?) = ? ORDER BY id',
                                   (collection, '$.' + foreign_key, identity))
            result[output] = [{'id': key, **json.loads(doc)} for key, doc in rows]
        result['media'].sort(key=lambda item: (item['position'], item['id']))
        for row in result['media']:
            row['file'] = self.get('media', row['mediaId'])
        return result

    def request_templates(self):
        # The browser substitutes user inputs into the induced transport shape.
        return {name: profile['request'] for name, profile in self.profiles.items()}

    def execute(self, method, path, query, body, blob=None):
        envelope = encoded({'method': method, 'path': path, 'query': query, 'body': body})
        for name, profile in self.profiles.items():
            inputs = {}
            try:
                collect_inputs(profile['request'], envelope, inputs)
                if blob is not None:
                    inputs['size'] = len(blob)
                env = {'input': inputs}
                graph = self.forwards[name]
                if canonical(graph['request'](env)) != canonical(envelope):
                    continue
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            effect = graph['effect'](env)
            self.validate(effect, blob)
            with self.db:
                self.store(effect['collection'], effect['id'], effect['values'], blob)
            return graph['status'](env), {'data': [effect['id']]}
        return None

    def validate(self, effect, blob):
        collection, identity, values = (effect[key] for key in ('collection', 'id', 'values'))
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity):
            raise ValueError('Expected a record identifier')
        for key, target in (('parentId', 'product'), ('productId', 'product'), ('mediaId', 'media'),
                            ('coverId', 'product_media')):
            if key in values and self.get(target, values[key]) is None:
                raise ValueError(f'Unknown {key}')
        if collection == 'product':
            product = self.get(collection, identity)
            if product is None:
                raise ValueError('Unknown product')
            if 'coverId' in values and self.get('product_media', values['coverId'])['productId'] != identity:
                raise ValueError('Cover must belong to this product')
            for price in values.get('price', []):
                if price['currencyId'] not in {item['currencyId'] for item in product['price']}:
                    raise ValueError('Unknown price currency')
                for key in ('gross', 'net'):
                    if type(price[key]) not in (float, int) or not 0 <= price[key] <= 1_000_000:
                        raise ValueError('Invalid price')
        if collection in ('configurator', 'variant'):
            parent = values.get('parentId') or values['productId']
            options = values.get('optionIds') or [values['optionId']]
            if not set(options) <= {item['id'] for item in self.get('product', parent)['options']}:
                raise ValueError('Unknown product option')
        if collection == 'variant':
            if type(values['stock']) is not int or values['stock'] < 0:
                raise ValueError('Invalid stock')
            if not isinstance(values['productNumber'], str) or not 1 <= len(values['productNumber']) <= 64:
                raise ValueError('Invalid product number')
        if blob is not None and (self.get(collection, identity) is None or
                                 not blob.startswith(b'\xff\xd8\xff') or not blob.endswith(b'\xff\xd9')):
            raise ValueError('Expected a JPEG upload for a known media record')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('traces', type=Path)
    parser.add_argument('checkpoint', type=Path)
    args = parser.parse_args()
    model = fit(read(args.traces))
    model['sha256'] = sha256(args.traces.read_bytes()).hexdigest()
    args.checkpoint.write_text(json.dumps(model, indent=2) + '\n')
    print(f'{len(model["actions"])} action profiles fit from native Shopware readbacks')


if __name__ == '__main__':
    main()
