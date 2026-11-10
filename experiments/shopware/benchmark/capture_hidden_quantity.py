"""Probe masked products in Shopware, freeze policies, then record unseen stock states."""
from argparse import ArgumentParser
import json
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError
from uuid import uuid4

from capture_quantity import api, page_control, seed
from hidden_quantity import FIELDS, STRATEGIES, acquire, refit, refit_expanded, resolve, rows, write_checkpoint


PROFILES = {
    'Muehlenbogen': ((3, 29, 4, 15, True), (4, 25, 7, 15, False),
                    (6, 33, 5, 15, True), (2, 7, 3, 15, True)),
    'Frachtkai': ((5, 38, 6, 15, False), (7, 35, 4, 15, True),
                  (2, 42, 8, 15, True), (3, 8, 4, 15, False)),
    'Kupfersteg': ((4, 39, 3, 15, True), (1, 36, 5, 15, False),
                   (9, 9, 7, 15, True), (1, 9, 4, 15, True)),
    'Westmole': ((6, 40, 7, 15, True), (5, 45, 9, 15, False),
                 (8, 37, 6, 15, True), (4, 8, 2, 15, False)),
}
TEST_STOCKS = (5, 23)
TEST_REQUESTS = (1, 3, 6, 9, 13, 18, 27, 41, 64, 65)


def patch_stock(oracle, identity, stock):
    oracle.admin('/product/' + identity, {'stock': stock}, 'PATCH')


def probe(oracle, identity, quantity):
    line = {'id': identity, 'referencedId': identity, 'type': 'product', 'quantity': quantity,
            'stackable': True, 'removable': True}
    try:
        result = oracle.store({}, '/checkout/cart/line-item', {'items': [line]})
    except HTTPError as error:
        if error.code not in (400, 404, 409):
            raise
        return 0, error.code
    accepted = next((row for row in result['lineItems'] if row['referencedId'] == identity), None)
    return (accepted['quantity'] if accepted else 0), 200


def masked(product, stock):
    return {**{key: value for key, value in product.items() if key in FIELDS}, 'stock': stock}


def support_row(oracle, shop, product, planes):
    stock = product['stock']
    identity = product['id']

    def observe(requested_stock, quantity):
        nonlocal stock
        if requested_stock != stock:
            patch_stock(oracle, identity, requested_stock)
            stock = requested_stock
        return probe(oracle, identity, quantity)[0]

    learning = {strategy: acquire(strategy, planes, observe) for strategy in STRATEGIES}
    return {'shop': shop, 'channel': oracle.channel['id'], 'product': masked(product, product['stock']),
            'support_stocks': list(planes), 'learning': learning}


def capture(output):
    output.mkdir(parents=True, exist_ok=False)
    base = api.oracle_module.BASE
    batches = []
    support = output / 'support.jsonl'
    with support.open('x') as out:
        for shop, settings in PROFILES.items():
            oracle = api.Oracle(575)
            domain = oracle.search('sales-channel-domain', {'limit': 1})[0]
            storefront = base + '/hidden-' + oracle.channel['id'][:10]
            oracle.sync('sales_channel_domain', [{'id': uuid4().hex, 'url': storefront,
                  'salesChannelId': oracle.channel['id'], 'languageId': oracle.channel['languageId'],
                  'currencyId': oracle.channel['currencyId'], 'snippetSetId': domain['snippetSetId']}])
            products = seed(oracle, settings, shop)
            for index, product in enumerate(products):
                planes = (9 if index == len(products) - 1 else 0, 64)
                out.write(json.dumps(support_row(oracle, shop, product, planes)) + '\n')
                out.flush()
            batches.append((shop, oracle, storefront, [masked(product, product['stock']) for product in products]))
            print(shop, 'support', flush=True)
    for strategy in STRATEGIES:
        write_checkpoint(output / (strategy + '.json'), refit(rows(support), strategy))
    print('Froze all three policies before requesting unseen stock states', flush=True)
    with (output / 'holdout.jsonl').open('x') as out:
        for shop, oracle, storefront, products in batches:
            for stock in TEST_STOCKS:
                for product in products:
                    identity = product['id']
                    patch_stock(oracle, identity, stock)
                    url = storefront + '/detail/' + identity
                    row = observation(oracle, shop, product, stock, url, base, TEST_REQUESTS)
                    out.write(json.dumps(row) + '\n')
                    out.flush()
                print(shop, 'unseen stock', stock, flush=True)


def observation(oracle, shop, product, stock, url, base, requests):
    cart = []
    for request in requests:
        quantity, status = probe(oracle, product['id'], request)
        cart.append({'request': request, 'quantity': quantity, 'status': status})
    return {'split': 'holdout', 'shop': shop, 'channel': oracle.channel['id'],
            'product': masked(product, stock), 'cart': cart,
            'storefront': page_control(url, product), 'storefront_path': url.removeprefix(base)}


def bind_channel(channel):
    oracle = api.Oracle.__new__(api.Oracle)
    oracle.auth_lock = Lock()
    oracle.headers = oracle.authenticate()
    oracle.channel = oracle.search('sales-channel', {'ids': [channel]})[0]
    return oracle


def collect_additional(output):
    base = api.oracle_module.BASE
    support = rows(output / 'support.jsonl')
    channels = {}
    recheck = []
    additional = output / 'additional.jsonl'
    with additional.open('x') as out:
        for row in support:
            if row['learning']['analytic']['policy'] is not None:
                continue
            channel = row['channel']
            if channel not in channels:
                channels[channel] = bind_channel(channel)
            oracle = channels[channel]
            product = row['product']
            identity = product['id']

            def observe(stock, quantity):
                patch_stock(oracle, identity, stock)
                return probe(oracle, identity, quantity)[0]

            result = resolve(row, observe)
            out.write(json.dumps({'product_id': identity, 'result': result}) + '\n')
            recheck.append((row, oracle))
    write_checkpoint(output / 'resolved.json', refit_expanded(support, rows(additional)))
    print('Froze the expanded policy before the second test stocks', flush=True)
    with (output / 'resolution-holdout.jsonl').open('x') as out:
        for row, oracle in recheck:
            channel, product = row['channel'], row['product']
            url = base + '/hidden-' + channel[:10] + '/detail/' + product['id']
            for stock in (3, 11):
                patch_stock(oracle, product['id'], stock)
                observed = observation(oracle, row['shop'], product, stock, url, base, (1, 3, 5, 8, 12))
                out.write(json.dumps(observed) + '\n')
                out.flush()


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--resolve', action='store_true')
    args = parser.parse_args()
    (collect_additional if args.resolve else capture)(args.output)
