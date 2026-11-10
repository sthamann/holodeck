"""Fit and score the numeric extension of the joint transaction and projection deck."""
from argparse import ArgumentParser
from collections import Counter
from itertools import product
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from joint_lessons import projection_samples
from learn_policy import fingerprint, json_lines, require_offline
from money_programs import variant
from numeric_vm import install
from program_deck import Grammar, execute, signature
from program_deck_bench import measure, validate_cohort
from program_deck_sparse import fit, load, shipping_weight_control
from program_lessons import canonical_quote, commands, seed


HERE = Path(__file__).resolve().parent
# Retain the learned shipping stage and use the source's total amount as
# denominator if the new observations do not distinguish those choices.
HYPOTHESES = list(product((14, 17), ('amount', 'bands'), ('learned', 'base')))


def quote_errors(rows, baseline, choice):
    misses = 0
    for row in rows:
        state = seed(row)
        for index, (kind, args) in enumerate(commands(row)[:2]):
            event = {'id': str(index), 'kind': kind, 'root': state['root'], 'args': args}
            program = list(variant(dict(baseline.predict(signature(state, event))), *choice).items())
            state, writes = execute(state, event, program)
            misses += canonical_quote(writes['mixed.quote']) != row[('before', 'after')[index]]
    return misses


def fit_numeric(args):
    rows = json_lines(args.source)
    if len({row['channel'] for row in rows}) < 3 or any(row['split'] != 'train' for row in rows):
        raise ValueError('Fit requires separate training shops')
    baseline = load(args.baseline)
    install()
    counts = [quote_errors(rows, baseline, choice) for choice in HYPOTHESES]
    perfect = [choice for choice, error in zip(HYPOTHESES, counts, strict=True) if not error]
    if not perfect or len({choice[0] for choice in perfect}) != 1:
        raise ValueError(f'Traces do not uniquely identify a compatible precision: {counts}')
    choice = perfect[0]
    previous = json.loads(args.commerce.read_text())
    # Keep the original commerce gates and bindings as supervision. Only
    # numeric expressions change. Neither training labels nor the compiler
    # are needed by the live request path.
    samples = [(ctx, variant(cells, *choice)) for ctx, cells in previous] + projection_samples(previous)
    grammar = Grammar.fit(samples)
    with TemporaryDirectory() as directory:
        inputs = Path(directory)
        (inputs / 'grammar.json').write_text(json.dumps(grammar.atoms) + '\n')
        (inputs / 'retrieval.json').write_text(json.dumps(samples) + '\n')
        fit(inputs, args.output, ordered=True)
    manifest_path = args.output / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['numeric_vm_sha256'] = fingerprint(HERE / 'numeric_vm.py')
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    report = {'training_channels': sorted({row['channel'] for row in rows}),
              'training_shops': sorted({row['shop'] for row in rows}),
              'training_cases': len(rows), 'training_sha256': fingerprint(args.source),
              'baseline_checkpoint': baseline.checkpoint, 'numeric_vm': manifest['numeric_vm_sha256'],
              'grammar_sha256': manifest['grammar_sha256'], 'weights_sha256': manifest['weights_sha256'],
              'numeric_compiler': fingerprint(HERE / 'money_programs.py'),
              'choices': [{'hypothesis': hypothesis, 'wrong_quotes': error}
                          for hypothesis, error in zip(HYPOTHESES, counts, strict=True)],
              'selected': choice, 'equally_exact': perfect}
    (args.output / 'training.json').write_text(json.dumps(report, indent=2) + '\n')
    print(report, flush=True)


def evaluate(args):
    rows = json_lines(args.source)
    training = json.loads((args.checkpoint / 'training.json').read_text())
    validate_cohort(rows, args.cases_per_shop)
    channels = {row['channel'] for row in rows}
    if (len(channels) != 4 or any(row['split'] != 'holdout' for row in rows)
            or channels & set(training['training_channels'])):
        raise ValueError('Holdout must be disjoint from fitting')
    decks = {'baseline': load(args.baseline), 'numeric': load(args.checkpoint)}
    if decks['baseline'].checkpoint != training['baseline_checkpoint']:
        raise ValueError('Comparison baseline differs from the one used to compile the checkpoint')
    control, intervention = shipping_weight_control(args.checkpoint, args.checkpoint / 'grammar.json')
    args.output.mkdir(parents=True, exist_ok=False)
    cohorts = {'holdout': (args.source, rows)}
    for path in args.regression:
        cohorts[path.parent.name + '-' + path.stem] = (path, json_lines(path))
    report = {'teacher_port_closed': args.teacher_port,
              'shipping_weight_intervention': intervention,
              'fit_receipt_sha256': fingerprint(args.checkpoint / 'training.json'),
              'holdout_configs': sorted({(row['config']['item_increment'],
                                          row['config']['total_increment'],
                                          row['config']['shipping_tax']) for row in rows}),
              'cohorts': {}}
    for name, (path, cohort) in cohorts.items():
        results = {}
        arms = {**decks, 'numeric-shipping-control': control} if name == 'holdout' else decks
        for arm, deck in arms.items():
            result = measure(cohort, deck, args.output / f'{name}-{arm}.sqlite', set())
            by_shop = {shop: [sum(row['exact'] for row in result['details'] if row['shop'] == shop), total]
                       for shop, total in sorted(Counter(row['shop'] for row in cohort).items())}
            results[arm] = {'checkpoint': deck.checkpoint, 'by_shop': by_shop, **result}
            print(name, arm, by_shop, result['by_event'], result['apply_ms_p50_p95'], flush=True)
        report['cohorts'][name] = {'source_sha256': fingerprint(path), 'results': results}
    require_offline(args.teacher_port)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('fit', 'evaluate'))
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--teacher-port', type=int, required=True)
    parser.add_argument('--commerce', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--regression', type=Path, action='append', default=[])
    parser.add_argument('--cases-per-shop', type=int, default=12)
    args = parser.parse_args()
    if (args.mode == 'fit' and args.commerce is None) or (args.mode == 'evaluate' and args.checkpoint is None):
        parser.error('fit requires --commerce, evaluate requires --checkpoint')
    require_offline(args.teacher_port)
    (fit_numeric if args.mode == 'fit' else evaluate)(args)
