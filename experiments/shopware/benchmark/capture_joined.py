"""Record a full native commerce journey and its native Flow Builder effects."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from random import Random
from uuid import uuid4

from capture_flows import COMMANDS, api, install, status


CHAINS = ('xpPsSp', 'xsSpPs', 'pcSxsPp', 'sfxPpSs', 'xs', 'xp', 'pPxs', 'sSxp')


def quote(response):
    values = api.cart_snapshot(response)
    return {key: values[key] for key in ('goods', 'gross', 'shipping', 'net', 'tax')}


def episode(oracle, shop, rate, code, mode, amount, tags, chain, number, rng):
    events = []
    pid = uuid4().hex
    original = {'name': f'{shop} Produkt {number}', 'price': rng.randrange(2450, 13800), 'stock': 12}
    oracle.sync('product', [{'id': pid, 'productNumber': 'JOIN-' + pid[:12],
        'name': original['name'], 'active': True, 'isCloseout': True, 'stock': original['stock'],
        'taxId': oracle.tax[rate], 'price': api.prices(oracle, rate, original['price']),
        'visibilities': [{'salesChannelId': oracle.channel['id'], 'visibility': 30}]}])
    initial = api.observed_product(oracle, pid)
    change = {'name': f'{shop} Angebot {number}', 'stock': rng.randrange(6, 14)}
    oracle.admin('/product/' + pid, change, 'PATCH')
    edited = api.observed_product(oracle, pid)
    events.append({'event': 'catalog.edit', 'args': change, 'observed': edited})
    session = {}
    oracle.register(session)
    quantity = change['stock'] + 2 if number % 3 == 0 else rng.randrange(1, 5)
    cart = oracle.store(session, '/checkout/cart/line-item', {'items': [
        {'id': pid, 'referencedId': pid, 'type': 'product', 'quantity': quantity,
         'stackable': True, 'removable': True}]})
    events.append({'event': 'cart.add', 'args': {'quantity': quantity},
                   'observed': {'quantity': cart['lineItems'][0]['quantity'], **quote(cart)}})
    cart = oracle.store(session, '/checkout/cart/line-item', {
        'items': [{'type': 'promotion', 'referencedId': code}]})
    promotion = next((line for line in api.cart_snapshot(cart)['lines'] if line['type'] == 'promotion'), None)
    events.append({'event': 'cart.promo', 'args': {'code': code},
                   'observed': {'discount': -promotion['total'] if promotion else 0,
                                'accepted': promotion is not None, **quote(cart)}})
    order = oracle.store(session, '/checkout/order', {})['id']
    detail = oracle.order(order)
    events.append({'event': 'checkout.place', 'args': {}, 'observed': {
        'gross': detail['totals'][2], 'net': detail['totals'][4], 'stock': api.get_stock(oracle, pid),
        **{key: detail[key] for key in ('stage', 'payment', 'delivery')},
    }})
    identifiers = detail['ids']
    state_ids = {kind: oracle.search(kind.replace('_', '-'), {'ids': [oid]})[0]['stateId']
                 for kind, oid in identifiers.items()}
    states, recorded = status(oracle, order, tags)
    if any(recorded.values()):
        raise ValueError('New native order already has a workflow tag')
    for letter in chain:
        kind, verb, slot = COMMANDS[letter]
        result, state_ids[kind] = api.step(oracle, kind, identifiers[kind], state_ids[kind], verb)
        current, markers = status(oracle, order, tags)
        entered = f'{slot}.{current[slot]}' if states[slot] != current[slot] else None
        events.append({'event': result['event'], 'args': {},
                       'observed': {**current, 'accepted': result['status'] == 200,
                                    'stock': api.get_stock(oracle, pid)},
                       'native_event': entered, 'markers': markers,
                       'delta': {key: markers[key] - recorded[key] for key in markers}})
        states, recorded = current, markers
    return {'shop': shop, 'channel': oracle.channel['id'], 'episode': number, 'split': 'holdout',
            'native_order': order, 'product': 'JOIN-' + pid[:12], 'initial': initial,
            'rate': rate, 'shipping': oracle.shipping,
            'policy': {'active': True, 'mode': mode, 'value': amount}, 'events': events}


def channel(profile, tags):
    shop, rate, shipping, mode, amount = profile
    oracle = api.Oracle(shipping)
    oracle.admin('/sales-channel/' + oracle.channel['id'], {'name': shop}, 'PATCH')
    code = 'HDJOIN-' + uuid4().hex[:8]
    api.promotion(oracle, code, mode, amount)
    rng = Random(shipping)
    rows = [episode(oracle, shop, rate, code, mode, amount, tags, chain, i, rng)
            for i, chain in enumerate(CHAINS)]
    print(shop, len(rows), 'native checkout and workflow journeys', flush=True)
    return rows


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('output', type=Path)
    args = cli.parse_args()
    profiles = [('Küstenwerk', 19, 825, 'percentage', 22),
                ('Wiesenlager', 7, 515, 'absolute', 1375)]
    tags = install(api.Oracle(500))
    with ThreadPoolExecutor(max_workers=2) as pool, args.output.open('w') as output:
        for rows in pool.map(lambda profile: channel(profile, tags), profiles):
            output.writelines(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
            output.flush()


if __name__ == '__main__':
    main()
