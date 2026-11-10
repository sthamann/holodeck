"""Compare exact and compatibility arithmetic against the native line oracle."""
from argparse import ArgumentParser
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
import json
from math import floor, log10
from pathlib import Path

from collection_vm import rounded


def php82_cents(value):
    """Positive, bounded monetary values on the PHP 8.2 half-away path."""
    if not value:
        return 0
    if not 0 < value < 1e10:
        raise ValueError('Outside the validated positive monetary range')
    places = 14 - floor(log10(value))
    if 2 < places < 17:
        shifted = floor(value * (10.0 ** places) + 0.5) / (10.0 ** (places - 2))
    else:
        shifted = value * 100
    return floor(shifted + 0.5)


def compatibility(unit_cents, rate, quantity, precision=14):
    unit = unit_cents / 100
    raw = ((unit / 100) * 100) / ((100 + rate) / 100) * (rate / 100)
    normalized = float(format(raw, f'.{precision}g'))
    return php82_cents(normalized * quantity)


def compare(rows):
    count = Counter()
    mismatches = []
    for row in rows:
        unit, rate, qty = (row[key] for key in ('unit', 'rate', 'quantity'))
        expected = row['14']['tax_cents']
        rational = Fraction(unit * rate, 100 + rate)
        with localcontext() as ctx:
            ctx.prec = 14
            ctx.rounding = ROUND_HALF_UP
            normalized = Decimal(unit * rate) / Decimal(100 + rate)
        compatible = compatibility(unit, rate, qty)
        count['cases'] += 1
        count['exact_half_cent'] += (rational * qty).denominator == 2
        count['precision_changes_native_result'] += expected != row['17']['tax_cents']
        count['rational_matches'] += rounded(rational * qty) == expected
        count['significant_decimal_matches'] += rounded(Fraction(normalized) * qty) == expected
        count['compatibility_matches'] += compatible == expected
        if compatible != expected:
            mismatches.append({**row, 'compatibility': compatible})
    known = next(row for row in rows if (row['unit'], row['rate'], row['quantity']) == (6199, 12, 14))
    return {**count, 'waldkai': known, 'first_compatibility_mismatches': mismatches[:8]}


def fixtures():
    cases = {(6199, 12, 14), (6198, 12, 14), (6200, 12, 14)}
    for rate in (4, 6, 8, 11, 12, 19, 20, 21):
        for qty in (1, 2, 3, 5, 7, 14, 23, 51):
            for start in (30, 850, 6090, 10000):
                ties = [price for price in range(start, start + 200)
                        if Fraction(price * qty * rate, 100 + rate).denominator == 2][:2]
                for price in ties or [start]:
                    cases.update((price + diff, rate, qty) for diff in (-1, 0, 1))
    return sorted(cases)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('fixtures', 'compare'))
    parser.add_argument('output', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    if args.mode == 'fixtures':
        args.output.write_text(json.dumps(fixtures()) + '\n')
    else:
        if args.source is None:
            parser.error('compare requires --source')
        report = compare([json.loads(line) for line in args.source.read_text().splitlines()])
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
