"""Record native Flow Builder transitions and committed order tags."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

source = Path(__file__).resolve().parents[1] / 'scale' / 'capture.py'
spec = importlib.util.spec_from_file_location('shopware_flow_oracle', source)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

COMMANDS = {
    'p': ('order_transaction', 'paid', 'payment'),
    'P': ('order_transaction', 'reopen', 'payment'),
    's': ('order_delivery', 'ship', 'delivery'),
    'S': ('order_delivery', 'reopen', 'delivery'),
    'x': ('order', 'process', 'stage'),
    'c': ('order_delivery', 'cancel', 'delivery'),
    'f': ('order_transaction', 'fail', 'payment'),
}
TRAIN = ('psPSps', 'spSPsp', 'pPxs', 'sSxp', 'xpPpP', 'xsSsS', 'x', 'pPx', 'sSx', 'xsSpPs')
TEST = ('xpPsSp', 'xsSpPs', 'pPpPx', 'sSsSx', 'pxPsS', 'sxSpP', 'p', 's',
        'psPpSsx', 'spSsPpx', 'xs', 'xp')


def order_tag_rule(oracle, name, tag):
    rule, root = uuid4().hex, uuid4().hex
    oracle.sync('rule', [{'id': rule, 'name': name, 'priority': 1, 'conditions': [
        {'id': root, 'type': 'andContainer', 'position': 0},
        {'id': uuid4().hex, 'parentId': root, 'type': 'orderTag', 'position': 0,
         'value': {'operator': '=', 'identifiers': [tag]}},
    ]}])
    return rule


def tag_action(tags, **extra):
    return {'id': uuid4().hex, 'actionName': 'action.add.order.tag', 'position': 1,
            'displayGroup': 1, 'config': {'tagIds': {tag: name for tag, name in tags.items()}}, **extra}


def install(oracle):
    nonce = uuid4().hex[:8]
    tags = {k: uuid4().hex for k in ('paid-witness', 'shipped-witness', 'both', 'either')}
    titles = {tag: f'Holodeck {nonce} {key}' for key, tag in tags.items()}
    oracle.sync('tag', [{'id': tag, 'name': name} for tag, name in titles.items()])
    flows = []
    for trigger, witness, other in (
        ('state_enter.order_transaction.state.paid', 'paid-witness', 'shipped-witness'),
        ('state_enter.order_delivery.state.shipped', 'shipped-witness', 'paid-witness'),
    ):
        rule = order_tag_rule(oracle, f'{nonce} {other}', tags[other])
        condition = uuid4().hex
        flows.extend([
            {'id': uuid4().hex, 'name': f'{nonce} {witness} combine', 'eventName': trigger,
             'active': True, 'priority': 30, 'sequences': [
                 {'id': condition, 'ruleId': rule, 'position': 1, 'displayGroup': 1},
                 tag_action({tags['both']: titles[tags['both']]}, parentId=condition, trueCase=True),
             ]},
            {'id': uuid4().hex, 'name': f'{nonce} {witness} record', 'eventName': trigger,
             'active': True, 'priority': 20, 'sequences': [
                 tag_action({tags[key]: titles[tags[key]] for key in (witness, 'either')}),
             ]},
        ])
    oracle.sync('flow', flows)
    return tags


def status(oracle, order, tag_ids):
    row = oracle.search('order', {'ids': [order], 'associations': {'tags': {}}})[0]
    actual = {tag['id'] for tag in row['tags']}
    detail = oracle.order(order)
    return ({k: detail[k] for k in ('stage', 'payment', 'delivery')},
            {k: int(tag_ids[k] in actual) for k in ('both', 'either')})


def capture_sequence(oracle, shop, product, tag_ids, commands, index, split):
    session = {}
    oracle.register(session)
    oracle.store(session, '/checkout/cart/line-item', {'items': [
        {'id': product, 'referencedId': product, 'type': 'product', 'quantity': 1,
         'stackable': True, 'removable': True}]})
    order = oracle.store(session, '/checkout/order', {})['id']
    details = oracle.order(order)
    local_ids = details['ids']
    state_ids = {kind: oracle.search(kind.replace('_', '-'), {'ids': [oid]})[0]['stateId']
                 for kind, oid in local_ids.items()}
    before_state, before = status(oracle, order, tag_ids)
    if any(before.values()):
        raise ValueError('Fresh order already has an effect tag')
    steps = []
    for letter in commands:
        kind, verb, slot = COMMANDS[letter]
        change, state_ids[kind] = api.step(oracle, kind, local_ids[kind], state_ids[kind], verb)
        if change['status'] != 200:
            raise ValueError(f'Fixture command failed: {change}')
        current, observed = status(oracle, order, tag_ids)
        if current[slot] == before_state[slot]:
            raise ValueError(f'Expected a native entered-state event: {change}')
        event = f'{slot}.{current[slot]}'
        steps.append({'event': event, 'current': current, 'observed': observed,
                      'delta': {key: observed[key] - before[key] for key in before}})
        before_state, before = current, observed
    return {'shop': shop, 'channel': oracle.channel['id'], 'episode': index, 'split': split,
            'native_order': order, 'steps': steps}


def channel(profile, tags, pilot, expanded):
    name, split, ship, unit, rate = profile
    oracle = api.Oracle(ship)
    oracle.admin('/sales-channel/' + oracle.channel['id'], {'name': name}, 'PATCH')
    product = uuid4().hex
    oracle.sync('product', [{'id': product, 'productNumber': 'FLOW-' + product[:12],
        'name': name + ' Testartikel', 'active': True, 'isCloseout': True,
        'stock': 100, 'taxId': oracle.tax[rate], 'price': api.prices(oracle, rate, unit),
        'visibilities': [{'salesChannelId': oracle.channel['id'], 'visibility': 30}]}])
    sequences = TRAIN if split == 'train' else TEST
    if expanded:
        sequences += ('pcSs', 'sfPp') if split == 'train' else ('pcSxsPp', 'sfxPpSs')
    if pilot:
        sequences = ('pPxs', 'sSxp')
    rows = [capture_sequence(oracle, name, product, tags, chain, index, split)
            for index, chain in enumerate(sequences)]
    print(name, split, len(rows), 'native Flow Builder journeys', flush=True)
    return rows


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('output', type=Path)
    cli.add_argument('--pilot', action='store_true')
    cli.add_argument('--historical', action='store_true')
    args = cli.parse_args()
    profiles = [('Flusslabor', 'train', 610, 1435, 19),
                ('Bergpost', 'holdout', 450, 2895, 7),
                ('Kanalhandel', 'holdout', 995, 5220, 19)]
    if args.historical:
        profiles = [('Zeitlabor', 'train', 845, 1880, 19),
                    ('Nordkai', 'holdout', 565, 3305, 7),
                    ('Farnlogistik', 'holdout', 1095, 4985, 19)]
    tags = install(api.Oracle(550))
    with ThreadPoolExecutor(max_workers=3) as pool, args.output.open('w') as output:
        for batch in pool.map(lambda profile: channel(profile, tags, args.pilot, args.historical),
                              profiles[:1] if args.pilot else profiles):
            output.writelines(json.dumps(row, ensure_ascii=False) + '\n' for row in batch)
            output.flush()


if __name__ == '__main__':
    main()
