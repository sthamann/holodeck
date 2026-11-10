"""Few-shot Bayesian program induction over an explicit, shared predicate grammar."""
from collections import Counter
from hashlib import sha256
from itertools import combinations
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter

import numpy as np

from graph_contract import encode, literal, read
from graph_ir import share


PENALTY = 64


def atoms(field, values):
    for threshold in sorted({value + offset for value in values for offset in (-1, 0, 1)}):
        yield (field, 'ge', threshold)
        yield (field, 'le', threshold)
        yield (field, 'eq', threshold)


def vocabulary(facts):
    fields = set(facts[0])
    if any(set(row) != fields or any(type(value) is not int for value in row.values()) for row in facts):
        raise ValueError('All examples need the same typed integer fact schema')
    tests = [atom for field in sorted(fields) for atom in atoms(field, [row[field] for row in facts])]
    result = [['constant', 0], ['constant', 1], *[['atom', atom] for atom in tests]]
    for first, second in combinations(tests, 2):
        if first[0] != second[0]:
            result.extend((['all', first, second], ['any', first, second]))
    return result


def satisfies(test, facts):
    field, op, threshold = test
    observed = facts[field]
    return observed >= threshold if op == 'ge' else observed <= threshold if op == 'le' else observed == threshold


def truth(candidate, facts):
    op, *terms = candidate
    if op == 'constant':
        return bool(terms[0])
    if op == 'atom':
        return satisfies(terms[0], facts)
    results = [satisfies(test, facts) for test in terms]
    return all(results) if op == 'all' else any(results)


def expression(candidate):
    op, *terms = candidate
    if op == 'constant':
        return literal(terms[0])

    def comparison(term):
        field, operator, threshold = term
        actual, boundary = read('root', field), literal(threshold)
        if operator == 'ge':
            return ['le', boundary, actual]
        return ['le' if operator == 'le' else 'eq', actual, boundary]

    if op == 'atom':
        return comparison(terms[0])
    return ['min' if op == 'all' else 'max', *[comparison(term) for term in terms]]


def digest(candidates):
    return sha256(encode(candidates).encode()).hexdigest()


def score_prior(candidate):
    return -(0 if candidate[0] == 'constant' else len(candidate) - 1)


def best(weights):
    winners = np.flatnonzero(weights == weights.max())
    if len(winners) != 1 or weights[winners[0]] <= -PENALTY:
        return None
    return int(winners[0])


class OnlinePosterior:
    def __init__(self, candidates):
        self.candidates = candidates
        self.weights = np.array([score_prior(candidate) for candidate in candidates], dtype=np.int32)
        self.events = {}
        self.timeline = []

    def observe(self, identity, facts, accepted):
        content = encode((facts, accepted))
        if identity in self.events:
            if self.events[identity] != content:
                raise ValueError('Observation identity reused for different content')
            return
        started = perf_counter()
        predicted = np.fromiter((truth(candidate, facts) for candidate in self.candidates),
                                dtype=np.bool_, count=len(self.candidates))
        self.weights[predicted != accepted] -= PENALTY
        self.events[identity] = content
        index = best(self.weights)
        self.timeline.append({'evidence': len(self.events),
                              'consistent': int(np.sum(self.weights > -PENALTY)),
                              'best_tied': int(np.sum(self.weights == self.weights.max())),
                              'winner': self.candidates[index] if index is not None else None,
                              'update_ms': (perf_counter() - started) * 1000})

    def save(self, path):
        path = Path(path)
        with NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                np.savez_compressed(output, logits=self.weights, vocabulary_digest=digest(self.candidates),
                                    evidence=len(self.events))
                output.flush()
                os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)


class PolicyCheckpoint:
    def __init__(self, vocabulary_path, weights_path):
        self.candidates = json.loads(Path(vocabulary_path).read_text())
        with np.load(weights_path, allow_pickle=False) as snapshot:
            if str(snapshot['vocabulary_digest']) != digest(self.candidates):
                raise ValueError('Policy weights require the original grammar vocabulary')
            self.weights = snapshot['logits'].copy()
            self.evidence = int(snapshot['evidence'])
        if self.weights.shape != (len(self.candidates),) or self.weights.dtype.kind != 'i':
            raise ValueError('Malformed policy logits')
        selected = best(self.weights)
        if self.evidence <= 0 or selected is None:
            raise ValueError('There is no uniquely supported policy checkpoint')
        self.candidate = self.candidates[selected]
        self.program = share({'eligible': expression(self.candidate)})


def minimal_consistent(candidates, rows):
    """Exact symbolic baseline over the same grammar and examples."""
    remaining = [candidate for candidate in candidates if all(
        truth(candidate, row['facts']) == row['observed']['cart.accepted'] for row in rows)]
    if not remaining:
        return []
    counts = Counter(score_prior(candidate) for candidate in remaining)
    priority = max(counts)
    return [candidate for candidate in remaining if score_prior(candidate) == priority]


def synthesize(candidates, rows):
    minimal = minimal_consistent(candidates, rows)
    return minimal[0] if len(minimal) == 1 else None, len(minimal)
