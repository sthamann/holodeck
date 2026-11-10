"""Compare whole-program retrieval and learned composition on sealed Shopware cohorts."""
from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from graph_contract import Store, encode, root_of
from learn_policy import fingerprint, json_lines, require_offline
from program_deck import Additive, Grammar, Learned, ProgramDeck, Retrieval, feature_encoder, train
from program_deck_checkpoint import Checkpoint
from program_deck_sparse import load as load_sparse, shipping_weight_control
from program_lessons import canonical_order, canonical_quote, cases, commands, seed


HERE = Path(__file__).resolve().parent
NOVEL = ('fixed', 'percentage')


def combination(row):
    return row['config']['shipping_tax'], row['config']['mode']


def validate_cohort(rows, expected):
    identities = {(row['channel'], row['case']) for row in rows}
    if len(identities) != len(rows):
        raise ValueError('Duplicate case identity in a capture')
    by_channel = Counter(row['channel'] for row in rows)
    if expected is not None and (len(by_channel) != 4 or set(by_channel.values()) != {expected}):
        raise ValueError(f'Expected four complete shops with {expected} cases each')


def hashes():
    return {name: fingerprint(HERE / name) for name in
            ('program_deck.py', 'program_lessons.py', 'collection_vm.py', 'graph_contract.py')}


def fit(args):
    rows = json_lines(args.source)
    validate_cohort(rows, args.cases_per_shop)
    if any(row['split'] != 'train' or combination(row) == NOVEL for row in rows):
        raise ValueError('Training includes an evaluation-only configuration')
    channels = {row['channel'] for row in rows}
    if len(channels) < 4:
        raise ValueError('Expected four separate training channels')
    samples, provenance = cases(rows, args.lab)
    supervision_events = len(samples)
    unique = {}
    for ctx, cells in samples:
        previous = unique.setdefault(ctx, cells)
        if previous != cells:
            raise ValueError('The observation contract cannot distinguish two teaching programs')
    samples = list(unique.items())
    grammar = Grammar.fit(samples)
    features = feature_encoder(args.base, [ctx for ctx, _ in samples])
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'grammar.json').write_text(json.dumps(grammar.atoms) + '\n')
    (args.output / 'retrieval.json').write_text(json.dumps(samples) + '\n')
    stats = {'training_shops': sorted({row['shop'] for row in rows}), 'training_channels': sorted(channels),
             'training_source': fingerprint(args.source), 'training_journeys': len(rows),
             'training_combinations': sorted({combination(row) for row in rows}),
             'source': hashes(), 'provenance': provenance, 'supervision_events': supervision_events,
             'distinct_signatures': len(samples), 'cells': len(grammar.cells),
             'alternatives_including_inactive': sum(map(len, grammar.atoms.values())),
             'base_revision': args.base.name, 'training_steps': args.steps, 'models': {}}
    for label, route in (('shared', False), ('routed', True)):
        path = args.output / f'{label}.safetensors'
        stats['models'][label] = {**train(features, grammar, samples, path, route, args.steps),
                                  'sha256': fingerprint(path)}
        print(label, stats['models'][label], flush=True)
    (args.output / 'fit.json').write_text(json.dumps(stats, indent=2) + '\n')


