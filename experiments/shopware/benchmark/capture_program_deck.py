"""Capture independent shipping tax and promotion configurations from Shopware."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from random import Random
from threading import Lock
from uuid import uuid4

from capture_mixed import api, install, sample


CHANNEL_SETUP = Lock()


PROFILES = {
    'train': (
        ('Kranlager', 415, 13, 725, (7, 19), None),
        ('Eichufer', 1075, 29, 1430, (7, 19), None),
        ('Sattelhof', 635, 18, 935, (7, 19), 7),
        ('Torfmarkt', 1230, 25, 1685, (7, 19), 19),
    ),
    'holdout': (
        ('Kanalwerk', 565, 17, 955, (5, 7, 19), 13),
        ('Duenenlager', 985, 33, 1570, (7, 13, 19), 5),
        ('Platanenhof', 785, 21, 625, (5, 13, 19), None),
        ('Lehmkontor', 1185, 27, 1335, (5, 7, 19), None),
    ),
    'confirm': (
        ('Erlenkai', 735, 26, 1175, (4, 8, 20), 11),
        ('Steindock', 1145, 37, 1870, (6, 12, 21), 17),
        ('Waldkai', 525, 16, 795, (4, 12, 20), None),
        ('Feldhafen', 1295, 32, 1365, (6, 8, 21), None),
    ),
    'joint': (
        ('Falkensteg', 670, 23, 1150, (5, 9, 19), 14),
        ('Kupferkai', 920, 31, 1450, (6, 11, 20), 16),
        ('Lindenbogen', 555, 19, 875, (5, 11, 19), None),
        ('Sandwerk', 1135, 28, 1220, (6, 9, 20), None),
    ),
}


def capture(profile, phase, tags, count):
    name, shipping, percent, fixed, rates, tax = profile
    with CHANNEL_SETUP:
        oracle = api.Oracle(shipping)
    oracle.admin('/sales-channel/' + oracle.channel['id'], {'name': name}, 'PATCH')
    for rate in set(rates) | ({tax} if tax is not None else set()):
        if rate not in oracle.tax:
            tax_id = uuid4().hex
            oracle.sync('tax', [{'id': tax_id, 'name': f'{name} Teststeuer {rate}', 'taxRate': rate}])
            oracle.tax[rate] = tax_id
    method = oracle.channel['shippingMethodId']
    if tax is not None:
        oracle.admin('/shipping-method/' + method, {'taxType': 'fixed', 'taxId': oracle.tax[tax]}, 'PATCH')
    settings = oracle.search('shipping-method', {'ids': [method]})[0]
    if settings['taxType'] != ('fixed' if tax is not None else 'auto'):
        raise ValueError('Shopware shipping method did not retain the requested configuration')
    prefix = 'DECK-' + uuid4().hex[:10]
    modes = [('none', 0, None), ('absolute', fixed, prefix + '-A')]
    if phase != 'train' or tax is None:
        modes.append(('percentage', percent, prefix + '-P'))
    for mode, value, code in modes[1:]:
        api.promotion(oracle, code, mode, value)
    rng = Random(shipping)
    rows = []
    for i in range(count):
        row = sample(oracle, name, 'holdout', i, modes[i % len(modes)], tags, rng, rates)
        row['split'] = 'train' if phase == 'train' else 'holdout'
        row['config'].update(shipping_tax=settings['taxType'], shipping_rate=tax)
        rows.append(row)
    print(name, phase, len(rows), flush=True)
    return rows


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=PROFILES)
    parser.add_argument('output', type=Path)
    parser.add_argument('--count', type=int, default=9)
    parser.add_argument('--shop', action='append', help='Record only named channels to finish an interrupted capture')
    args = parser.parse_args()
    profiles = [profile for profile in PROFILES[args.phase] if not args.shop or profile[0] in args.shop]
    if args.shop and {profile[0] for profile in profiles} != set(args.shop):
        parser.error('A selected shop is not in this capture phase')
    tags = install(api.Oracle(500))
    failures = []
    with args.output.open('x') as output, ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(capture, profile, args.phase, tags, args.count): profile[0] for profile in profiles}
        for job in as_completed(jobs):
            try:
                rows = job.result()
            except Exception as error:
                failures.append((jobs[job], error))
                continue
            output.writelines(json.dumps(row) + '\n' for row in rows)
            output.flush()
    if failures:
        name, error = failures[0]
        raise RuntimeError(f'{len(failures)} incomplete channels, first: {name}') from error


if __name__ == '__main__':
    main()
