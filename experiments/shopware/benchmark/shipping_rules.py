"""Fit a structural prior and acquire shipping programs from cart observations."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from functools import lru_cache
import json
from math import log
from operator import eq, ge, lt
from pathlib import Path

import numpy as np

from collection_vm import ARITHMETIC, evaluate, literal, record, ref
from shipping_cases import AMOUNTS, CONTEXTS, COUNTRIES, FIELDS, GROUPS, WEIGHTS


ATOMS = (tuple(('country', '=', name) for name in COUNTRIES),
         tuple(('group', '=', name) for name in GROUPS),
         tuple(('amount', '>=', amount) for amount in AMOUNTS[1:]),
         tuple(('weight', op, weight) for op in ('>=', '<') for weight in WEIGHTS[1:]))
OP = {'=': eq, '>=': ge, '<': lt}
CONNECTIVES = ('and', 'or')
BUDGETS = (4, 8, 12, 16, 24, 36)
STRATEGIES = ('learned', 'uniform', 'size', 'fixed')
ALL = (1 << len(CONTEXTS)) - 1


def evaluate_tree(tree, facts):
    head, *rest = tree
    if head in CONNECTIVES:
        return (all if head == 'and' else any)(evaluate_tree(child, facts) for child in rest)
    op, target = rest
    return OP[op](facts[head], target)


def combine(op, left, right):
    children = [child for term in (left, right) for child in (term[1:] if term[0] == op else (term,))]
    return op, *sorted(children)


@lru_cache(maxsize=None)
def expressions(fields):
    if fields.bit_count() == 1:
        return tuple((sum(1 << i for i, case in enumerate(CONTEXTS)
                          if evaluate_tree(atom, dict(zip(FIELDS, case, strict=True)))), atom)
                     for atom in ATOMS[fields.bit_length() - 1])
    result = {}
    first = fields & -fields
    for left in range(1, fields):
        if left & fields != left or left & first == 0:
            continue
        for a, lhs in expressions(left):
            for b, rhs in expressions(fields ^ left):
                for mask, op in ((a & b, 'and'), (a | b, 'or')):
                    tree = combine(op, lhs, rhs)
                    if mask not in result or tree < result[mask]:
                        result[mask] = tree
    return tuple(result.items())


def shape(tree):
    head, *rest = tree
    if head in CONNECTIVES:
        return head, *sorted(shape(child) for child in rest)
    return (head,)


def features(tree, parent='root'):
    head, *rest = tree
    result = Counter({f'node:{parent}:{head}': 1})
    if head in CONNECTIVES:
        result[f'arity:{head}:{len(rest)}'] += 1
        for child in rest:
            result.update(features(child, head))
        for i, first in enumerate(rest):
            for second in rest[i+1:]:
                if first[0] not in CONNECTIVES and second[0] not in CONNECTIVES:
                    result['siblings:' + head + ':' + ':'.join(sorted((first[0], second[0])))] += 1
    elif head != 'const':
        result['atom:' + ':'.join(map(str, tree))] += 1
    return result


class Hypotheses:
    def __init__(self):
        programs = {0: ('const', False), ALL: ('const', True)}
        for fields in sorted(range(1, 1 << len(FIELDS)), key=lambda mask: (mask.bit_count(), mask)):
            for mask, tree in expressions(fields):
                programs.setdefault(mask, tree)
        self.masks = tuple(sorted(programs))
        self.index = {mask: i for i, mask in enumerate(self.masks)}
        self.trees = tuple(programs[mask] for mask in self.masks)
        self.table = np.array([[bool(mask & (1 << i)) for i in range(len(CONTEXTS))]
                               for mask in self.masks], dtype=bool)
        rows = [features(tree) for tree in self.trees]
        self.names = sorted(set().union(*rows))
        self.vectors = np.array([[row[name] for name in self.names] for row in rows], dtype=float)
        self.size = np.array([sum(value for name, value in row.items() if name.startswith('node:'))
                              for row in rows], dtype=float)


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def write(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write('\n')


def populations(rows, domain):
    grouped = defaultdict(dict)
    for row in rows:
        case = tuple(row['case'])
        channel = grouped[row['channel']]
        if case not in domain or case in channel or not set(row['available']) <= set(row['methods']):
            raise ValueError('Invalid or duplicate shipping observation')
        if row['quote'] is not None and row['quote']['method'] not in row['available']:
            raise ValueError('Native quote uses an unavailable shipping method')
        if 'order' in row and row['order'] != row['quote']:
            raise ValueError('Native order differs from the recorded quote')
        channel[case] = row
    for channel in grouped.values():
        if set(channel) != set(domain):
            raise ValueError('Incomplete observations for one shop')
        methods = next(iter(channel.values()))['methods']
        if len(methods) != len(set(methods)) or any(row['methods'] != methods for row in channel.values()):
            raise ValueError('Inconsistent shipping identities')
    return {identity: [channel[case] for case in domain] for identity, channel in grouped.items()}


def observed_masks(rows):
    return {method: sum(1 << i for i, row in enumerate(rows) if method in row['available'])
            for method in rows[0]['methods']}


def fit(hyp, traces):
    channels = populations(traces, CONTEXTS)
    masks = [mask for rows in channels.values() for mask in observed_masks(rows).values()]
    if any(mask not in hyp.index for mask in masks):
        raise ValueError('A training shipping method is outside the declared grammar')
    target = hyp.vectors[[hyp.index[mask] for mask in masks]].mean(axis=0)
    weights = np.zeros(len(hyp.names))
    for _ in range(350):
        scores = hyp.vectors @ weights
        probability = np.exp(scores - scores.max())
        probability /= probability.sum()
        weights += 0.15 * (target - probability @ hyp.vectors - 0.025 * weights)
    return {'weights': dict(zip(hyp.names, weights.round(10).tolist(), strict=True)),
            'training_channels': sorted(channels), 'training_masks': sorted(set(masks)),
            'hypotheses': len(hyp.masks), 'training_methods': len(masks)}


def entropy(probabilities):
    p = np.clip(probabilities, 1e-15, 1 - 1e-15)
    return -p * np.log(p) - (1-p) * np.log1p(-p)


def acquire(hyp, log_prior, rows, strategy, *, seed=241):
    methods = rows[0]['methods']
    candidates = {method: np.arange(len(hyp.masks)) for method in methods}
    pending = set(range(len(CONTEXTS)))
    generator = np.random.default_rng(seed)
    order = generator.permutation(len(CONTEXTS))
    history, snapshots = [], {}
    for step in range(1, len(CONTEXTS)+1):
        if strategy == 'fixed':
            query = int(order[step-1])
        else:
            score = np.zeros(len(CONTEXTS))
            for subset in candidates.values():
                if len(subset) < 2:
                    continue
                distribution = np.exp(log_prior[subset] - log_prior[subset].max())
                probability = (distribution @ hyp.table[subset]) / distribution.sum()
                score += entropy(probability)
            # The same API response observes all methods in the shop. Use an
            # unqueried context even if the class is now certain, to audit it.
            query = max(pending, key=lambda i: (round(float(score[i]), 12), -i))
        pending.remove(query)
        row = rows[query]
        history.append(query)
        for method, subset in candidates.items():
            candidates[method] = subset[hyp.table[subset, query] == (method in row['available'])]
        if step in BUDGETS:
            snapshots[step] = {'methods': {method: {'remaining': len(subset), **(
                {'tree': hyp.trees[int(subset[0])], 'mask': hyp.masks[int(subset[0])]} if len(subset) == 1 else {})}
                for method, subset in candidates.items()},
                'queries': history.copy(),
                'price': fit_price([rows[i] for i in history])}
    return snapshots


def fit_price(rows):
    """Infer a two-tier price from delivery quotes without reading price matrices."""
    quotes = [(row['case'], row['quote']['shipping']) for row in rows if row['quote'] is not None]
    if len({price for _, price in quotes}) != 2:
        return None
    candidates = []
    for field, thresholds in (('amount', AMOUNTS[1:]), ('weight', WEIGHTS[1:])):
        index = FIELDS.index(field)
        for threshold in thresholds:
            low = {price for facts, price in quotes if facts[index] < threshold}
            high = {price for facts, price in quotes if facts[index] >= threshold}
            if len(low) == len(high) == 1:
                candidates.append((field, threshold, low.pop(), high.pop()))
    return candidates[0] if len(candidates) == 1 else None


def build_checkpoint(hyp, prior, traces):
    channels = populations(traces, CONTEXTS)
    if set(channels) & set(prior['training_channels']) or set(prior['weights']) != set(hyp.names):
        raise ValueError('Training channels overlap or the prior does not match the grammar')
    logits = hyp.vectors @ np.array([prior['weights'][name] for name in hyp.names])
    priors = {'learned': logits, 'uniform': np.zeros(len(hyp.masks)),
              'size': -log(2)*hyp.size, 'fixed': np.zeros(len(hyp.masks))}
    return {strategy: {channel: acquire(hyp, priors[strategy], rows, strategy)
                       for channel, rows in channels.items()} for strategy in STRATEGIES}


def conjunction(*values):
    return all(values)


def disjunction(*values):
    return any(values)


def choose(condition, yes, no):
    return yes if condition else no


def install():
    for name, op in {'eq': eq, 'ge': ge, 'lt': lt, 'all': conjunction, 'any': disjunction,
                     'choose': choose}.items():
        if ARITHMETIC.setdefault(name, op) is not op:
            raise ValueError(f'Conflicting VM operation {name}')


def compile_rule(tree, scope):
    op, *rest = tree
    if op in CONNECTIVES:
        return ['all' if op == 'and' else 'any', *(compile_rule(term, scope) for term in rest)]
    if op == 'const':
        return literal(rest[0])
    compare, operand = rest
    return [{'=': 'eq', '>=': 'ge', '<': 'lt'}[compare], ref(scope, op), literal(operand)]


def compile_price(tier, scope):
    field, threshold, low, high = tier
    return ['choose', ['lt', ref(scope, field), literal(threshold)], literal(low), literal(high)]


def offer_program(snapshot, scope='facts'):
    if snapshot['price'] is None:
        return record()
    return record(**{method: record(available=compile_rule(value['tree'], scope),
                                   shipping=compile_price(snapshot['price'], scope))
                     for method, value in snapshot['methods'].items() if value['remaining'] == 1})


def offers(snapshot, case):
    install()
    facts = dict(zip(FIELDS, case, strict=True))
    return {method: value['shipping'] for method, value in evaluate(offer_program(snapshot), {'facts': facts}).items()
            if value['available']}


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('phase', choices=('fit', 'acquire'))
    cli.add_argument('source', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--prior', type=Path)
    args = cli.parse_args()
    hyp = Hypotheses()
    traces = read_rows(args.source)
    if args.phase == 'fit':
        write(args.output, fit(hyp, traces))
    elif args.prior is not None:
        write(args.output, build_checkpoint(hyp, json.loads(args.prior.read_text()), traces))
    else:
        cli.error('Provide --prior for acquisition')


if __name__ == '__main__':
    main()
