"""Capture separate native quantity labels from Cart API and Storefront markup."""
from argparse import ArgumentParser
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import uuid4

from capture_mixed import api


PROFILES = {
    'train': {
        'Kiesel': ((1, 9, 1, 4, True), (3, 11, 1, 2, True), (2, 8, 3, 3, False), (4, 12, 2, 17, False)),
        'Ufer': ((3, 8, 1, 11, True), (2, 13, 1, 7, True), (1, 10, 3, 0, False), (4, 15, 3, 5, False)),
        'Weide': ((1, 8, 1, 0, True), (2, 11, 1, 6, True), (3, 10, 2, 2, False), (1, 9, 2, 12, False)),
        'Kran': ((4, 12, 1, 5, True), (2, 7, 1, 3, True), (2, 12, 3, 16, False), (3, 13, 3, 9, False)),
    },
    'holdout': {
        'Nordufer': ((5, 19, 4, 12, True), (5, 19, 4, 12, False), (3, 21, 5, 2, True), (3, 21, 5, 2, False)),
        'Sandkai': ((6, 22, 5, 15, True), (6, 22, 5, 15, False), (2, 17, 4, 20, True), (2, 17, 4, 20, False)),
        'Feldwerk': ((4, 23, 4, 17, True), (4, 23, 4, 17, False), (6, 25, 5, 4, True), (6, 25, 5, 4, False)),
        'Kronensteg': ((5, 27, 5, 24, True), (5, 27, 5, 24, False), (6, 29, 4, 10, True), (6, 29, 4, 10, False)),
    },
}


class QuantityInput(HTMLParser):
    def __init__(self, identity):
        super().__init__()
        self.name = f'lineItems[{identity}][quantity]'
        self.input = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'input' and values.get('name') == self.name and values.get('type') == 'number':
            if self.input is not None:
                raise ValueError('Duplicate quantity control for one product')
            self.input = {key: int(values[key]) for key in ('min', 'max', 'step', 'value')}


def seed(oracle, rows, shop):
    products = []
    for i, (minimum, maximum, step, stock, closeout) in enumerate(rows):
        identity = uuid4().hex
        products.append({'id': identity, 'name': f'{shop} Paket {i+1}', 'stock': stock,
                         'minimum': minimum, 'maximum': maximum, 'step': step, 'closeout': closeout,
                         'unit': 1275 + i * 207, 'rate': 19})
    oracle.sync('product', [{'id': p['id'], 'name': p['name'], 'productNumber': 'QTY-' + p['id'][:14],
                              'active': True, 'isCloseout': p['closeout'], 'stock': p['stock'],
                              'minPurchase': p['minimum'], 'maxPurchase': p['maximum'],
                              'purchaseSteps': p['step'], 'taxId': oracle.tax[p['rate']],
                              'price': api.prices(oracle, p['rate'], p['unit']),
                              'visibilities': [{'salesChannelId': oracle.channel['id'], 'visibility': 30}]}
                             for p in products])
    return products


def cart_observations(oracle, product):
    lo, hi, step, stock = (product[key] for key in ('minimum', 'maximum', 'step', 'stock'))
    quantities = {1, 2, lo-1, lo, lo+1, lo+step-1, lo+step, lo+step+1,
                  hi-1, hi, hi+1, hi+step+1, stock-1, stock, stock+1}
    for quantity in sorted(quantities - {0, -1}):
        pid = product['id']
        line = {'id': pid, 'referencedId': pid, 'type': 'product', 'quantity': quantity,
                'stackable': True, 'removable': True}
        try:
            result = oracle.store({}, '/checkout/cart/line-item', {'items': [line]})
        except HTTPError as error:
            if error.code not in (400, 404, 409):
                raise
            yield {'request': quantity, 'quantity': 0, 'status': error.code}
        else:
            accepted = next((row for row in result['lineItems'] if row['referencedId'] == pid), None)
            yield {'request': quantity, 'quantity': accepted['quantity'] if accepted else 0, 'status': 200}


def page_control(url, product):
    with urlopen(url, timeout=40) as reply:
        text = reply.read().decode()
    if product['name'] not in text:
        raise ValueError(f'Expected the native product page at {url}')
    parser = QuantityInput(product['id'])
    parser.feed(text)
    return parser.input


def capture(phase, output, base):
    for shop, policies in PROFILES[phase].items():
        oracle = api.Oracle(575)
        domain = oracle.search('sales-channel-domain', {'limit': 1})[0]
        storefront = base + '/q-' + oracle.channel['id'][:10]
        oracle.sync('sales_channel_domain', [{'id': uuid4().hex, 'url': storefront,
              'salesChannelId': oracle.channel['id'], 'languageId': oracle.channel['languageId'],
              'currencyId': oracle.channel['currencyId'], 'snippetSetId': domain['snippetSetId']}])
        for product in seed(oracle, policies, shop):
            url = storefront + '/detail/' + product['id']
            row = {'split': phase, 'shop': shop, 'channel': oracle.channel['id'], 'product': product,
                   'cart': list(cart_observations(oracle, product)), 'storefront': page_control(url, product),
                   'storefront_path': url.removeprefix(base)}
            output.write(json.dumps(row) + '\n')
            output.flush()
        print(shop, phase, flush=True)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=PROFILES)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with args.output.open('x') as out:
        capture(args.phase, out, api.oracle_module.BASE)
