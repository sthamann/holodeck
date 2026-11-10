"""Measure the same new traces with the old and incrementally extended deck."""
from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path

from adaptation import ACTIONS, restore
from graph_contract import Store, encode, root_of
from joint_deck import JointDeck, program_hash
from learn_policy import fingerprint, json_lines, require_offline
from program_deck_bench import measure
from program_deck_sparse import load
from program_lessons import canonical_order, canonical_quote, commands, seed


def attempt(deck, store, row, event, before):
    tenant = row['channel']
    try:
        probe = deck.plan(before, event, tenant)
    except (ValueError, KeyError, TypeError, ArithmeticError) as error:
        return None, before, str(error)
    event = {**event, 'offer': program_hash(probe['program'])}
    receipt = store.apply(tenant, event, deck, before['revision'])
    return receipt, store.read(tenant, before['root']), None


def check(label, model, rows, directory):
    deck = JointDeck(model)
    store = Store(directory / f'{label}.sqlite')
    details = []
    try:
        for row in rows:
            state = seed(row)
            state['config']['joint.checkpoint'] = deck.identity
            state['workflow_effects'] = []
            store.seed(state)
            journey = []
            for index, (kind, args) in enumerate(commands(row)):
                request = {'root': state['root'], 'id': f"{state['root']}:{index}", 'kind': kind, 'args': args}
                previous = state
                receipt, state, refusal = attempt(deck, store, row, request, state)
                fields = root_of(state)['fields']
                if index == 0:
                    correct = receipt and canonical_quote(fields['mixed.quote']) == row['before']
                    expected_write = True
                elif index == 1:
                    correct = receipt and canonical_quote(fields['mixed.quote']) == row['after']
                    expected_write = True
                elif index == 2:
                    correct = receipt and canonical_order(fields['mixed.order']) == row['order']
                    expected_write = True
                else:
                    native = row['events'][index - 3]
                    expected_write = native['http_status'] == 200 and native['event'] is not None
                    notifications = [item['event'] for item in receipt['workflow_steps']] if receipt else []
                    correct = (bool(receipt) == expected_write and
                               notifications == ([native['event']] if expected_write else []) and
                               all(fields.get('order.' + key) == value for key, value in native['state'].items()) and
                               {i['id']: i['stock'] for i in fields['mixed.catalog']} == native['stock'] and
                               len(state['workflow_effects']) == native['markers']['both'])
                    if not receipt:
                        correct &= store.read(row['channel'], state['root']) == previous
                journey.append({'event': kind, 'exact': bool(correct), 'expected_write': expected_write,
                                'committed': bool(receipt), 'refusal': refusal})
            details.append({'shop': row['shop'], 'case': row['case'],
                            'exact': all(step['exact'] for step in journey), 'steps': journey})
    finally:
        store.close()
    totals = Counter()
    for row in details:
        for step in row['steps']:
            if step['event'] not in ACTIONS:
                continue
            kind = 'mutations' if step['expected_write'] else 'no_mutations'
            totals[kind, 'total'] += 1
            totals[kind, 'exact'] += step['exact']
    return {'journeys': len(details), 'exact': sum(row['exact'] for row in details),
            **{kind: {stat: totals[kind, stat] for stat in ('total', 'exact')}
               for kind in ('mutations', 'no_mutations')}, 'details': details}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('specialist', type=Path)
    parser.add_argument('holdout', type=Path)
    parser.add_argument('regression', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--teacher-port', type=int, default=55724)
    args = parser.parse_args()
    require_offline(args.teacher_port)
    meta = json.loads((args.specialist / 'adaptation.json').read_text())
    rows = json_lines(args.holdout)
    shops = Counter(row['channel'] for row in rows)
    if len(shops) != 4 or set(shops.values()) != {4} or set(shops) & set(meta['training_channels']) or any(
            row['split'] != 'holdout' for row in rows):
        raise ValueError('Confirmation requires four complete, independent channels')
    args.output.mkdir(parents=True, exist_ok=False)
    before = load(args.base)
    after = restore(args.base, args.specialist)
    report = {'holdout_sha256': fingerprint(args.holdout), 'training_sha256': meta['source'],
              'before': check('before', before, rows, args.output),
              'after': check('after', after, rows, args.output)}
    regression = json_lines(args.regression)
    old = measure(regression, before, args.output / 'old-regression.sqlite', set())
    new = measure(regression, after, args.output / 'new-regression.sqlite', set())
    effects = 0
    for left, right in zip(old['details'], new['details'], strict=True):
        for a, b in zip(left['steps'], right['steps'], strict=True):
            if a['exact'] != b['exact'] or encode(a.get('program')) != encode(b.get('program')):
                raise AssertionError('The specialist changed an existing operation')
            effects += 1
    report['regression'] = {'journeys': len(regression), 'identical_programs': effects,
                            'old': sum(group['exact'] for group in old['groups'].values()),
                            'new': sum(group['exact'] for group in new['groups'].values())}
    require_offline(args.teacher_port)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: {k: v for k, v in report[key].items() if k != 'details'}
                      for key in ('before', 'after', 'regression')}))


if __name__ == '__main__':
    main()
