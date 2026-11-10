"""Induce scalar expressions from observations, then compile them into a joint deck.

Only typed operand scopes are declared. No target expression or candidate
Shopware formula is supplied to the search.
"""
from argparse import ArgumentParser
from collections import defaultdict
from copy import deepcopy
from fractions import Fraction
from itertools import product
import json
from operator import add, mul, neg, sub
from pathlib import Path
from shutil import copyfile

from collection_vm import literal as L, ref as R
from learn_policy import fingerprint, json_lines, require_offline
from numeric_vm import quantize
from program_deck_bench import measure, validate_cohort
from program_deck_sparse import load


MONEY, COUNT, RATIO, STEP = 'money', 'count', 'ratio', 'step'
MAX_NODES = 7
SAME_TYPE = tuple((kind, kind) for kind in (MONEY, COUNT))
OPERATORS = [
    *((name, signature, signature[0], function, commutative)
      for name, function, commutative in (('add', add, True), ('sub', sub, False),
                                            ('min', min, True), ('max', max, True))
      for signature in SAME_TYPE),
    ('neg', (MONEY,), MONEY, neg, False),
    ('mul', (MONEY, COUNT), MONEY, mul, False),
    ('mul', (MONEY, RATIO), MONEY, mul, False),
    ('quantize', (MONEY, STEP), MONEY, lambda amount, step: quantize(float(amount / 100), step), False),
]


def induce(examples, types, result_type):
    """Bottom-up enumeration, retaining one expression per observed value vector."""
    table, seen = defaultdict(list), defaultdict(set)
    target = tuple(value for _, value in examples)
    match = []
    attempts = 0

    def admit(cost, kind, tree, vector):
        if kind == result_type and vector == target:
            match.append(tree)
        if vector in seen[kind]:
            return
        seen[kind].add(vector)
        table[cost, kind].append((tree, vector))

    for key, kind in types.items():
        admit(1, kind, ('ref', key), tuple(facts[key] for facts, _ in examples))
    for kind in {MONEY, COUNT}:
        admit(1, kind, ('literal', 0), (0,) * len(examples))
    for cost in range(2, MAX_NODES + 2):
        if match:
            return {'expression': match[0], 'nodes': cost - 1, 'exact_root_matches': len(match),
                    'evaluated_candidates': attempts, 'examples': len(examples)}
        if cost > MAX_NODES:
            break
        for name, kinds, output, function, symmetric in OPERATORS:
            if len(kinds) == 1:
                partitions = [(cost - 1,)]
            else:
                partitions = [(left, cost - 1 - left) for left in range(1, cost - 1)
                              if not symmetric or left <= cost - 1 - left]
            for sizes in partitions:
                domains = [table[size, kind] for size, kind in zip(sizes, kinds, strict=True)]
                for operands in product(*domains):
                    trees, vectors = zip(*operands, strict=True)
                    if symmetric and sizes[0] == sizes[1] and trees[0] > trees[1]:
                        continue
                    attempts += 1
                    vector = tuple(function(*values) for values in zip(*vectors, strict=True))
                    admit(cost, output, (name, *trees), vector)
    raise ValueError(f'No exact {result_type} expression in budget after {attempts} evaluations')


TASKS = {
    'quantity': ({'requested': COUNT, 'stock': COUNT}, COUNT),
    'line': ({'unit': MONEY, 'quantity': COUNT, 'item': STEP}, MONEY),
    'remaining': ({'stock': COUNT, 'quantity': COUNT}, COUNT),
    'absolute': ({'basis': MONEY, 'setting': MONEY, 'item': STEP}, MONEY),
    'percentage': ({'basis': MONEY, 'setting': RATIO, 'item': STEP}, MONEY),
    'shipping': ({'setting': MONEY, 'item': STEP, 'total': STEP}, MONEY),
    'goods': ({'basis': MONEY, 'adjustment': MONEY}, MONEY),
    'gross': ({'goods': MONEY, 'delivery': MONEY, 'item': STEP, 'total': STEP}, MONEY),
    'net': ({'goods': MONEY, 'delivery': MONEY, 'tax': MONEY}, MONEY),
}


