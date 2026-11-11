"""Record native contrasts for output-only induction on isolated Shopware."""
from argparse import ArgumentParser
import json
from pathlib import Path
from random import Random
from uuid import uuid4

from capture_mixed import api, install, sample
from capture_money import set_rounding


PROFILES = {
    'train': (('Uhrkai', 493, 1, 1, None, 11, 511),
              ('Wellenlager', 837, 5, 5, 12, 23, 787),
              ('Messingbogen', 763, 1, 5, 19, 27, 629),
              ('Birkentor', 537, 5, 5, None, 19, 913)),
    'holdout': (('Ankerfluss', 767, 5, 10, None, 17, 673),
                ('Drahtufer', 859, 2, 10, 14, 31, 839),
                ('Walzwerk', 943, 1, 2, 16, 37, 547),
                ('Seidenhafen', 721, 2, 5, None, 29, 967)),
    'forward-holdout': (('Lichtwerft', 679, 1, 10, 16, 21, 617),
                        ('Kieselmarkt', 583, 2, 5, None, 19, 743),
                        ('Eichenkai', 821, 5, 10, 14, 29, 937),
                        ('Formufer', 457, 1, 2, None, 13, 559)),
}


def baskets(rng, index, capped):
    if capped:
        return [(219, 12, 2, 3), (131, 19, 1, 4), (59, 7, 1, 4)]
    rates = (7, 12, 19, 12, 7)
    return [(rng.randrange(183, 9800), rate,
             9 if index % 4 == 0 and offset == 0 else rng.randrange(1, 5), 6)
            for offset, rate in enumerate(rates[:3 + index % 3])]


def capture(profile, phase, count, tags):
    shop, shipping, item, total, fixed, percent, absolute = profile
    oracle = api.Oracle(shipping)
    oracle.admin('/sales-channel/' + oracle.channel['id'], {'name': shop}, 'PATCH')
    set_rounding(oracle, item, total)
    for rate in {7, 12, 19, fixed} - {None}:
        if rate not in oracle.tax:
            identity = uuid4().hex
            oracle.sync('tax', [{'id': identity, 'name': f'{shop} {rate}%', 'taxRate': rate}])
            oracle.tax[rate] = identity
    if fixed:
        oracle.admin('/shipping-method/' + oracle.channel['shippingMethodId'],
                     {'taxType': 'fixed', 'taxId': oracle.tax[fixed]}, 'PATCH')
    code = 'IND-' + uuid4().hex[:10]
    none = ('none', 0, None)
    pct = ('percentage', percent, code + 'P')
    cut = ('absolute', absolute, code + 'A')
    cap = ('absolute', 5000, code + 'C')
    for mode, amount, coupon in (pct, cut, cap):
        api.promotion(oracle, coupon, mode, amount)
    rng = Random(shipping)
    for index in range(count):
        capped = fixed is not None and index % 5 == 3
        coupon = cap if capped else (none, pct, cut)[index % 3]
        row = sample(oracle, shop, 'holdout', index, coupon, tags, rng, (7, 12, 19),
                     basket=baskets(rng, index, capped))
        if tuple(api.cents(row[key]['interval']) for key in ('item', 'total')) != (item, total):
            raise ValueError('Order rounding differs from the requested training input')
        row['split'] = phase
        row['config'].update(item_increment=item, total_increment=total,
                             shipping_tax='fixed' if fixed else 'auto', shipping_rate=fixed,
                             money_profile='shopware-php82-p14')
        yield row
    print(shop, phase, count, flush=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=PROFILES)
    parser.add_argument('output', type=Path)
    parser.add_argument('--count', type=int, default=8)
    args = parser.parse_args()
    tags = install(api.Oracle(500))
    with args.output.open('x') as out:
        for profile in PROFILES[args.phase]:
            split = 'holdout' if args.phase == 'forward-holdout' else args.phase
            for row in capture(profile, split, args.count, tags):
                out.write(json.dumps(row) + '\n')
                out.flush()


if __name__ == '__main__':
    main()