def score(row, index, receipt, state):
    fields = root_of(state)['fields']
    writes = receipt['writes']
    if index < 2:
        expected = row['before' if index == 0 else 'after']
        return {'write_set': set(writes) == {'mixed.items', 'mixed.quote'},
                'requested_items': fields['mixed.items'] == row['cart_input'],
                'quote': canonical_quote(fields['mixed.quote']) == expected}
    if index == 2:
        expected = row['order']
        return {'write_set': set(writes) == {'mixed.order', 'mixed.catalog', 'order.gross', 'order.net',
                                             'order.stage', 'order.payment', 'order.delivery'},
                'order': canonical_order(fields['mixed.order']) == expected,
                'stock': {item['id']: item['stock'] for item in fields['mixed.catalog']} == expected['stock'],
                'fields': all(fields[f'order.{key}'] == expected[key]
                              for key in ('gross', 'net', 'stage', 'payment', 'delivery'))}
    expected = row['events'][index - 3]
    return {'write_set': len(writes) == 2 and set(writes) <= {'order.stage', 'order.payment', 'order.delivery',
                                                             'result.accepted'},
            'accepted': writes.get('result.accepted') is True,
            'status': all(fields[f'order.{key}'] == value for key, value in expected['state'].items()),
            'workflow': fields['effect.ready'] == expected['markers']['both'],
            'effects': len(state['workflow_effects']) == expected['markers']['both'],
            'notification': [step['event'] for step in receipt['workflow_steps']] == [expected['event']]}


def measure(rows, deck, path, training):
    store = Store(path)
    details, repeats, timings = [], 0, []
    counts = Counter()
    try:
        for row in rows:
            state = seed(row)
            state['workflow_effects'] = []
            store.seed(state)
            steps = []
            for index, (kind, args) in enumerate(commands(row)):
                event = {'kind': kind, 'id': f'{state["root"]}:{index}', 'root': state['root'], 'args': args}
                revision = state['revision']
                begin = perf_counter()
                try:
                    receipt = store.apply(row['channel'], event, deck, revision)
                except (TypeError, ValueError, KeyError, ArithmeticError) as error:
                    steps.append({'kind': kind, 'exact': False, 'error': str(error)})
                    continue
                timings.append((perf_counter() - begin) * 1000)
                state = store.read(row['channel'], state['root'])
                try:
                    checks = score(row, index, receipt, state)
                except (TypeError, KeyError) as error:
                    checks = {'score_shape': False, 'error': str(error)}
                exact = all(value is True for value in checks.values())
                steps.append({'kind': kind, 'exact': exact, 'checks': checks,
                              'new_program': encode(dict(receipt['program'])) not in training,
                              'program': receipt['program']})
                if index == 2:
                    store.close()
                    store = Store(path)
                    replay = store.apply(row['channel'], event, deck, revision)
                    if encode(replay) != encode(receipt) or store.read(row['channel'], state['root']) != state:
                        raise AssertionError('Checkout replay changed its durable receipt or state')
                    repeats += 1
            group = 'novel' if combination(row) == NOVEL else 'known'
            exact = all(step['exact'] for step in steps)
            counts[group, 'total'] += 1
            counts[group, 'exact'] += exact
            counts[group, 'novel_promo'] += steps[1]['exact'] and steps[1].get('new_program', False)
            details.append({'shop': row['shop'], 'case': row['case'], 'config': row['config'],
                            'group': group, 'exact': exact, 'steps': steps})
    finally:
        store.close()
    family = Counter()
    for row in details:
        for step in row['steps']:
            family[step['kind'], 'total'] += 1
            family[step['kind'], 'exact'] += step['exact']
    return {'groups': {group: {key: counts[group, key] for key in ('total', 'exact', 'novel_promo')}
                       for group in ('known', 'novel')},
            'by_event': {kind: [family[kind, 'exact'], family[kind, 'total']]
                         for kind in sorted({kind for kind, _ in family})},
            'replayed_checkout_receipts': repeats, 'routes': {str(k): v for k, v in getattr(deck.predict, 'routes', {}).items()},
            'apply_ms_p50_p95': [float(value) for value in np.percentile(timings, [50, 95])]
            if timings else [],
            'details': details}


