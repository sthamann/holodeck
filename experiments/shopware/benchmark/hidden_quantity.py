"""Identify an executable quantity policy from bounded behavioral probes."""
from argparse import ArgumentParser
from dataclasses import asdict, dataclass
from functools import lru_cache
import json
from pathlib import Path

import numpy as np

from collection_vm import literal as L, record, ref as R


LIMIT = 64
BUDGET = 6
STRATEGIES = ('adaptive', 'analytic', 'fixed')
FIELDS = {'id', 'name', 'unit', 'rate', 'stock'}


@dataclass(frozen=True)
class Policy:
    start: int
    end: int
    stride: int
    stock_limited: bool


def policies():
    for start in range(1, LIMIT + 1):
        for limited in (False, True):
            yield Policy(start, start, 1, limited)
            for stride in range(1, LIMIT - start + 1):
                for end in range(start + stride, LIMIT + 1, stride):
                    yield Policy(start, end, stride, limited)


POLICIES = tuple(policies())


@lru_cache(maxsize=3)
def responses(stock):
    """Index by candidate and requested quantity minus one."""
    candidates = np.array([(p.start, p.end, p.stride, p.stock_limited) for p in POLICIES],
                          dtype=np.int16)
    start, end, stride, limited = candidates.T[:, :, None]
    high = np.where(limited, np.minimum(end, stock), end)
    requested = np.arange(1, LIMIT + 1, dtype=np.int16)
    projected = start + ((np.clip(requested, start, np.maximum(start, high)) - start) // stride) * stride
    return np.where(high < start, 0, projected).astype(np.uint8)


def adaptive(candidates, planes, evidence):
    visited = {(event['stock'], event['request']) for event in evidence}
    best = None
    for stock in planes:
        table = responses(stock)[candidates]
        for request, column in enumerate(table.T, 1):
            if (stock, request) in visited:
                continue
            bins = np.bincount(column)
            worst = int(max(bins))
            if worst == len(candidates):
                continue
            score = (worst, int(bins @ bins), stock != (evidence[-1]['stock'] if evidence else planes[-1]),
                     stock, request)
            if best is None or score < best[0]:
                best = (score, (stock, request))
    return best[1] if best else None


def fixed(candidates, planes, evidence):
    low, high = planes
    schedule = ((high, 1), (low, 32), (high, 32), (high, LIMIT), (low, 1), (high, 16))
    return schedule[len(evidence)] if len(evidence) < len(schedule) else None


def analytic(candidates, planes, evidence):
    low, high = planes
    stage = len(evidence)
    if stage == 0:
        return high, 1
    if stage == 1:
        return high, LIMIT
    first, last = (event['quantity'] for event in evidence[:2])
    if stage == 2 and first != last:
        return high, last - 1
    return (low, last) if evidence[-1]['stock'] == high else None


PROPOSERS = dict(adaptive=adaptive, analytic=analytic, fixed=fixed)


def acquire(strategy, planes, observe):
    if len(planes) != 2 or type(planes[0]) is not int or not 0 <= planes[0] < LIMIT or planes[1] != LIMIT:
        raise ValueError('Expected one lower stock and the reference high stock')
    candidates = np.arange(len(POLICIES))
    evidence, first_unique = [], None
    for _ in range(BUDGET):
        if len(candidates) == 1:
            first_unique = len(evidence)
            break
        query = PROPOSERS[strategy](candidates, planes, evidence)
        if query is None:
            break
        stock, request = query
        predicted = responses(stock)[candidates, request - 1]
        quantity = observe(stock, request)
        candidates = candidates[predicted == quantity]
        if not len(candidates):
            raise ValueError(f'Observation outside the declared family at {query}: {quantity}')
        evidence.append({'stock': stock, 'request': request, 'quantity': quantity, 'remaining': len(candidates)})
    if len(candidates) == 1:
        first_unique = len(evidence)
    return {'probes': evidence, 'remaining': len(candidates), 'identified_at': first_unique,
            'policy': asdict(POLICIES[candidates[0]]) if first_unique is not None else None}


def refit(rows, strategy):
    profiles = {}
    for row in rows:
        if set(row['product']) != FIELDS:
            raise ValueError('The import must omit all purchase-policy fields')
        events = iter(row['learning'][strategy]['probes'])

        def observe(stock, request):
            observation = next(events)
            if (stock, request) != (observation['stock'], observation['request']):
                raise ValueError('Recorded probe did not follow its frozen strategy')
            return observation['quantity']

        verified = acquire(strategy, row['support_stocks'], observe)
        if next(events, None) is not None or verified != row['learning'][strategy]:
            raise ValueError('Support transcript did not reproduce')
        if verified['policy'] is not None:
            profiles[row['product']['id']] = verified['policy']
    return profiles


def resolve(row, observe):
    """Ask a separating question only after the original support is exhausted."""
    evidence = row['learning']['analytic']['probes']
    candidates = np.arange(len(POLICIES))
    for event in evidence:
        outcomes = responses(event['stock'])[candidates, event['request'] - 1]
        candidates = candidates[outcomes == event['quantity']]
    if len(candidates) <= 1:
        raise ValueError('Resolution requires unresolved support')
    query = adaptive(candidates, (0, *row['support_stocks']), evidence)
    if query is None:
        raise ValueError('Expanded support cannot separate these candidates')
    stock, request = query
    quantity = observe(stock, request)
    outcomes = responses(stock)[candidates, request - 1]
    candidates = candidates[outcomes == quantity]
    if not len(candidates):
        raise ValueError('New evidence contradicts the declared policy family')
    return {'stock': stock, 'request': request, 'quantity': quantity,
            'before': row['learning']['analytic']['remaining'], 'remaining': len(candidates),
            'policy': asdict(POLICIES[candidates[0]]) if len(candidates) == 1 else None}


def refit_expanded(support, additional):
    profiles = refit(support, 'analytic')
    by_id = {row['product']['id']: row for row in support}
    for row in additional:
        identity, result = row['product_id'], row['result']
        if identity in profiles:
            raise ValueError('Additional support cannot overwrite an identified policy')

        def observe(stock, request):
            if (stock, request) != (result['stock'], result['request']):
                raise ValueError('Additional probe did not follow the separating strategy')
            return result['quantity']

        if resolve(by_id[identity], observe) != result:
            raise ValueError('Additional support did not reproduce')
        if result['policy'] is not None:
            profiles[identity] = result['policy']
    return profiles


def checkpoint(profiles):
    # Unidentified products have an empty progression in both the reader and
    # the writer. A learned cap remains independent of the mutable stock fact.
    default = {'start': 1, 'end': 0, 'stride': 1, 'stock_limited': False}
    entry = ['get_default', L(profiles), R('product', 'id'), L(default)]

    def lookup(key):
        return ['get', entry, L(key)]

    end = lookup('end')
    return {'supervision': 'cart', 'exact_domains': 1, 'catalog_fields': [], 'mode': 'floor',
            'domain': record(start=lookup('start'), stride=lookup('stride'), end=[
                'select_value', lookup('stock_limited'), ['min', end, R('product', 'stock')], end])}


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def write_checkpoint(path, profiles):
    with path.open('x') as file:
        file.write(json.dumps(checkpoint(profiles), indent=2) + '\n')


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('support', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--strategy', choices=STRATEGIES, default='analytic')
    parser.add_argument('--additional', type=Path)
    args = parser.parse_args()
    support = rows(args.support)
    if args.additional:
        if args.strategy != 'analytic':
            parser.error('The additional probes follow the analytic arm')
        profiles = refit_expanded(support, rows(args.additional))
    else:
        profiles = refit(support, args.strategy)
    write_checkpoint(args.output, profiles)
