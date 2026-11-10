"""Fit joint effects at the semantic boundary observed by the model."""
from collections import defaultdict
from itertools import combinations
import json

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim

from dataflow import STATE_FIELDS, focused, separate
from graph_contract import encode
from graph_ir import GRAPH


def event_name(ctx):
    return ctx[0][0].removeprefix('event=')


def is_status(event):
    return event.partition('.')[0] in STATE_FIELDS


def facts(ctx):
    return {tag.split('=', 1)[0]: tag.split('=', 1)[1] for tag in ctx[0]
            if tag.startswith('setting.') and '=' in tag}


def with_region(plan):
    region, leaves = separate(plan)
    return {**leaves, GRAPH: region} if region is not None else plan


class JointEffects:
    """One learned categorical decision owns a coherent graph or state effect."""

    def __init__(self, samples, path):
        groups = defaultdict(list)
        for ctx, program in samples:
            event = event_name(ctx)
            if is_status(event) or GRAPH in with_region(program):
                groups[event].append((ctx, encode(with_region(program))))
        self.selectors = {}
        keyed = {}
        for event, observations in groups.items():
            if is_status(event):
                keys = [focused(ctx)[0] for ctx, _ in observations]
            else:
                available = sorted({key for ctx, _ in observations for key in facts(ctx)})
                selectors = (columns for n in range(len(available) + 1) for columns in combinations(available, n))
                for selected in selectors:
                    possible = [(event, *(facts(ctx).get(key, '') for key in selected)) for ctx, _ in observations]
                    if len(set(zip(possible, [value for _, value in observations]))) == len(set(possible)):
                        self.selectors[event] = selected
                        keys = possible
                        break
                else:
                    raise ValueError(f'Conflicting numeric evidence for {event}')
            for key, (_, plan) in zip(keys, observations, strict=True):
                if keyed.setdefault(key, plan) != plan:
                    raise ValueError(f'Observed effects disagree for model input {key}')
        self.events = set(groups)
        self.keys = {key: i for i, key in enumerate(sorted(keyed))}
        choices = sorted(set(keyed.values()))
        self.programs = [json.loads(choice) for choice in choices]
        index = {plan: i for i, plan in enumerate(choices)}
        target = mx.array([index[keyed[key]] for key in self.keys])
        mx.random.seed(813)
        model = nn.Embedding(len(self.keys), len(self.programs))
        x = mx.arange(len(self.keys))
        optimizer = optim.Adam(0.045)

        def loss(net):
            return nn.losses.cross_entropy(net(x), target).mean()

        gradients = nn.value_and_grad(model, loss)
        for _ in range(240):
            value, grad = gradients(model)
            optimizer.update(model, grad)
            mx.eval(model.parameters(), optimizer.state, value)
        if not bool(mx.all(model(x).argmax(axis=-1) == target)):
            raise ValueError('Joint effect weights do not reconstruct the training classes')
        model.save_weights(str(path))
        self.model = nn.Embedding(len(self.keys), len(self.programs))
        self.model.load_weights(str(path))
        self.rotated_model = nn.Embedding(len(self.keys), len(self.programs))
        self.rotated_model.update({'weight': mx.roll(self.model.weight, 1, axis=-1)})
        self.metadata = {'keys': [list(key) for key in self.keys], 'programs': self.programs,
                         'configuration_selectors': self.selectors, 'training_loss': float(value),
                         'ablation_logit_column_roll': 1}

    def select(self, ctx, rotate=False):
        event = event_name(ctx)
        if event not in self.events:
            return None
        if is_status(event):
            key = focused(ctx)[0]
        else:
            options = facts(ctx)
            key = (event, *(options.get(name, '') for name in self.selectors[event]))
        if key not in self.keys:
            raise ValueError('This effect input was outside the learned scope')
        head = self.rotated_model if rotate else self.model
        decision = int(head(mx.array(self.keys[key])).argmax())
        return [self.programs[decision]]

    @classmethod
    def restore(cls, metadata, path):
        """Load the trained selector without the observation or training process."""
        restored = cls.__new__(cls)
        restored.keys = {tuple(key): i for i, key in enumerate(metadata['keys'])}
        restored.events = {key[0].removeprefix('event=') for key in restored.keys}
        restored.selectors = metadata['configuration_selectors']
        restored.programs = metadata['programs']
        restored.model = nn.Embedding(len(restored.keys), len(restored.programs))
        restored.model.load_weights(str(path))
        restored.rotated_model = nn.Embedding(len(restored.keys), len(restored.programs))
        restored.rotated_model.update({'weight': mx.roll(restored.model.weight, 1, axis=-1)})
        restored.metadata = metadata
        return restored


class Coupled:
    def __init__(self, composed, joint, rotate=False):
        self.composed = composed
        self.joint = joint
        self.rotate = rotate

    def __call__(self, ctx):
        result = self.joint.select(ctx, self.rotate)
        return self.composed(ctx) if result is None else result