def lessons(rows):
    teaching = defaultdict(list)
    for row in rows:
        config, order = row['config'], row['order']
        item, total = config['item_increment'], config['total_increment']
        requested = {line['id']: line['quantity'] for line in row['cart_input']}
        for product_row in row['catalog']:
            identity, stock = product_row['id'], product_row['stock']
            line = order['lines'].get(identity)
            quantity = line['quantity'] if line else 0
            if line:
                teaching['quantity'].append(({'requested': requested[identity], 'stock': stock}, quantity))
                teaching['line'].append(({'unit': product_row['unit'], 'quantity': quantity, 'item': item},
                                         line['gross']))
            teaching['remaining'].append(({'stock': stock, 'quantity': quantity}, order['stock'][identity]))
        basis = row['before']['goods']
        mode = config['mode']
        adjustment = 0 if mode == 'none' else row['after']['discount']['gross']
        if mode != 'none':
            setting = config['discount_value']
            if mode == 'percentage':
                setting = Fraction(setting, 100)
            teaching[mode].append(({'basis': basis, 'setting': setting, 'item': item}, adjustment))
        for quote, change in ((row['before'], 0), (row['after'], adjustment)):
            delivery, goods = quote['shipping']['gross'], quote['goods']
            teaching['shipping'].append(({'setting': config['shipping'], 'item': item, 'total': total}, delivery))
            teaching['goods'].append(({'basis': basis, 'adjustment': change}, goods))
            teaching['gross'].append(({'goods': goods, 'delivery': delivery, 'item': item, 'total': total},
                                      quote['gross']))
            teaching['net'].append(({'goods': goods, 'delivery': delivery,
                                     'tax': sum(band[2] for band in quote['taxes'])}, quote['net']))
    return teaching


def lower(tree, bindings):
    op, *args = tree
    if op == 'ref':
        return bindings[args[0]]
    if op == 'literal':
        return L(args[0])
    operands = [lower(arg, bindings) for arg in args]
    if op == 'quantize':
        return ['php82_quantize', ['f64_div', operands[0], L(100)], operands[1]]
    if op == 'neg':
        return ['sub', L(0), operands[0]]
    return [op, *operands]


def substitute(tree, before, after):
    if tree == before:
        return after
    if isinstance(tree, dict):
        return {key: substitute(value, before, after) for key, value in tree.items()}
    if isinstance(tree, list) and tree[0] != 'literal':
        return [substitute(value, before, after) for value in tree]
    return tree


def compile_grammar(atoms, learned):
    """Replace identified scalar producers in the published grammar, preserving token IDs."""
    def expression(name, **bindings):
        return lower(learned[name]['expression'], bindings)

    revised = deepcopy(atoms)
    item = ['get_default', R('config'), L('item_increment'), L(1)]
    total = ['get_default', R('config'), L('total_increment'), L(1)]
    previous_delivery = ['php82_quantize', ['f64_div', R('config', 'shipping'), L(100)], item]
    delivery = expression('shipping', setting=R('config', 'shipping'), item=item, total=total)
    for name in ('shipping_bands', 'quote'):
        revised[name] = [substitute(tree, previous_delivery, delivery) for tree in revised[name]]
    for index, tree in enumerate(atoms['lines'][1:], start=1):
        result = deepcopy(tree)
        fields = result[3][1][3][1]
        prior = fields['quantity']
        quantity = expression('quantity', requested=R('request', 'quantity'), stock=R('product', 'stock'))
        fields['quantity'] = quantity
        fields['gross'] = expression('line', unit=R('product', 'unit'), quantity=quantity, item=item)
        fields['tax'] = substitute(fields['tax'], prior, quantity)
        revised['lines'][index] = result
    # The existing selector supplies the mode. Its token choices are unchanged.
    modes = {0: None, 1: None, 2: 'absolute', 3: 'percentage'}
    if len(atoms['discount_value']) != len(modes):
        raise ValueError('The discount vocabulary differs from the published checkpoint')
    for index, mode in modes.items():
        if mode is not None:
            value = R('config', 'discount_value')
            setting = ['div', value, L(100)] if mode == 'percentage' else value
            revised['discount_value'][index] = expression(mode, basis=R('subtotal'), setting=setting, item=item)
    tax = ['sum', ['map', R('total_bands'), 'b', R('b', 'tax')]]
    revised['goods'][1] = expression('goods', basis=R('subtotal'), adjustment=R('discount_value'))
    revised['gross'][1] = expression('gross', goods=R('goods'), delivery=delivery, item=item, total=total)
    revised['net'][1] = expression('net', goods=R('goods'), delivery=delivery, tax=tax)
    updated = revised['updated'][1][3][1]
    updated['stock'] = expression('remaining', stock=R('product', 'stock'), quantity=updated['stock'][2])
    return revised


