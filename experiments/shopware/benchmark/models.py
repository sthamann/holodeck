"""Frozen Qwen retrieval, a joint controller, and a learned routed controller."""
import json
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
import numpy as np

from graph_contract import FIELD_TYPES, encode


class Features:
    def __init__(self, base, contexts, samples):
        from mlx_lm import load

        self.tags = sorted({tag for tags, _ in samples for tag in tags})
        self.dimension = len(self.tags) + 129
        self.model, self.tokenizer = load(str(base))
        self.hidden = self.model.language_model.model
        self.cache = {}
        rng = np.random.default_rng(12)
        self.projection = mx.array(rng.standard_normal((4096, 128), dtype=np.float32) / 64)
        self.warm(contexts)

    def warm(self, contexts):
        remaining = sorted(set(contexts) - self.cache.keys())
        for start in range(0, len(remaining), 8):
            group = remaining[start:start + 8]
            tokens = [self.tokenizer.encode('Shop state: ' + ', '.join(tags) + '. ' + text)
                      for tags, text in group]
            width = max(map(len, tokens))
            padded = mx.array([row + [0] * (width - len(row)) for row in tokens])
            hidden = self.hidden(padded)
            vector = hidden[mx.arange(len(tokens)), mx.array([len(row) - 1 for row in tokens])]
            compressed = vector.astype(mx.float32) @ self.projection
            compressed /= mx.sqrt(mx.mean(compressed ** 2, axis=-1, keepdims=True)) + 1e-8
            for entry, dense in zip(group, np.array(compressed), strict=True):
                tags, text = entry
                one_hot = np.array([float(t in tags) for t in self.tags], dtype=np.float32)
                self.cache[entry] = np.concatenate((dense * 0.3, one_hot, [float(bool(text))])).astype(np.float32)

    def __call__(self, ctx):
        if ctx not in self.cache:
            self.warm([ctx])
        return self.cache[ctx]


class Vocabulary:
    def __init__(self, samples):
        self.fields = list(FIELD_TYPES)
        self.choices = [[None, *[json.loads(expr) for expr in sorted({
            encode(program[field]) for _, program in samples if field in program})]]
                        for field in self.fields]
        self.width = max(map(len, self.choices))
        self.mask = mx.array([[0 if i < len(choices) else -1e6 for i in range(self.width)]
                              for choices in self.choices])
        self.index = [{encode(expr): i for i, expr in enumerate(choices)}
                      for choices in self.choices]

    def encode(self, program):
        return [index[encode(program.get(field))] for field, index in zip(self.fields, self.index, strict=True)]

    def decode(self, tokens):
        return {field: choices[int(token)]
                for field, choices, token in zip(self.fields, self.choices, tokens, strict=True)
                if int(token) != 0}


class Retrieval:
    def __init__(self, samples, features):
        unique = {(ctx, encode(program)): program for ctx, program in samples}
        self.features = features
        self.contexts = [pair[0] for pair in unique]
        self.programs = list(unique.values())
        self.vectors = np.array([features(ctx) for ctx in self.contexts])

    def __call__(self, ctx):
        distance = np.sum((self.vectors - self.features(ctx)) ** 2, axis=-1)
        seen = set()
        programs = []
        for i in np.argsort(distance, kind='stable'):
            program = self.programs[i]
            key = encode(program)
            if key in seen:
                continue
            seen.add(key)
            programs.append(program)
            if len(programs) == 3:
                break
        return programs


class Controller(nn.Module):
    def __init__(self, dim, vocab, routed=False):
        super().__init__()
        self.input = nn.Linear(dim, 96)
        if vocab is not None:
            self.out = nn.Linear(96, len(vocab.fields) * vocab.width)
        self.routed = routed
        self.experts = [nn.Sequential(nn.Linear(96, 32 if routed else 64), nn.Tanh(),
                                      nn.Linear(32 if routed else 64, 96))
                        for _ in range(4 if routed else 1)]
        if routed:
            self.router = nn.Linear(96, 4)

    def hidden(self, x, training=False, reroute=False):
        shared = mx.tanh(self.input(x))
        routes = None
        if self.routed:
            gates = mx.softmax(self.router(shared), axis=-1)
            if training:
                # Hard two-expert forward pass. Soft gates supply the gradient.
                cutoff = mx.sort(gates, axis=-1)[..., -2:-1]
                keep = gates >= cutoff
                sparse = gates * keep
                sparse /= sparse.sum(axis=-1, keepdims=True)
                weights = gates + mx.stop_gradient(sparse - gates)
                residual = sum(expert(shared) * weights[..., i:i + 1]
                               for i, expert in enumerate(self.experts))
                routes = gates
            else:
                order = np.argsort(-np.array(gates[0]), kind='stable')[:2]
                routes = order.tolist()
                indices = [(int(i) + int(reroute)) % len(self.experts) for i in order]
                weights = gates[:, order.tolist()]
                weights /= weights.sum()
                residual = sum(self.experts[i](shared) * weights[:, pos:pos + 1]
                               for pos, i in enumerate(indices))
        else:
            residual = self.experts[0](shared)
        return mx.tanh(shared + residual), routes

    def scores(self, x, vocab, training=False, reroute=False):
        hidden, routes = self.hidden(x, training, reroute)
        logits = self.out(hidden)
        return logits.reshape(-1, len(vocab.fields), vocab.width) + vocab.mask, routes


def fit(samples, features, vocab, path, routed, steps, seed):
    mx.random.seed(seed)
    rng = np.random.default_rng(seed)
    x = mx.array(np.array([features(ctx) for ctx, _ in samples]))
    y = mx.array(np.array([vocab.encode(program) for _, program in samples], dtype=np.int32))
    model = Controller(x.shape[-1], vocab, routed)
    update = optim.Adam(learning_rate=0.003)
    # Every retained output slot participates, including the explicit no-op.
    def loss(net, x, y):
        logits, gates = net.scores(x, vocab, training=True)
        weights = 1 + 2 * (y != 0)
        supervised = (nn.losses.cross_entropy(logits, y) * weights).sum() / weights.sum()
        if gates is None:
            return supervised
        return supervised + 0.08 * ((gates.mean(axis=0) - 0.25) ** 2).sum()

    grad = nn.value_and_grad(model, loss)
    result = None
    for i in range(steps):
        indices = mx.array(rng.integers(0, len(samples), 96, dtype=np.int32))
        result, gradients = grad(model, x[indices], y[indices])
        update.update(model, gradients)
        mx.eval(model.parameters(), update.state, result)
    model.save_weights(str(path))
    print(f'{path.name} steps={steps} loss={float(result):.5f}', flush=True)


class Learned:
    def __init__(self, features, vocab, path, routed=False, reroute=False, seed=71):
        mx.random.seed(seed)
        self.features, self.vocab = features, vocab
        self.model = Controller(features.dimension, vocab, routed)
        if path is not None:
            self.model.load_weights(str(path))
        self.reroute = reroute
        self.routes = {}

    def __call__(self, ctx):
        scores, chosen = self.model.scores(mx.array(self.features(ctx))[None, :], self.vocab,
                                           reroute=self.reroute)
        indices = np.array(scores.argmax(axis=-1)[0])
        if chosen is not None:
            self.routes[encode(ctx)] = chosen
        return [self.vocab.decode(indices)]
