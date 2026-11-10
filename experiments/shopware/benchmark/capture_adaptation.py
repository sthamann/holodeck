"""Record accepted and refused after-sales commands from isolated Shopware."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from random import Random
from threading import Lock
from uuid import uuid4

from capture_flows import COMMANDS, api, install, status
from capture_mixed import sample


COMMANDS = {**COMMANDS, 'u': ('order_transaction', 'refund_partially', 'payment'),
            'r': ('order_transaction', 'refund', 'payment'),
            'v': ('order_delivery', 'retour_partially', 'delivery'),
            't': ('order_delivery', 'retour', 'delivery')}
PROFILES = {
    'train': (('Kanalservice', 485, (7, 19)), ('Kornwerk', 925, (7, 19))),
    'holdout': (('Birkenkai', 655, (5, 9, 19)), ('Sandfracht', 1145, (6, 11, 20)),
                ('Lindenlager', 785, (5, 11, 19)), ('Werksand', 1025, (6, 9, 20))),
}
TAPES = {
    'train': ('rtuvpsuvrtrt', 'trspvutrrt', 'prstuv', 'stprvu'),
    'holdout': ('ptusvtr', 'suvprt', 'vpsrtuv', 'ursptvrt'),
}
SETUP = Lock()


def observe(oracle, order, ids, tag_ids, catalog, tape):
    state_ids = {kind: oracle.search(kind.replace('_', '-'), {'ids': [oid]})[0]['stateId']
                 for kind, oid in ids.items()}
    state, markers = status(oracle, order, tag_ids)
    records = []
    for key in tape:
        entity, verb, field = COMMANDS[key]
        observed, state_ids[entity] = api.step(oracle, entity, ids[entity], state_ids[entity], verb)
        after, updated = status(oracle, order, tag_ids)
        records.append({'command': observed['event'], 'http_status': observed['status'],
                        'before': state, 'state': after, 'before_markers': markers, 'markers': updated,
                        'event': f'{field}.{after[field]}' if after[field] != state[field] else None,
                        'stock': dict(zip((item['id'] for item in catalog), oracle.stocks(catalog), strict=True))})
        state, markers = after, updated
    return records


def channel(profile, phase, tags, count):
    name, shipping, rates = profile
    with SETUP:
        oracle = api.Oracle(shipping)
    oracle.admin('/sales-channel/' + oracle.channel['id'], {'name': name}, 'PATCH')
    for rate in rates:
        if rate not in oracle.tax:
            identity = uuid4().hex
            oracle.sync('tax', [{'id': identity, 'name': f'{name} Teststeuer {rate}', 'taxRate': rate}])
            oracle.tax[rate] = identity
    code = 'AFTER-' + uuid4().hex[:10]
    api.promotion(oracle, code, 'percentage', 15 if phase == 'train' else 22)
    promo = ('percentage', 15 if phase == 'train' else 22, code)
    rng = Random(shipping)
    rows = []
    for i in range(count):
        def record(ref, order, ids, tag_ids, catalog, index):
            tape = TAPES[phase][index % len(TAPES[phase])]
            return observe(ref, order, ids, tag_ids, catalog, tape)
        row = sample(oracle, name, 'train' if phase == 'train' else 'holdout', i, promo, tags, rng, rates, observe=record)
        row['config'].update(shipping_tax='auto', shipping_rate=None)
        rows.append(row)
    print(name, phase, len(rows), flush=True)
    return rows


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=PROFILES)
    parser.add_argument('output', type=Path)
    parser.add_argument('--count', type=int, default=4)
    args = parser.parse_args()
    tags = install(api.Oracle(500))
    with args.output.open('x') as output, ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(channel, profile, args.phase, tags, args.count) for profile in PROFILES[args.phase]]
        for job in as_completed(jobs):
            output.writelines(json.dumps(row) + '\n' for row in job.result())
            output.flush()


if __name__ == '__main__':
    main()
