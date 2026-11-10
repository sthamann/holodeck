"""Compare acquisition and verify both consumers of the frozen masked policies."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from hidden_quantity import FIELDS, STRATEGIES, checkpoint, refit, refit_expanded, rows
from learn_policy import require_offline
from quantity_program import install, values_for
from quantity_transfer import native_options, replay_cart


def verify_population(support, primary, resolution, additional):
    by_id = {row['product']['id']: row for row in support}
    extra = {row['product_id']: row['result']['stock'] for row in additional}
    if len(by_id) != len(support) or len(extra) != len(additional):
        raise ValueError('Duplicate support product')
    if {row['product']['id'] for row in primary} != by_id.keys() or {
            row['product']['id'] for row in resolution} != extra.keys():
        raise ValueError('Reference population does not match its intended support')
    for cohort, testing, expected in ((primary, (5, 23), 10), (resolution, (3, 11), 5)):
        keys = set()
        for row in cohort:
            product = row['product']
            identity, stock = product['id'], product['stock']
            taught = by_id[identity]
            if row['split'] != 'holdout' or set(product) != FIELDS or (
                    row['channel'] != taught['channel'] or row['shop'] != taught['shop']):
                raise ValueError('Holdout inputs do not match the masked product')
            if any(product[field] != taught['product'][field] for field in FIELDS - {'stock'}):
                raise ValueError('The holdout must change only the stock input')
            if stock not in testing or stock in taught['support_stocks'] or stock == extra.get(identity):
                raise ValueError('Evaluation must use new stock inputs')
            if (identity, stock) in keys or len({event['request'] for event in row['cart']}) != expected:
                raise ValueError('Duplicate or incomplete reference snapshot')
            keys.add((identity, stock))
        if len(keys) != len({identity for identity, _ in keys}) * len(testing):
            raise ValueError('Incomplete stock intervention matrix')


def measure(observations, profiles, baseline, frozen, database):
    expected = checkpoint(profiles)
    if json.loads(frozen.read_text()) != expected:
        raise ValueError('Frozen program does not reproduce from support observations')
    admitted = [row for row in observations if row['product']['id'] in profiles]
    names = {row['product']['name'] for row in admitted}
    mismatched_controls = []
    for row in admitted:
        offered = list(values_for(expected['domain'], row['product']))
        native = native_options(row['storefront'])
        if offered != native:
            mismatched_controls.append({'product': row['product']['name'], 'stock': row['product']['stock'],
                                        'observed': native, 'offered': offered})
    runtime = replay_cart(observations, baseline, frozen, database)
    wrong_admitted = [miss for miss in runtime['misses'] if miss['product'] in names]
    admitted_actions = sum(len(row['cart']) for row in admitted)
    total_actions = sum(len(row['cart']) for row in observations)
    withheld = [row for row in observations if row['product']['id'] not in profiles]
    if any(values_for(expected['domain'], row['product']) for row in withheld):
        raise AssertionError('Unidentified product offered a purchase quantity')
    return {'controls_exact': len(admitted) - len(mismatched_controls), 'controls_tested': len(admitted),
            'controls_withheld': len(withheld), 'control_misses': mismatched_controls,
            'writes_exact': admitted_actions - len(wrong_admitted), 'writes_tested': admitted_actions,
            'writes_withheld': total_actions - admitted_actions, 'write_misses': wrong_admitted,
            'matches_including_abstentions': runtime['cart_exact'],
            'empty_checkouts_blocked': runtime['empty_checkout_blocked']}


def evaluate(capture, baseline, output):
    support = rows(capture / 'support.jsonl')
    primary = rows(capture / 'holdout.jsonl')
    additional = rows(capture / 'additional.jsonl')
    resolution = rows(capture / 'resolution-holdout.jsonl')
    verify_population(support, primary, resolution, additional)
    output.mkdir(parents=True, exist_ok=False)
    install()
    report = {'products': len(support), 'shops': len({row['channel'] for row in support}),
              'capture_sha256': {name: sha256((capture / name).read_bytes()).hexdigest()
                                 for name in ('support.jsonl', 'holdout.jsonl', 'additional.jsonl',
                                              'resolution-holdout.jsonl')},
              'strategies': {}, 'additional': additional}
    for strategy in STRATEGIES:
        learned = refit(support, strategy)
        histories = [row['learning'][strategy] for row in support]
        resolved_cost = [history['identified_at'] for history in histories if history['identified_at'] is not None]
        report['strategies'][strategy] = {'products_identified': len(learned),
            'queries': sum(len(history['probes']) for history in histories),
            'median_queries_for_identified': median(resolved_cost) if resolved_cost else None,
            **measure(primary, learned, baseline, capture / (strategy + '.json'), output / (strategy + '.sqlite'))}
    learned = refit_expanded(support, additional)
    report['expanded'] = {'products_identified': len(learned), 'additional_queries': len(additional),
        **measure(resolution, learned, baseline, capture / 'resolved.json', output / 'resolved.sqlite')}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--teacher-port', type=int, default=55729)
    args = parser.parse_args()
    require_offline(args.teacher_port)
    evaluate(args.capture, args.baseline, args.output)
    require_offline(args.teacher_port)
