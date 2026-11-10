"""Capture independent sales channels with the existing Shopware API recorder."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from random import Random
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scale'))
from capture import Oracle, cart_snapshot, get_stock, observed_product, prices, promotion, relation, step


def episode(oracle, product, policy, index, rate, rng, challenge, composition=None):
    pid = product['id']
    initial = observed_product(oracle, pid)
    session = {}
    oracle.register(session)
    events = []

    def record(event, args, observed):
        events.append({'event': event, 'args': args, 'observed': observed})

    change = {'name': f"{product['name']} {index}", 'price': rng.randrange(950, 16000),
              'stock': rng.randrange(6, 18)}
    if composition:
        choices = (('name',), ('stock',), ('price',), ('name', 'price'), ('price', 'stock'))
        held_out = ('name', 'stock')
        fields = choices[index % len(choices)] if composition in ('train', 'dataflow_train') else held_out
        change = {key: change[key] for key in fields}
    patch = dict(change)
    if 'price' in patch:
        patch['price'] = prices(oracle, rate, patch['price'])
    oracle.admin('/product/' + pid, patch, 'PATCH')
    record('catalog.edit', change, observed_product(oracle, pid))

    def cart_price(response):
        snapshot = cart_snapshot(response)
        return {key: snapshot[key] for key in ('gross', 'goods', 'shipping', 'net', 'tax')}

    quantity = rng.randrange(1, 5)
    if (challenge and index % 4 == 0) or (composition and index < 4):
        quantity = change.get('stock', initial['stock']) + 2
    cart = oracle.store(session, '/checkout/cart/line-item', {'items': [
        {'id': pid, 'referencedId': pid, 'type': 'product', 'quantity': quantity,
         'stackable': True, 'removable': True}]})
    record('cart.add', {'quantity': quantity}, {
        'quantity': cart['lineItems'][0]['quantity'], **cart_price(cart)})
    code, configured = policy
    cart = oracle.store(session, '/checkout/cart/line-item', {'items': [
        {'type': 'promotion', 'referencedId': code}]})
    record('cart.promo', {'code': code}, {
        'discount': -sum(row['total'] for row in cart_snapshot(cart)['lines'] if row['type'] == 'promotion'),
        'accepted': any(row['type'] == 'promotion' for row in cart_snapshot(cart)['lines']),
        **cart_price(cart)})
    order = oracle.store(session, '/checkout/order', {})
    transaction = relation(oracle, order, 'order_transaction')
    delivery = relation(oracle, order, 'order_delivery')
    detail = oracle.order(order['id'])
    record('checkout.place', {}, {
        'gross': detail['totals'][2], 'net': detail['totals'][4],
        'payment': detail['payment'], 'delivery': detail['delivery'],
        'stage': detail['stage'], 'stock': get_stock(oracle, pid)})
    persisted_order = oracle.search('order', {'ids': [order['id']]})[0]
    entities = {'order_transaction': transaction, 'order_delivery': delivery, 'order': persisted_order}
    current = {name: row['stateId'] for name, row in entities.items()}
    actions = [('order_delivery', 'retour'), ('order_transaction', 'paid'),
               ('order_transaction', 'paid'), ('order', 'process'), ('order_delivery', 'ship'),
               ('order', 'complete'), ('order_delivery', 'retour_partially' if index % 2 else 'retour'),
               ('order_transaction', 'refund_partially' if index % 2 else 'refund')]
    if oracle.shipping in (985, 555) and index % 3 == 0:
        actions[3], actions[4] = actions[4], actions[3]
    if challenge:
        # New temporal compositions, including verbs absent from training.
        actions = [
            ('order', 'complete'), ('order_delivery', 'retour'),
            ('order_transaction', 'refund'), ('order_transaction', 'paid'),
            ('order_delivery', 'ship_partially'), ('order_delivery', 'reopen'),
            ('order', 'process'), ('order_delivery', 'ship'), ('order', 'complete'),
            ('order_delivery', 'retour_partially'), ('order_transaction', 'refund_partially'),
        ]
        if index % 2:
            actions[4:6] = [('order_delivery', 'ship'), ('order_delivery', 'retour')]
    if composition:
        if composition in ('train', 'dataflow_train'):
            actions = [('order', 'complete'), ('order_transaction', 'refund'), *actions[:4],
                       ('order_delivery', 'ship_partially'), ('order_delivery', 'reopen'), *actions[4:]]
        else:
            actions = [actions[3], *actions[:3], ('order_delivery', 'ship_partially'), actions[4],
                       ('order_transaction', 'paid'), *actions[5:]]
        if composition == 'dataflow':
            actions = [*actions[:1], ('order', 'cancel'), ('order', 'reopen'), *actions[1:]]
        if composition == 'dataflow_train':
            preparation = [('order', 'process')] if index % 2 else []
            actions = [*preparation, ('order', 'cancel'), ('order', 'reopen'), *actions]
    for entity, verb in actions:
        result, current[entity] = step(oracle, entity, entities[entity]['id'], current[entity], verb)
        # Independently read all durable states, including effects outside the requested entity.
        after = oracle.order(order['id'])
        record(result['event'], {}, {**{key: after[key] for key in ('payment', 'delivery', 'stage')},
                                     **({'stock': get_stock(oracle, pid)} if composition and
                                        composition.startswith('dataflow') else {}),
                                     'accepted': result['status'] == 200})
    return {'product': product['productNumber'], 'initial': initial, 'policy': configured, 'events': events}


def channel(spec):
    name, split, rate, shipping, pct, fixed, count, challenge, composition = spec
    oracle = Oracle(shipping)
    oracle.admin('/sales-channel/' + oracle.channel['id'], {'name': name}, 'PATCH')
    source = oracle.search('sales-channel', {'ids': [oracle.channel['id']]})[0]
    seed = uuid4().hex[:8].upper()
    policies = []
    for mode, value, active in [('percentage', pct, True), ('absolute', fixed, True),
                                ('percentage', pct, False)]:
        code = f'{seed}-{len(policies)}'
        promotion(oracle, code, mode, value, active=active)
        rule = oracle.search('promotion', {'filter': [{'type': 'equals', 'field': 'code', 'value': code}],
                                           'associations': {'discounts': {}}})[0]
        amount = rule['discounts'][0]['value']
        policies.append((code, {'active': rule['active'], 'mode': rule['discounts'][0]['type'],
                                'value': int(amount) if mode == 'percentage' else round(amount * 100)}))
    policies.append((seed + '-unknown', {}))
    rng = Random(shipping)
    rows = []
    for index in range(count):
        pid = uuid4().hex
        product = {'id': pid, 'productNumber': f'{seed}-{index}', 'name': f'{name} Artikel',
                   'active': True, 'isCloseout': True, 'stock': 10, 'taxId': oracle.tax[rate],
                   'price': prices(oracle, rate, rng.randrange(1500, 13000)),
                   'visibilities': [{'salesChannelId': source['id'], 'visibility': 30}]}
        oracle.sync('product', [product])
        row = episode(oracle, product, policies[index % len(policies)], index, rate, rng, challenge, composition)
        rows.append({**row, 'shop': source['name'], 'channel': source['id'], 'split': split,
                     'rate': rate, 'shipping': oracle.shipping, 'episode': index})
    print(f'{split} {source["name"]}: {len(rows)} complete Shopware journeys', flush=True)
    return rows


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--count', type=int, default=12)
    parser.add_argument('--challenge', action='store_true')
    parser.add_argument('--composition', action='store_true')
    parser.add_argument('--composition-confirmation', action='store_true')
    parser.add_argument('--dataflow', action='store_true')
    parser.add_argument('--dataflow-confirmation', action='store_true')
    args = parser.parse_args()
    shops = [('Papierhafen', 'train', 19, 425, 11, 420),
             ('Grünfaden', 'train', 7, 610, 16, 780),
             ('Hirschwerk', 'train', 19, 690, 24, 1480),
             ('Buchinsel', 'train', 7, 350, 8, 350),
             ('Lichtlager', 'holdout', 19, 985, 23, 675),
             ('Teekontor', 'holdout', 7, 555, 29, 1195)]
    if args.challenge:
        shops = [('Steinbogen', 'holdout', 7, 275, 34, 850),
                 ('Kabelkontor', 'holdout', 19, 1160, 14, 990)]
    if args.composition:
        shops = [('Morgenfabrik', 'train', 19, 470, 18, 625),
                 ('Feldregal', 'train', 7, 910, 27, 1425),
                 ('Wollspeicher', 'holdout', 19, 1080, 32, 875),
                 ('Atlaswerk', 'holdout', 7, 305, 12, 535)]
    if args.composition_confirmation:
        shops = [('Bernsteinhandel', 'holdout', 7, 745, 14, 1135),
                 ('Stadtgarten', 'holdout', 19, 1235, 28, 960)]
    if args.dataflow:
        shops = [('Werkstattinsel', 'train', 19, 565, 16, 1150),
                 ('Moorhaus', 'train', 7, 810, 26, 470),
                 ('Riedmarkt', 'holdout', 7, 1315, 21, 1775),
                 ('Nordlichtwaren', 'holdout', 19, 785, 37, 1035)]
    if args.dataflow_confirmation:
        shops = [('Werftkontor', 'holdout', 19, 660, 28, 1565),
                 ('Moosladen', 'holdout', 7, 465, 33, 895)]
    with ThreadPoolExecutor(max_workers=4) as pool, args.output.open('w') as output:
        for batch in pool.map(channel, [
            (*shop, args.count, args.challenge,
             ('dataflow_train' if shop[1] == 'train' else 'dataflow') if args.dataflow or args.dataflow_confirmation
             else shop[1] if args.composition or args.composition_confirmation else None)
            for shop in shops
        ]):
            output.writelines(json.dumps(row, ensure_ascii=False) + '\n' for row in batch)
            output.flush()


if __name__ == '__main__':
    main()
