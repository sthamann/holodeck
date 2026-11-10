"""Capture checkout and rounding boundaries in fresh isolated Shopware channels."""
from argparse import ArgumentParser
from fractions import Fraction
import json
from pathlib import Path
from random import Random
from uuid import uuid4

from capture_mixed import api, install, sample


PROFILES = {
    'train': (('Geldkai', 525, 1, 1, None), ('Silberbogen', 865, 1, 5, 12),
              ('Marktbruecke', 532, 5, 5, None)),
    'holdout': (('Flusskonto', 735, 1, 1, None), ('Nordtaler', 813, 1, 5, 14),
                ('Kupfermuehle', 645, 5, 5, None), ('Hafenrechnung', 947, 1, 10, 16)),
}


def set_rounding(oracle, item, total):
    config = {name: {'decimals': 2, 'interval': cents / 100, 'roundForNet': True}
              for name, cents in (('itemRounding', item), ('totalRounding', total))}
    currency = oracle.channel['currencyId']
    oracle.admin('/currency/' + currency, config, 'PATCH')
    overrides = oracle.search('currency-country-rounding', {'filter': [
        {'type': 'equals', 'field': 'currencyId', 'value': currency},
        {'type': 'equals', 'field': 'countryId', 'value': oracle.country}]})
    for record in overrides:
        oracle.admin('/currency-country-rounding/' + record['id'], config, 'PATCH')


def fixtures(phase, index):
    qty = (14, 7, 2, 5, 13, 3)[index % 6]
    if phase == 'train' and index == 0:
        unit = 6199
    else:
        start = (2400 if phase == 'train' else 5100) + index * 219
        unit = next((value for value in range(start, start + 112)
                     if Fraction(value * qty * 12, 112).denominator == 2), start)
    basket = [(unit, 12, qty, qty + 3),
            (1103 + index * 173, 19, 2 + index % 4, 10),
              (803 + index * 97, 7, 1 + index % 5, 9)]
    if phase == 'holdout':
        basket.append((421 + index * 391, 12, 1 + index % 3, 9))
    return basket


def capture(profile, phase, tag_ids, count):
    shop, shipping, item, total, fixed = profile
    oracle = api.Oracle(shipping)
    oracle.admin('/sales-channel/' + oracle.channel['id'], {'name': shop}, 'PATCH')
    set_rounding(oracle, item, total)
    for rate in {7, 12, 19, fixed} - {None}:
        if rate not in oracle.tax:
            uid = uuid4().hex
            oracle.sync('tax', [{'id': uid, 'name': f'{shop} {rate}%', 'taxRate': rate}])
            oracle.tax[rate] = uid
    if fixed:
        oracle.admin('/shipping-method/' + oracle.channel['shippingMethodId'],
                     {'taxType': 'fixed', 'taxId': oracle.tax[fixed]}, 'PATCH')
    tag = 'ROUND-' + uuid4().hex[:8]
    percent = 13 if phase == 'train' else 17
    absolute = 517 if phase == 'train' else 763
    modes = [('none', 0, None), ('percentage', percent, tag+'P'), ('absolute', absolute, tag+'A')]
    for mode, amount, code in modes[1:]:
        api.promotion(oracle, code, mode, amount)
    rng = Random(shipping)
    for index in range(count):
        row = sample(oracle, shop, 'holdout', index, modes[index % len(modes)], tag_ids, rng, (7, 12, 19),
                     basket=fixtures(phase, index))
        row['split'] = phase
        observed = tuple(api.cents(row[key]['interval']) for key in ('item', 'total'))
        if observed != (item, total) or any(row[key]['decimals'] != 2 for key in ('item', 'total')):
            raise ValueError(f'Observed order rounding {observed} differs from configured {(item,total)}')
        row['config'].update(shipping_tax='fixed' if fixed else 'auto', shipping_rate=fixed,
                             item_increment=item, total_increment=total, money_profile='shopware-php82-p14')
        yield row
    print(shop, phase, count, flush=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=PROFILES)
    parser.add_argument('output', type=Path)
    parser.add_argument('--count', type=int, default=6)
    args = parser.parse_args()
    tags = install(api.Oracle(500))
    # Currency settings belong to the isolated teacher and are intentionally
    # changed sequentially. Each order records the effective rounding values.
    with args.output.open('x') as out:
        for profile in PROFILES[args.phase]:
            for row in capture(profile, args.phase, tags, args.count):
                out.write(json.dumps(row) + '\n')
                out.flush()


if __name__ == '__main__':
    main()
