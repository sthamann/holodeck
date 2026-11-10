"""One learned dataflow decoder for commerce writes and internal transitions."""
from collections import Counter
from copy import deepcopy
from graphlib import TopologicalSorter
from hashlib import sha256
import json

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
import numpy as np
from mlx.utils import tree_flatten

from collection_vm import run
from graph_contract import encode, retrieve, root_of
from models import Controller, Features


# This is the storage schema. Event-to-program mappings live in the weights.
FIELD_TYPES = {
    'mixed.items': list, 'mixed.catalog': list, 'mixed.quote': dict, 'mixed.order': dict,
    'order.gross': int, 'order.net': int, 'order.stage': str, 'order.payment': str, 'order.delivery': str,
    'result.accepted': bool, 'memory.0': int, 'memory.1': int, 'effect.ready': int, 'effect.emitted': int,
}
NOTIFY = {'order.payment': 'payment', 'order.delivery': 'delivery', 'order.stage': 'stage'}
EMISSIONS = {'effect.emitted': 'order.mark'}


def signature(state, event):
    fields = root_of(state)['fields']
    tags = {f'event={event["kind"]}'}
    for name, values in (('state', fields), ('config', state['config']), ('event', event['args'])):
        tags.update(f'{name}.{key}' for key in values)
        tags.update(f'{name}.{key}={value}' for key, value in values.items() if type(value) in (str, bool))
    return tuple(sorted(tags)), ''


def references(expression):
    if isinstance(expression, list):
        if expression and expression[0] == 'ref':
            yield expression[1]
        elif not expression or expression[0] != 'literal':
            for term in expression:
                yield from references(term)
    elif isinstance(expression, dict):
        for term in expression.values():
            yield from references(term)


class Grammar:
    def __init__(self, atoms):
        self.atoms = atoms
        self.cells = list(atoms)
        self.width = max(map(len, atoms.values()))
        self.mask = mx.array([[0 if i < len(atoms[key]) else -1e6 for i in range(self.width)]
                              for key in self.cells])
        self.indices = [{encode(expr): idx for idx, expr in enumerate(atoms[key])} for key in self.cells]

    @classmethod
    def fit(cls, samples):
        choices = {}
        for _, cells in samples:
            for name, expression in cells.items():
                choices.setdefault(name, {}).setdefault(encode(expression), expression)
        graph = {key: sorted({ref for expr in choices[key].values() for ref in references(expr)
                              if ref in choices and ref != key}) for key in sorted(choices)}
        order = TopologicalSorter(graph).static_order()
        return cls({key: [None, *(choices[key][expr] for expr in sorted(choices[key]))] for key in order})

    def targets(self, program):
        return [index[encode(program.get(key))] for key, index in zip(self.cells, self.indices, strict=True)]

    def decode(self, tokens):
        return [(key, self.atoms[key][int(token)]) for key, token in zip(self.cells, tokens, strict=True)
                if token]


class Head(Controller):
    def __init__(self, dimension, grammar, routed):
        super().__init__(dimension, None, routed)
        self.out = nn.Linear(96, len(grammar.cells) * grammar.width)
        self.grammar = grammar

    def logits(self, features, training=False, rotate=False):
        hidden, gates = self.hidden(features, training=training, reroute=rotate)
        return self.out(hidden).reshape(-1, len(self.grammar.cells), self.grammar.width) + self.grammar.mask, gates


def train(features, grammar, samples, path, routed, steps):
    mx.random.seed(161)
    rng = np.random.default_rng(161)
    x = mx.array(np.stack([features(ctx) for ctx, _ in samples]))
    y = mx.array(np.array([grammar.targets(cells) for _, cells in samples], dtype=np.int32))
    model = Head(features.dimension, grammar, routed)
    optimizer = optim.Adam(0.003)

    def loss(net, values, target):
        logits, gates = net.logits(values, training=True)
        weights = 1 + 2 * (target != 0)
        value = (nn.losses.cross_entropy(logits, target) * weights).sum() / weights.sum()
        if gates is not None:
            value += 0.06 * ((gates.mean(axis=0) - 0.25) ** 2).sum()
        return value

    grad = nn.value_and_grad(model, loss)
    for _ in range(steps):
        indices = mx.array(rng.integers(0, len(samples), 64, dtype=np.int32))
        error, gradient = grad(model, x[indices], y[indices])
        optimizer.update(model, gradient)
        mx.eval(model.parameters(), optimizer.state, error)
    model.save_weights(str(path))
    yhat, _ = model.logits(x, training=True)
    exact = int(mx.all(yhat.argmax(axis=-1) == y, axis=-1).sum())
    return {'training_signatures_exact': exact, 'parameters': sum(int(value.size) for _, value in tree_flatten(model.parameters())),
            'loss': float(error)}


