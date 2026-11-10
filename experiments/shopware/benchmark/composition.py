"""Decode expressions and UI bindings as individual learned choices."""
from hashlib import blake2b
import json
import re

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
import numpy as np

from graph_contract import FIELD_TYPES, encode, literal
from models import Controller, Features

ARITY = {'add': 2, 'sub': 2, 'mul': 2, 'muldiv': 3, 'min': 2, 'max': 2,
         'eq': 2, 'le': 2, 'select': 3}


def linearize(expression):
    operation, *operands = expression
    if operation in ('read', 'const'):
        return [encode(expression)]
    if operation not in ARITY or len(operands) != ARITY[operation]:
        raise ValueError(f'Unsupported expression {expression}')
    return [encode(operation), *(item for child in operands for item in linearize(child))]


def reconstruct(tokens):
    stream = iter(tokens)

    def parse():
        entry = json.loads(next(stream))
        if isinstance(entry, list):
            return entry
        return [entry, *[parse() for _ in range(ARITY[entry])]]

    try:
        result = parse()
        if next(stream, None) is not None:
            raise ValueError('Extra nodes after the expression')
        return result
    except (StopIteration, KeyError, TypeError) as error:
        raise ValueError('Incomplete expression') from error


class TextFeatures(Features):
    """The same frozen encoder and word features are used by all comparison arms."""
    WIDTH = 256

    def __init__(self, base, contexts, samples):
        super().__init__(base, contexts, samples)
        self.dimension += self.WIDTH
        self.words = {}

    def __call__(self, ctx):
        text = ctx[1]
        if text not in self.words:
            words = re.findall(r'\w+', text.casefold())
            terms = words + [' '.join(pair) for pair in zip(words, words[1:])]
            vector = np.zeros(self.WIDTH, dtype=np.float32)
            for term in terms:
                h = int.from_bytes(blake2b(term.encode(), digest_size=4).digest(), 'little')
                vector[h % self.WIDTH] = 1
            self.words[text] = vector
        return np.concatenate((super().__call__(ctx), self.words[text]))


class Atoms:
    def __init__(self, samples):
        self.fields = [field for field in FIELD_TYPES if field != 'holo.view']
        expressions = [linearize(expr) for _, plan in samples for field, expr in plan.items()
                       if field != 'holo.view']
        self.tokens = [None, *sorted({token for seq in expressions for token in seq})]
        self.by_token = {token: i for i, token in enumerate(self.tokens)}
        self.slots = max(map(len, expressions))
        views = [plan['holo.view'][1] for _, plan in samples if 'holo.view' in plan]
        self.columns = [field for field in self.fields if any(field in view['columns'] for view in views)]
        self.actions = [None, *sorted({view['action'] for view in views if view['action'] is not None})]
        self.bindings = [None, *sorted({view['binding'] for view in views if view['binding'] is not None})]
        self.kinds = [None, *sorted({view['kind'] for view in views})]
        self.output_keys = ['argument.' + field.rsplit('.', 1)[-1] for field in self.fields]

    def field_evidence(self, ctx):
        tags, _ = ctx
        return np.array([float(key in tags) for key in self.output_keys], dtype=np.float32)

    @staticmethod
    def shared_context(ctx):
        tags, text = ctx
        # Each output receives its own argument presence below. The shared
        # network cannot infer co-occurring write sets from argument presence.
        return tuple(tag for tag in tags if not tag.startswith('argument.')), text

    def target(self, plan):
        presence = np.array([field in plan for field in self.fields], dtype=np.int32)
        tokens = np.zeros((len(self.fields), self.slots), dtype=np.int32)
        for idx, field in enumerate(self.fields):
            if field in plan:
                seq = [self.by_token[t] for t in linearize(plan[field])]
                tokens[idx, :len(seq)] = seq
        view = plan.get('holo.view', [None, None])[1]
        if view is None:
            return presence, tokens, 0, [0] * len(self.columns), 0, 0, 0
        return (presence, tokens, self.kinds.index(view['kind']),
                [int(field in view['columns']) for field in self.columns],
                self.columns.index(view['sort']), self.actions.index(view['action']),
                self.bindings.index(view['binding']))

    def decode(self, outputs):
        presence, nodes, kind, cols, sort, action, binding = [np.array(x[0]) for x in outputs]
        plan = {}
        for i, field in enumerate(self.fields):
            if presence[i] <= 0:
                continue
            selected = []
            needed = 1
            for row in nodes[i]:
                token = self.tokens[int(row[1:].argmax()) + 1]
                selected.append(token)
                item = json.loads(token)
                needed += (ARITY[item] if isinstance(item, str) else 0) - 1
                if needed == 0:
                    break
            if needed:
                raise ValueError('Expression did not close within the grammar budget')
            plan[field] = reconstruct(selected)
        component = self.kinds[int(kind.argmax())]
        if component is not None:
            plan['holo.view'] = literal({
                'kind': component,
                'columns': [field for field, score in zip(self.columns, cols, strict=True) if score > 0],
                'sort': self.columns[int(sort.argmax())],
                'action': self.actions[int(action.argmax())],
                'binding': self.bindings[int(binding.argmax())],
            })
        return plan