def fit(source, baseline, destination):
    rows = json_lines(source)
    if not rows or any(row['split'] != 'train' for row in rows):
        raise ValueError('Fit accepts training observations only')
    teaching = lessons(rows)
    learned = {name: induce(teaching[name], *spec) for name, spec in TASKS.items()}
    atoms = json.loads((baseline / 'grammar.json').read_text())
    revised = compile_grammar(atoms, learned)
    destination.mkdir(parents=True, exist_ok=False)
    grammar = destination / 'grammar.json'
    grammar.write_text(json.dumps(revised) + '\n')
    copyfile(baseline / 'sparse.npz', destination / 'sparse.npz')
    manifest = json.loads((baseline / 'manifest.json').read_text())
    manifest['grammar_sha256'] = fingerprint(grammar)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    result = {'training_channels': sorted({row['channel'] for row in rows}),
              'training_shops': sorted({row['shop'] for row in rows}),
              'training_cases': len(rows), 'training_sha256': fingerprint(source),
              'compiler_sha256': fingerprint(Path(__file__)), 'learned': learned}
    (destination / 'induction.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


def evaluate(source, baseline, checkpoint, output, count, passive):
    rows = json_lines(source)
    validate_cohort(rows, count)
    fit_info = json.loads((checkpoint / 'induction.json').read_text())
    if any(row['split'] != 'holdout' for row in rows) or set(fit_info['training_channels']) & {
            row['channel'] for row in rows}:
        raise ValueError('Holdout must be disjoint from the training channels')
    output.mkdir(parents=True, exist_ok=False)
    report = {'holdout_sha256': fingerprint(source), 'arms': {}}
    arms = {'source_authored': baseline, 'induced': checkpoint}
    if passive is not None:
        arms['passive_induction'] = passive
    for name, path in arms.items():
        if name != 'source_authored':
            info = json.loads((path / 'induction.json').read_text())
            if set(info['training_channels']) & {row['channel'] for row in rows}:
                raise ValueError(f'Training and holdout channels overlap for {name}')
        result = measure(rows, load(path), output / (name + '.sqlite'), set())
        report['arms'][name] = result
        print(name, result['groups'], result['by_event'], flush=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('fit', 'evaluate'))
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--passive', type=Path)
    parser.add_argument('--cases-per-shop', type=int)
    parser.add_argument('--teacher-port', type=int, required=True)
    args = parser.parse_args()
    require_offline(args.teacher_port)
    if args.mode == 'fit':
        fit(args.source, args.baseline, args.output)
    elif args.checkpoint is None:
        parser.error('evaluate requires --checkpoint')
    else:
        evaluate(args.source, args.baseline, args.checkpoint, args.output, args.cases_per_shop, args.passive)
    require_offline(args.teacher_port)


if __name__ == '__main__':
    main()