class Learned:
    def __init__(self, features, grammar, path, routed=False, rotate=False):
        self.features, self.grammar = features, grammar
        self.model = Head(features.dimension, grammar, routed)
        self.model.load_weights(str(path))
        self.rotate = rotate
        self.routes = Counter()

    def __call__(self, context):
        x = mx.array(self.features(context)[None])
        logits, gates = self.model.logits(x, rotate=self.rotate)
        if gates is not None:
            self.routes[tuple(gates)] += 1
        return self.grammar.decode(np.array(logits.argmax(axis=-1)[0]))


class Retrieval:
    def __init__(self, features, grammar, samples):
        self.features, self.grammar = features, grammar
        self.vectors = np.stack([features(ctx) for ctx, _ in samples])
        self.tokens = [grammar.targets(plan) for _, plan in samples]

    def __call__(self, context):
        vector = self.features(context)
        index = np.argmin(np.sum((self.vectors - vector) ** 2, axis=-1))
        return self.grammar.decode(self.tokens[int(index)])


class Additive:
    """A categorical linear control on exactly the same program cells."""
    def __init__(self, features, grammar, samples):
        self.features, self.grammar = features, grammar
        x = np.stack([self.vector(ctx) for ctx, _ in samples])
        labels = np.array([grammar.targets(cells) for _, cells in samples])
        targets = np.eye(grammar.width)[labels].reshape(len(x), -1)
        self.weights = x.T @ np.linalg.solve(x @ x.T + 0.05 * np.eye(len(x)), targets)

    def vector(self, ctx):
        return np.append(self.features(ctx)[128:], 1.0)

    def __call__(self, context):
        values = (self.vector(context) @ self.weights).reshape(len(self.grammar.cells), self.grammar.width)
        values += np.array(self.grammar.mask)
        return self.grammar.decode(values.argmax(axis=-1))


def feature_encoder(base, contexts):
    return Features(base, contexts, contexts)


def execute(state, event, program):
    if len(program) > 64:
        raise ValueError('Program exceeds cell budget')
    env = run(program, {'state': root_of(state)['fields'], 'event': event['args'], 'config': state['config']})
    writes = {name.removeprefix('write:'): env[name] for name, _ in program if name.startswith('write:')}
    if not writes or any(key not in FIELD_TYPES or type(value) is not FIELD_TYPES[key]
                         for key, value in writes.items()):
        raise ValueError('Missing or invalid typed writes')
    # This also rules out fractions or other non-persistable output values.
    json.dumps(writes, allow_nan=False)
    after = deepcopy(state)
    root_of(after)['fields'].update(writes)
    return after, writes


class ProgramDeck:
    def __init__(self, predict, checkpoint):
        self.predict = predict
        self.checkpoint = checkpoint

    def forward(self, state, event, tenant):
        retrieve(state, event, tenant)
        program = self.predict(signature(state, event))
        after, writes = execute(state, event, program)
        before = root_of(state)['fields']
        steps, effects = [], []
        for key, prefix in NOTIFY.items():
            if key not in before or key not in writes or before[key] == writes[key]:
                continue
            child = {'id': f"{event['id']}:{key}", 'kind': f'{prefix}.{writes[key]}',
                     'root': event['root'], 'args': {}}
            cells = self.predict(signature(after, child))
            after, change = execute(after, child, cells)
            if set(change) & NOTIFY.keys():
                raise ValueError('An internal program attempted a nested notification')
            steps.append({'event': child['kind'], 'program': cells, 'writes': change})
            for field, kind in EMISSIONS.items():
                if change.get(field) == 1:
                    identity = sha256(encode((tenant, event['root'], child['id'], self.checkpoint, kind)).encode()).hexdigest()
                    effects.append({'id': identity, 'kind': kind, 'root': event['root'], 'caused_by': child['id']})
        after.setdefault('workflow_effects', []).extend(effects)
        return {'graph': after, 'writes': writes, 'program': program, 'workflow_steps': steps,
                'workflow_effects': effects, 'checkpoint': self.checkpoint}