class FactorController(Controller):
    WIDTH = 40

    def __init__(self, dim, atoms, routed=False):
        super().__init__(dim, None, routed)
        self.presence = nn.Linear(96, len(atoms.fields))
        self.local_presence = mx.zeros((len(atoms.fields),))
        self.fields = nn.Linear(96, len(atoms.fields) * self.WIDTH)
        self.positions = nn.Linear(96, atoms.slots * self.WIDTH)
        self.token = nn.Linear(self.WIDTH, len(atoms.tokens))
        self.kind = nn.Linear(96, len(atoms.kinds))
        self.columns = nn.Linear(96, len(atoms.columns))
        self.column_skip = nn.Linear(dim, len(atoms.columns), bias=False)
        self.sort = nn.Linear(96, len(atoms.columns))
        self.action = nn.Linear(96, len(atoms.actions))
        self.binding = nn.Linear(96, len(atoms.bindings))
        if hasattr(atoms, 'regions'):
            self.region = nn.Linear(96, len(atoms.regions))

    def outputs(self, x, evidence, atoms, training=False, reroute=False):
        shared, routes = self.hidden(x, training, reroute)
        field = self.fields(shared).reshape(-1, len(atoms.fields), 1, self.WIDTH)
        position = self.positions(shared).reshape(-1, 1, atoms.slots, self.WIDTH)
        nodes = self.token(mx.tanh(field + position))
        columns = self.columns(shared) + self.column_skip(x)
        written = self.presence(shared) + 4 * evidence * self.local_presence
        outputs = (written, nodes, self.kind(shared), columns,
                   self.sort(shared), self.action(shared), self.binding(shared))
        if hasattr(atoms, 'regions'):
            outputs = (*outputs, self.region(shared))
        return outputs, routes


def train(samples, features, atoms, path, routed, steps, seed=71):
    mx.random.seed(seed)
    rng = np.random.default_rng(seed)
    x = mx.array(np.array([features(atoms.shared_context(ctx)) for ctx, _ in samples]))
    evidence = mx.array(np.array([atoms.field_evidence(ctx) for ctx, _ in samples]))
    y = tuple(mx.array(np.array(part, dtype=np.int32)) for part in zip(
        *(atoms.target(program) for _, program in samples), strict=True))
    ui_indices = np.array([i for i, (_, p) in enumerate(samples) if 'holo.view' in p], dtype=np.int32)
    vm_indices = np.array([i for i, (_, p) in enumerate(samples) if 'holo.view' not in p], dtype=np.int32)
    model = FactorController(features.dimension, atoms, routed)
    update = optim.Adam(learning_rate=0.003)

    def loss(net, x, evidence, target):
        outputs, gates = net.outputs(x, evidence, atoms, training=True)
        presence, tokens, kind, columns, sort, action, binding, *regions = target
        written, nodes, type_scores, shown, sort_scores, action_scores, bind_scores, *region_scores = outputs
        write_loss = nn.losses.binary_cross_entropy(written, presence.astype(mx.float32), with_logits=True)
        weights = (tokens != 0) + 0.22 * (tokens == 0)
        mask = presence[..., None] * weights
        node_loss = (nn.losses.cross_entropy(nodes, tokens) * mask).sum() / (mask.sum() + 1e-8)
        type_loss = nn.losses.cross_entropy(type_scores, kind).mean()
        view = kind != 0
        column_loss = (nn.losses.binary_cross_entropy(shown, columns.astype(mx.float32), with_logits=True,
                                                      reduction='none') * (1 + columns)).mean(axis=-1)
        options = (column_loss + nn.losses.cross_entropy(sort_scores, sort)
                   + nn.losses.cross_entropy(action_scores, action) + nn.losses.cross_entropy(bind_scores, binding))
        result = write_loss + node_loss + type_loss + (options * view).sum() / (view.sum() + 1e-8)
        if regions:
            result += nn.losses.cross_entropy(region_scores[0], regions[0]).mean()
        if gates is not None:
            result += 0.08 * ((gates.mean(axis=0) - 0.25) ** 2).sum()
        return result

    grad = nn.value_and_grad(model, loss)
    for _ in range(steps):
        ids = mx.array(np.concatenate((rng.choice(vm_indices, 18), rng.choice(ui_indices, 18))))
        error, gradients = grad(model, x[ids], evidence[ids], tuple(a[ids] for a in y))
        update.update(model, gradients)
        mx.eval(model.parameters(), update.state, error)
    model.save_weights(str(path))
    print(f'{path.name} steps={steps} loss={float(error):.5f}', flush=True)


class Composed:
    def __init__(self, features, atoms, checkpoint, routed=False, reroute=False):
        self.features, self.atoms = features, atoms
        self.net = FactorController(features.dimension, atoms, routed)
        self.net.load_weights(str(checkpoint))
        self.reroute = reroute
        self.routes = {}

    def __call__(self, ctx):
        shared = mx.array(self.features(self.atoms.shared_context(ctx)))[None, :]
        evidence = mx.array(self.atoms.field_evidence(ctx))[None, :]
        outputs, chosen = self.net.outputs(shared, evidence, self.atoms,
                                           reroute=self.reroute)
        if chosen is not None:
            self.routes[encode(ctx)] = chosen
        return [self.atoms.decode(outputs)]
