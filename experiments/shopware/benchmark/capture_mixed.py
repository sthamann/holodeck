"""Capture native Shopware baskets with multiple SKUs, rates and rounding effects."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from random import Random
from uuid import uuid4

from capture_flows import COMMANDS, api, install, status


def taxes(rows):
    return sorted([int(item['taxRate']), api.cents(item['price']), api.cents(item['tax'])]
                  for item in rows)


def price(data):
    return {'gross': api.cents(data['totalPrice']), 'taxes': taxes(data['calculatedTaxes'])}


def quote(cart):
    items = {}
    discount = None
    for line in cart['lineItems']:
        if line['type'] == 'product':
            items[line['referencedId']] = {'quantity': line['quantity'], **price(line['price'])}
        elif line['type'] == 'promotion':
            if discount is not None:
                raise ValueError('Only one coupon per case is supported')
            discount = price(line['price'])
    shipping = [price(delivery['shippingCosts']) for delivery in cart['deliveries']]
    if len(shipping) != 1:
        raise ValueError('Expected one native delivery')
    total = cart['price']
    return {'items': items, 'discount': discount, 'shipping': shipping[0],
            'goods': api.cents(total['positionPrice']), 'gross': api.cents(total['totalPrice']),
            'net': api.cents(total['netPrice']), 'taxes': taxes(total['calculatedTaxes'])}


def seed_catalog(oracle, shop, i, count, rng, rates, basket=None):
    fixture, payload = [], []
    bands = [*rates[:count], *(rng.choice(rates) for _ in range(max(0, count - len(rates))))]
    rng.shuffle(bands)
    if basket is not None:
        bands = [entry[1] for entry in basket]
    for j, rate in enumerate(bands):
        product = uuid4().hex
        unit = rng.randrange(179, 10900) if basket is None else basket[j][0]
        stock = rng.randrange(3, 16) if basket is None else basket[j][3]
        fixture.append({'id': product, 'unit': unit, 'rate': rate, 'stock': stock})
        payload.append({'id': product, 'name': f'{shop} {i}/{j}', 'productNumber': 'MIX-' + product[:12],
                        'active': True, 'isCloseout': True, 'stock': stock, 'taxId': oracle.tax[rate],
                        'price': api.prices(oracle, rate, unit),
                        'visibilities': [{'salesChannelId': oracle.channel['id'], 'visibility': 30}]})
    oracle.sync('product', payload)
    return fixture


def sample(oracle, shop, split, i, coupon, tag_ids, rng, rates, observe=None, basket=None):
    count = rng.choice((2, 3) if split == 'train' else (4, 5, 6))
    catalog = seed_catalog(oracle, shop, i, count, rng, rates, basket=basket)
    items = [{'id': p['id'], 'quantity': p['stock'] + 2 if j == 0 and i % 3 == 0
              else rng.randrange(1, min(6, p['stock'] + 1))} for j, p in enumerate(catalog)]
    if basket is not None:
        items = [{'id': product['id'], 'quantity': entry[2]} for product, entry in zip(catalog, basket, strict=True)]
    if len(rates) > 2:
        catalog += seed_catalog(oracle, shop, i + 10000, 1, rng, rates)
    session = {}
    oracle.register(session)
    request = [{'id': x['id'], 'referencedId': x['id'], 'type': 'product',
                'quantity': x['quantity'], 'stackable': True, 'removable': True} for x in items]
    before = quote(oracle.store(session, '/checkout/cart/line-item', {'items': request}))
    mode, amount, code = coupon
    after = (quote(oracle.store(session, '/checkout/cart/line-item', {'items': [
        {'type': 'promotion', 'referencedId': code}]})) if mode != 'none' else before)
    order = oracle.store(session, '/checkout/order', {})['id']
    details = oracle.order(order)
    raw_order = oracle.search('order', {'ids': [order]})[0]
    lines = oracle.search('order-line-item', {'filter': [
        {'type': 'equals', 'field': 'orderId', 'value': order}], 'limit': 40})
    native_order_lines = {x['productId']: {'quantity': x['quantity'], **price(x['price'])}
                          for x in lines if x['type'] == 'product'}
    stocks = {p['id']: stock for p, stock in zip(catalog, oracle.stocks(catalog), strict=True)}
    persisted = {'gross': details['totals'][2], 'net': details['totals'][4],
                 'taxes': taxes(raw_order['price']['calculatedTaxes']),
                 'lines': native_order_lines, 'stock': stocks,
                 **{key: details[key] for key in ('payment', 'delivery', 'stage')}}
    events = []
    if observe:
        events = observe(oracle, order, details['ids'], tag_ids, catalog, i)
    elif split == 'holdout':
        ids = details['ids']
        state_ids = {kind: oracle.search(kind.replace('_', '-'), {'ids': [oid]})[0]['stateId']
                     for kind, oid in ids.items()}
        for letter in ('pPs' if i % 2 else 'sSp'):
            kind, verb, field = COMMANDS[letter]
            response, state_ids[kind] = api.step(oracle, kind, ids[kind], state_ids[kind], verb)
            current, tags = status(oracle, order, tag_ids)
            events.append({'command': response['event'], 'state': current,
                           'event': f'{field}.{current[field]}', 'markers': tags})
    rounding = ({'item': raw_order['itemRounding'], 'total': raw_order['totalRounding'],
                 'currency': raw_order['currencyId']} if basket is not None else {})
    return {'shop': shop, 'channel': oracle.channel['id'], 'split': split, 'case': i, **rounding,
            'catalog': catalog, 'cart_input': items, 'config': {'shipping': oracle.shipping,
            'mode': mode, 'discount_value': amount}, 'before': before, 'after': after,
            'order': persisted, 'events': events}


def channel(profile, tags, count):
    shop, split, shipping, percentage, absolute, rates = profile
    oracle = api.Oracle(shipping)
    oracle.admin('/sales-channel/' + oracle.channel['id'], {'name': shop}, 'PATCH')
    for rate in rates:
        if rate not in oracle.tax:
            identity = uuid4().hex
            oracle.sync('tax', [{'id': identity, 'name': f'{shop} Teststeuer {rate}', 'taxRate': rate}])
            oracle.tax[rate] = identity
    prefix = 'MIX-' + uuid4().hex[:7]
    modes = [('none', 0, None), ('percentage', percentage, prefix + 'P'),
             ('absolute', absolute, prefix + 'A')]
    for mode, amount, code in modes[1:]:
        api.promotion(oracle, code, mode, amount)
    rng = Random(shipping)
    rows = [sample(oracle, shop, split, i, modes[i % len(modes)], tags, rng, rates) for i in range(count)]
    print(shop, split, len(rows), 'native mixed baskets and orders', flush=True)
    return rows


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('output', type=Path)
    cli.add_argument('--count', type=int, default=18)
    cli.add_argument('--new-rates', action='store_true')
    args = cli.parse_args()
    profiles = [('Mischlabor', 'train', 495, 11, 333, (7, 19)),
                ('Eichenlager', 'train', 920, 24, 1865, (7, 19)),
                ('Kieselmarkt', 'holdout', 685, 23, 1265, (7, 19)),
                ('Morgenkontor', 'holdout', 375, 31, 947, (7, 19))]
    if args.new_rates:
        profiles = [('Uferkauf', 'holdout', 575, 18, 1225, (5, 7, 19)),
                    ('Heidewerk', 'holdout', 890, 28, 855, (7, 13, 19))]
    tags = install(api.Oracle(500))
    with ThreadPoolExecutor(max_workers=4) as pool, args.output.open('w') as target:
        for rows in pool.map(lambda profile: channel(profile, tags, args.count), profiles):
            target.writelines(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
            target.flush()


if __name__ == '__main__':
    main()
