"""Compare a port, its forward-pass equivalent and trace-induced experts."""
from argparse import ArgumentParser
from contextlib import nullcontext
import json
from pathlib import Path
import platform
from unittest.mock import patch

import torch

from forward_vm import ExactLinear, load_forward
from learn_policy import fingerprint, json_lines, require_offline
from program_deck_bench import measure, validate_cohort
from program_deck_sparse import load


def effects(result):
    return [(row['shop'], row['case'], step['kind'], step.get('effect_sha256'), step.get('error'))
            for row in result['details'] for step in row['steps']]


def summary(result, deck):
    return {'exact': sum(row['exact'] for row in result['details']), 'total': len(result['details']),
            'by_shop': {shop: [sum(row['exact'] for row in result['details'] if row['shop'] == shop),
                               sum(row['shop'] == shop for row in result['details'])]
                        for shop in sorted({row['shop'] for row in result['details']})},
            'by_event': result['by_event'], 'p50_p95_apply_ms': result['apply_ms_p50_p95'],
            'replayed_checkout_receipts': result['replayed_checkout_receipts'],
            'forward_calls': getattr(deck.run_program, 'calls', 0)}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('holdout', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--port', type=int, action='append', required=True)
    parser.add_argument('--port-reference', type=Path, required=True, help='Source-informed port checkpoint')
    parser.add_argument('--induced', type=Path, required=True)
    parser.add_argument('--cases-per-shop', type=int, required=True)
    args = parser.parse_args()
    for port in args.port:
        require_offline(port)
    rows = json_lines(args.holdout)
    validate_cohort(rows, args.cases_per_shop)
    induced = json.loads((args.induced / 'induction.json').read_text())
    training = json.loads((args.port_reference / 'training.json').read_text())
    disjoint = set(induced['training_channels']) | set(training['training_channels'])
    if any(row['split'] != 'holdout' or row['channel'] in disjoint for row in rows):
        raise ValueError('Expected untouched native holdout channels')

    arms = {'A-port-interpreter': load(args.port_reference),
            'B-port-forward': load_forward(args.port_reference),
            'C-trace-forward': load_forward(args.induced)}
    control = load_forward(args.induced)
    machine = control.run_program
    gross = machine.grammar.atoms['gross'][1]
    expert = machine.experts[machine.addresses['gross', id(gross)]]
    weights = [cell.weight for cell in expert.modules() if isinstance(cell, ExactLinear)]
    if len(weights) != 1 or weights[0].tolist() != [1, 1]:
        raise ValueError('Expected a single two-input total summation')
    weights[0][0] = 0
    control.checkpoint += '-gross-weight-control'
    arms['C-gross-weight-control'] = control

    args.output.mkdir(parents=True, exist_ok=False)
    results = {}
    for label, deck in arms.items():
        guard = (nullcontext() if label == 'A-port-interpreter' else
                 patch('collection_vm.evaluate', side_effect=AssertionError('External interpreter entered')))
        with guard:
            result = measure(rows, deck, args.output / f'{label}.sqlite', set(), record_effects=True)
        results[label] = result
        (args.output / f'{label}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    a, b, c, negative = (effects(results[label]) for label in arms)
    report = {'holdout_sha256': fingerprint(args.holdout), 'training_sha256': induced['training_sha256'],
              'teacher_ports_refused_before_and_after': args.port,
              'platform': f'{platform.system()} {platform.machine()}', 'torch': torch.__version__,
              'port_forward_effects_identical': a == b, 'compared_steps': len(a),
              'gross_weight_changed_steps': sum(one != two for one, two in zip(c, negative, strict=True)),
              'arms': {label: summary(results[label], deck) for label, deck in arms.items()}}
    for port in args.port:
        require_offline(port)
    (args.output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if a != b:
        raise SystemExit('The compiled port changed the reference semantics')
    if c == negative:
        raise SystemExit('The in-expert weight intervention had no effect')


if __name__ == '__main__':
    main()
