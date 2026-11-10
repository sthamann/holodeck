"""Capture native Rule Builder behavior without exporting its definitions to the learner."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
from random import Random
from uuid import uuid4

source = Path(__file__).resolve().parents[1] / 'scale' / 'capture.py'
spec = importlib.util.spec_from_file_location('shopware_learning_oracle', source)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def seed_rule(oracle, code, logic, mode, value, goods, quantity):
    root = uuid4().hex
    rule = uuid4().hex
    conditions = [
        {'id': root, 'type': logic + 'Container', 'position': 0},
        {'id': uuid4().hex, 'parentId': root, 'position': 0, 'type': 'cartGoodsPrice',
         'value': {'operator': '>=', 'amount': goods / 100}},
        {'id': uuid4().hex, 'parentId': root, 'position': 1, 'type': 'cartLineItemGoodsTotal',
         'value': {'operator': '>=', 'count': quantity}},
    ]
    oracle.sync('rule', [{'id': rule, 'name': code, 'priority': 1, 'conditions': conditions}])
    api.promotion(oracle, code, mode, value)
    record = oracle.search('promotion', {'filter': [{'type': 'equals', 'field': 'code', 'value': code}]})[0]
    oracle.sync('promotion', [{'id': record['id'], 'cartRules': [{'id': rule}]}])
    configured = oracle.search('promotion', {'ids': [record['id']], 'associations': {'cartRules': {}}})[0]
    if not any(row['id'] == rule for row in configured['cartRules']):
        raise ValueError('Native promotion rule binding is missing')


def change_shipping(oracle, cents):
    method = oracle.search('shipping-method', {'ids': [oracle.channel['shippingMethodId']],
                                               'associations': {'prices': {}}})[0]
    row = method['prices'][0]
    oracle.sync('shipping_method_price', [{'id': row['id'], 'currencyPrice': [
        {'currencyId': oracle.channel['currencyId'], 'gross': cents / 100,
         'net': cents / 100, 'linked': False}]}])


def seed_items(oracle, shop, goods, quantity, rate, multi):
    price, remainder = divmod(goods, quantity)
    parts = [(quantity, price)] if not multi and not remainder else [
        (quantity - 1, price), (1, price + remainder)]
    products = []
    for count, amount in parts:
        if not count:
            continue
        pid = uuid4().hex
        number = 'POLICY-' + pid[:14]
        label = f'{shop} Artikel {pid[:5]}'
        stock = count + 4
        oracle.sync('product', [{'id': pid, 'productNumber': number, 'name': label,
            'active': True, 'isCloseout': True, 'stock': stock, 'taxId': oracle.tax[rate],
            'price': api.prices(oracle, rate, amount),
            'visibilities': [{'salesChannelId': oracle.channel['id'], 'visibility': 30}]}])
        saved = api.observed_product(oracle, pid)
        products.append((pid, count, saved))
    return products


def observation(oracle, products, code, placed, expected_goods, expected_quantity, shipping):
    session = {}
    oracle.register(session)
    items = [{'id': pid, 'referencedId': pid, 'type': 'product', 'quantity': count,
              'stackable': True, 'removable': True} for pid, count, _ in products]
    before = api.cart_snapshot(oracle.store(session, '/checkout/cart/line-item', {'items': items}))
    quantity = sum(item['quantity'] for item in before['lines'] if item['type'] == 'product')
    if (before['goods'], quantity, before['shipping']) != (expected_goods, expected_quantity, shipping):
        raise ValueError(f'Native fixture differs: {before}, expected {expected_goods, expected_quantity, shipping}')
    after = api.cart_snapshot(oracle.store(session, '/checkout/cart/line-item', {
        'items': [{'type': 'promotion', 'referencedId': code}]}))
    line = next((item for item in after['lines'] if item['type'] == 'promotion'), None)
    facts = {f'cart.{key}': before[key] for key in ('goods', 'gross', 'tax', 'net', 'shipping')}
    facts['cart.quantity'] = quantity
    result = {f'cart.{key}': after[key] for key in ('goods', 'gross', 'tax', 'net', 'shipping')}
    result.update({'cart.accepted': line is not None, 'cart.discount': -line['total'] if line else 0})
    order = None
    if placed:
        receipt = oracle.store(session, '/checkout/order', {})
        detail = oracle.order(receipt['id'])
        order = {'order.gross': detail['totals'][2], 'order.net': detail['totals'][4],
                 'order.stage': detail['stage'], 'order.payment': detail['payment'],
                 'order.delivery': detail['delivery'], 'product.stock': api.get_stock(oracle, products[0][0])}
    return facts, result, order


def exercise(task):
    shop, split, mode, value, fixed_shipping, seed = task
    goods, quantity = 12000, 4
    oracle = api.Oracle(fixed_shipping)
    oracle.admin('/sales-channel/' + oracle.channel['id'], {'name': shop}, 'PATCH')
    prefix = 'HDL-' + uuid4().hex[:8]
    codes = {logic: prefix + logic for logic in ('and', 'or')}
    for logic, code in codes.items():
        seed_rule(oracle, code, logic, mode, value, goods, quantity)
    if split == 'train':
        # Independent boundary interventions. The learner never receives these
        # target constants, rule names or combinators as labels.
        cases = [
            (12000, 4, 235, 19), (11999, 4, 1125, 7),
            (12000, 3, 275, 19), (11999, 3, 1440, 7),
            (12000, 3, 865, 7), (11999, 3, 315, 19),
            (11999, 4, 1500, 19), (9150, 5, 1200, 7),
            (15000, 6, 1025, 19), (8600, 2, 290, 7),
        ]
    else:
        rng = Random(seed)
        cases = [(g, q, fixed_shipping, rate) for g, q, rate in [
            (11999, 4, 7), (12000, 4, 19), (12001, 4, 19), (11999, 3, 19),
            (12000, 3, 7), (12001, 3, 7), (13000, 2, 19), (11000, 7, 7),
            (21500, 5, 7), (16000, 2, 19), (12000, 6, 7), (11996, 4, 19)]]
        cases += [(rng.randrange(7200, 18900), rng.randrange(2, 8), fixed_shipping,
                   rng.choice((7, 19))) for _ in range(6)]
    rows = []
    for i, (amount, count, shipping, rate) in enumerate(cases):
        if split == 'train':
            change_shipping(oracle, shipping)
        for logic, code in codes.items():
            placed = split == 'holdout' and i in (1, 4, 9, 11)
            products = seed_items(oracle, shop, amount, count, rate, multi=not placed)
            facts, result, order = observation(oracle, products, code, placed, amount, count, shipping)
            rows.append({'shop': shop, 'channel': oracle.channel['id'], 'case': i, 'split': split,
                         'policy': logic, 'facts': facts, 'observed': result, 'order': order,
                         'merchant': {'shipping': shipping, 'rate': rate, 'coupon.mode': mode,
                                      'coupon.value': value, 'coupon.active': True},
                         'initial': products[0][2] if placed else None, 'code': code})
    print(shop, split, len(rows), 'native policy observations', flush=True)
    return rows


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('output', type=Path)
    cli.add_argument('--pilot', action='store_true')
    args = cli.parse_args()
    profiles = [('Lernwerft', 'train', 'percentage', 17, 235, 11),
                ('Glasatelier', 'holdout', 'percentage', 23, 795, 17),
                ('Waldkasse', 'holdout', 'absolute', 1265, 455, 19)]
    with ThreadPoolExecutor(max_workers=3) as pool, args.output.open('w') as output:
        for rows in pool.map(exercise, profiles[:1] if args.pilot else profiles):
            output.writelines(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
            output.flush()


if __name__ == '__main__':
    main()
