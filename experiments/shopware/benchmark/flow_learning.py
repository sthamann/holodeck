"""Induce a finite-state workflow from event sequences and observed effect tags."""
from collections import deque
from hashlib import sha256
from itertools import combinations, permutations, product
import json
from pathlib import Path

import numpy as np

from graph_contract import encode, literal, read
from graph_ir import GRAPH, share
from policy_learning import PENALTY, best


SLOTS = ('memory.0', 'memory.1')
READY = 'effect.ready'
EMIT = 'effect.emitted'
FIELDS = {field: int for field in (*SLOTS, READY, EMIT)}


def prototype_space(alphabet):
    # A witness can be persistent, reset by one event, or restricted to the
    # latest event. None of the witnesses is assigned a domain meaning.
    clauses = [(event, reset) for event in alphabet
               for reset in (None, '*', *(name for name in alphabet if name != event))]
    yield ('false', (), False)
    yield ('true', (), False)
    for clause in clauses:
        for keep in (False, True):
            yield ('one', (clause,), keep)
    for pair in combinations(clauses, 2):
        if pair[0][0] != pair[1][0]:
            for name, keep in product(('all', 'any'), (False, True)):
                yield (name, pair, keep)
    for pair in permutations(clauses, 2):
        if pair[0][0] != pair[1][0]:
            for keep in (False, True):
                yield ('then', pair, keep)


def transition(prototype, before, event):
    operator, clauses, keep = prototype
    bits = []
    for i, (trigger, reset) in enumerate(clauses):
        hit = event == trigger and (operator != 'then' or i == 0 or before[0] == 1)
        value = hit if reset == '*' else (before[i] or hit) and event != reset
        bits.append(int(value))
    if operator in ('all', 'then'):
        ready = all(bits)
    elif operator in ('any', 'one'):
        ready = any(bits)
    else:
        ready = operator == 'true'
    return (*bits, int(ready or (keep and before[-1])))


def automaton(prototype, alphabet):
    """Canonical minimal Moore machine for equality on all event sequences."""
    start = (0,) * (len(prototype[1]) + 1)
    states = [start]
    indices = {start: 0}
    successors = []
    for state in states:
        row = []
        for event in alphabet:
            after = transition(prototype, state, event)
            if after not in indices:
                indices[after] = len(states)
                states.append(after)
            row.append(indices[after])
        successors.append(row)
    labels = [state[-1] for state in states]
    partitions = labels
    while True:
        named = {}
        refined = []
        for label, row in zip(labels, successors, strict=True):
            key = label, tuple(partitions[i] for i in row)
            refined.append(named.setdefault(key, len(named)))
        if refined == partitions:
            break
        partitions = refined
    representative = {group: i for i, group in enumerate(partitions)}
    pending = deque([partitions[0]])
    canonical = {partitions[0]: 0}
    result = []
    while pending:
        group = pending.popleft()
        pos = representative[group]
        row = []
        for successor in successors[pos]:
            target = partitions[successor]
            if target not in canonical:
                canonical[target] = len(canonical)
                pending.append(target)
            row.append(canonical[target])
        result.append((labels[pos], tuple(row)))
    return tuple(result)


def catalogue(alphabet):
    unique = {}
    for proto in prototype_space(alphabet):
        machine = automaton(proto, alphabet)
        if machine not in unique:
            unique[machine] = proto
    return [{'prototype': proto, 'automaton': machine} for machine, proto in unique.items()]


def observed_result(machine, event_indices):
    state = 0
    result = []
    for event in event_indices:
        state = machine[state][1][event]
        result.append(machine[state][0])
    return result


def compile_flow(prototype, alphabet):
    operator, clauses, keep = prototype
    event = read('event', 'token')
    updates = {}

    def hit(name):
        return ['eq', event, literal(alphabet.index(name))]

    for i, (trigger, reset) in enumerate(clauses):
        matched = hit(trigger)
        if operator == 'then' and i:
            matched = ['mul', matched, read('root', SLOTS[0])]
        persistent = ['max', read('root', SLOTS[i]), matched]
        if reset == '*':
            expression = matched
        elif reset is None:
            expression = persistent
        else:
            expression = ['mul', persistent, ['sub', literal(1), hit(reset)]]
        updates[SLOTS[i]] = expression
    if operator in ('all', 'then'):
        ready = ['min', *updates.values()]
    elif operator == 'any':
        ready = ['max', *updates.values()]
    elif operator == 'one':
        ready = updates[SLOTS[0]]
    else:
        ready = literal(int(operator == 'true'))
    previous = read('root', READY)
    if keep:
        ready = ['max', previous, ready]
    updates[READY] = ready
    updates[EMIT] = ['max', literal(0), ['sub', ready, previous]]
    return {GRAPH: share(updates)}


def replay_weights(candidates, alphabet, rows, label):
    prior = np.array([1 - len(candidate['automaton']) for candidate in candidates], dtype=np.int32)
    weights = prior.copy()
    timeline = []
    identities = set()
    for row in rows:
        identity = (row['channel'], row['episode'])
        if identity in identities:
            raise ValueError('Duplicate source sequence')
        identities.add(identity)
        events = [alphabet.index(step['event']) for step in row['steps']]
        observed = [step['observed'][label] for step in row['steps']]
        agrees = np.fromiter((observed_result(candidate['automaton'], events) == observed
                              for candidate in candidates), dtype=np.bool_, count=len(candidates))
        weights[~agrees] -= PENALTY
        timeline.append({'sequences': len(identities), 'consistent': int(np.sum(weights > -PENALTY)),
                         'minimal_tied': int(np.sum(weights == weights.max()))})
    index = best(weights)
    if index is None:
        raise ValueError(f'No uniquely supported minimal automaton for {label}: {timeline[-1]}')
    return weights, timeline, index


def fingerprint(value):
    return sha256(encode(value).encode()).hexdigest()


class FlowCheckpoint:
    def __init__(self, vocabulary_path, weight_path):
        data = json.loads(Path(vocabulary_path).read_text())
        self.alphabet = data['alphabet']
        with np.load(weight_path, allow_pickle=False) as snapshot:
            if str(snapshot['digest']) != fingerprint(data):
                raise ValueError('Weights require the original automata vocabulary')
            weights = snapshot['logits']
            if weights.shape != (len(data['candidates']),) or weights.dtype.kind != 'i':
                raise ValueError('Malformed flow checkpoint')
            index = best(weights)
        if index is None:
            raise ValueError('No supported workflow')
        self.identity = sha256(Path(weight_path).read_bytes()).hexdigest()
        self.selected = data['candidates'][index]
        self.program = compile_flow(self.selected['prototype'], self.alphabet)
