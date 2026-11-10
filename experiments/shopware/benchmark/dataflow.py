"""Learned composition of shared numeric regions and independent field bindings."""
import json
from pathlib import Path

from composition import Atoms
from graph_contract import FIELD_TYPES, context, encode, literal
from graph_ir import ARITY, GRAPH, share


# A schema association identifies the state of the object addressed by the
# event. It says nothing about allowed transitions or their effects.
STATE_FIELDS = {'order': 'order.stage', 'order_transaction': 'order.payment',
                'order_delivery': 'order.delivery'}


def focused(ctx):
    tags, text = ctx
    entity, _, action = tags[0].removeprefix('event=').partition('.')
    if entity not in STATE_FIELDS or not action:
        return ctx
    prefix = f'record.{STATE_FIELDS[entity]}='
    return (tags[0], *[tag for tag in tags if tag.startswith(prefix)]), text


def include_native_transitions(samples):
    """Add missing addressed-state cases from recorded Shopware status probes."""
    recorded = Path(__file__).resolve().parents[1] / 'workspace/transitions.jsonl'
    existing = {focused(ctx) for ctx, _ in samples}
    learned_verbs = {ctx[0][0].removeprefix('event=') for ctx, _ in samples}
    archive = []
    for line in recorded.read_text().splitlines():
        row = json.loads(line)
        event = row['event']
        if row['split'] != 'train' or event not in learned_verbs:
            continue
        entity, _, action = event.partition('.')
        # Cancel and reopen can also affect stock. Only complete captured
        # journeys are used to teach these particular effects.
        if entity == 'order' and action in ('cancel', 'reopen'):
            continue
        field = STATE_FIELDS[entity]
        raw = context({field: row['before']}, {}, {'kind': event, 'args': {}})
        identity = focused(raw)
        if identity in existing:
            continue
        program = {'result.accepted': literal(row['status'] == 200)}
        if row['before'] != row['after']:
            program[field] = literal(row['after'])
        archive.append((raw, program))
        existing.add(identity)
    return archive


class ScopedAtoms(Atoms):
    @staticmethod
    def shared_context(ctx):
        return Atoms.shared_context(focused(ctx))


def separate(program):
    numeric = {field: expression for field, expression in program.items()
               if FIELD_TYPES[field] in (int, bool)}
    if not any(expression[0] in ARITY for expression in numeric.values()):
        return None, program
    return share(numeric), {key: expr for key, expr in program.items() if key not in numeric}


class GraphAtoms(ScopedAtoms):
    def __init__(self, samples):
        parts = [(ctx, *separate(plan)) for ctx, plan in samples]
        self.regions = [None, *[json.loads(text) for text in sorted({
            encode(region) for _, region, _ in parts if region is not None})]]
        self.by_region = {encode(region): i for i, region in enumerate(self.regions)}
        super().__init__([(ctx, leaves) for ctx, _, leaves in parts])

    def target(self, plan):
        region, leaves = separate(plan)
        return (*super().target(leaves), self.by_region[encode(region)])

    def decode(self, outputs):
        region = self.regions[int(outputs[-1][0].argmax())]
        ordinary = outputs[:-1]
        if region is None:
            return super().decode(ordinary)
        # The selected region owns these writes. The output grammar permits
        # one producer for each destination, irrespective of the domain.
        import mlx.core as mx
        mask = mx.array([field not in region['outputs'] for field in self.fields])[None, :]
        scores, *heads = ordinary
        plan = super().decode((mx.where(mask, scores, -1e6), *heads))
        return {**plan, GRAPH: region}