def evaluate(args):
    checkpoint = args.checkpoint
    info = json.loads((checkpoint / 'fit.json').read_text())
    if info['source'] != hashes() or info['base_revision'] != args.base.name:
        raise ValueError('The checkpoint requires the same execution sources and base model')
    rows = json_lines(args.source)
    validate_cohort(rows, args.cases_per_shop)
    channels = {row['channel'] for row in rows}
    if len(channels) < 4 or channels & set(info['training_channels']) or any(
            row['split'] != 'holdout' for row in rows) or len(
            {row['channel'] for row in rows if combination(row) == NOVEL}) < 2:
        raise ValueError('Need four fresh channels, at least two with the reserved configuration')
    if args.disjoint_from and channels & {row['channel'] for row in json_lines(args.disjoint_from)}:
        raise ValueError('Confirmation channels overlap the earlier diagnosis')
    grammar = Grammar(json.loads((checkpoint / 'grammar.json').read_text()))
    stored = json.loads((checkpoint / 'retrieval.json').read_text())
    samples = [((tuple(tags), text), cells) for (tags, text), cells in stored]
    features = feature_encoder(args.base, [ctx for ctx, _ in samples])
    seen = {encode(cells) for _, cells in samples}
    runtime = Checkpoint(args.runtime, args.base) if args.runtime else None
    arms = {
        'A-whole-program-retrieval': ProgramDeck(Retrieval(features, grammar, samples), 'whole-retrieval'),
        'A-additive-cells': ProgramDeck(Additive(features, grammar, samples), 'additive'),
        'B-shared': runtime.deck('shared') if runtime else ProgramDeck(
            Learned(features, grammar, checkpoint / 'shared.safetensors'), info['models']['shared']['sha256']),
        'C-routed': runtime.deck('routed') if runtime else ProgramDeck(
            Learned(features, grammar, checkpoint / 'routed.safetensors', routed=True), info['models']['routed']['sha256']),
        'C-rotated': ProgramDeck(Learned(features, grammar, checkpoint / 'routed.safetensors',
                                         routed=True, rotate=True), 'rotated-expert-control'),
    }
    if args.sparse:
        arms['D-unordered-sparse'] = load_sparse(args.sparse)
    perturbation = None
    if args.ordered:
        arms['E-ordered-sparse'] = load_sparse(args.ordered)
        arms['E-shipping-output-control'], perturbation = shipping_weight_control(
            args.ordered, args.ordered / 'grammar.json')
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'teacher_port_closed': args.teacher_port, 'holdout_sha256': fingerprint(args.source),
              'shops': sorted({row['shop'] for row in rows}), 'training': info, 'arms': {},
              'cases_by_shop': dict(sorted(Counter(row['shop'] for row in rows).items())),
              'learned_runtime_files': sorted(item.name for item in args.runtime.iterdir()) if runtime else None,
              'ordered_runtime_files': sorted(item.name for item in args.ordered.iterdir()) if args.ordered else None,
              'shipping_output_intervention': perturbation,
              'prior_diagnosis_sha256': fingerprint(args.disjoint_from) if args.disjoint_from else None,
              'latency_note': 'Local apply timings include varying input encoder cache warmness.'}
    for name, deck in arms.items():
        report['arms'][name] = result = measure(rows, deck, args.output / f'{name}.sqlite', seen)
        print(name, result['groups'], flush=True)
    require_offline(args.teacher_port)
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('fit', 'evaluate'))
    parser.add_argument('source', type=Path)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--lab', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--sparse', type=Path)
    parser.add_argument('--ordered', type=Path)
    parser.add_argument('--disjoint-from', type=Path)
    parser.add_argument('--steps', type=int, default=500)
    parser.add_argument('--cases-per-shop', type=int)
    parser.add_argument('--teacher-port', type=int, required=True)
    args = parser.parse_args()
    if args.steps < 1 or (args.cases_per_shop is not None and args.cases_per_shop < 1):
        parser.error('training steps and case counts must be positive')
    require_offline(args.teacher_port)
    if args.mode == 'evaluate' and args.checkpoint is None:
        parser.error('evaluate requires --checkpoint')
    (fit if args.mode == 'fit' else evaluate)(args)


if __name__ == '__main__':
    main()
