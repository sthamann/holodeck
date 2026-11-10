"""Compose learned commerce effects and learned workflow effects in one commit."""
from hashlib import sha256
import json
from pathlib import Path

import mlx.core as mx
import numpy as np

from dataflow import STATE_FIELDS
from flow_learning import EMIT, FIELDS
from flow_transfer import FlowDeck
from graph_contract import Deck, context, encode, graph, initial, literal, root_of
from joint_effects import JointEffects


ENTERED_FIELDS = {'order.payment': 'payment', 'order.delivery': 'delivery', 'order.stage': 'stage'}
ADDED_VERBS = {'order_transaction.reopen', 'order_transaction.fail', 'order_delivery.cancel'}


def extend_native_transitions(base_directory, directory):
    """Add observed native states to the existing learned program selector."""
    manifest = json.loads((base_directory / 'report.json').read_text())
    metadata = manifest['joint_effect_training']
    old = JointEffects.restore(metadata, base_directory / 'joint-effects.safetensors')
    source = Path(__file__).resolve().parents[1] / 'workspace/transitions.jsonl'
    samples = []
    for line in source.read_text().splitlines():
        row = json.loads(line)
        if row['split'] != 'train' or row['event'] not in ADDED_VERBS:
            continue
        field = STATE_FIELDS[row['event'].partition('.')[0]]
        ctx = context({field: row['before']}, {}, {'kind': row['event'], 'args': {}})
        program = {'result.accepted': literal(row['status'] == 200)}
        if row['before'] != row['after']:
            program[field] = literal(row['after'])
        samples.append((ctx, program))
    if {ctx[0][0].removeprefix('event=') for ctx, _ in samples} != ADDED_VERBS:
        raise ValueError('Missing native teaching traces for the extension')
    supplemental = JointEffects(samples, directory / 'supplemental.safetensors')
    if old.keys.keys() & supplemental.keys.keys():
        raise ValueError('Extension would overwrite existing learned behavior')
    candidates = list(old.programs)
    by_program = {encode(program): i for i, program in enumerate(candidates)}
    indices = []
    for program in supplemental.programs:
        key = encode(program)
        if key not in by_program:
            by_program[key] = len(candidates)
            candidates.append(program)
        indices.append(by_program[key])
    old_values = np.array(old.model.weight)
    extra_values = np.array(supplemental.model.weight)
    merged = np.full((len(old.keys) + len(supplemental.keys), len(candidates)), -1e6, dtype=np.float32)
    merged[:len(old.keys), :len(old.programs)] = old_values
    merged[len(old.keys):, indices] = extra_values
    if not np.array_equal(merged[:len(old.keys)].argmax(axis=1), old_values.argmax(axis=1)):
        raise AssertionError('Previously learned effect selection changed')
    if not np.array_equal(merged[len(old.keys):].argmax(axis=1),
                          np.array(indices)[extra_values.argmax(axis=1)]):
        raise AssertionError('Supplemental effect classes were remapped incorrectly')
    joined = {**metadata, 'programs': candidates,
              'keys': [*metadata['keys'], *supplemental.metadata['keys']],
              'configuration_selectors': {**old.selectors, **supplemental.selectors}}
    path = directory / 'commerce.safetensors'
    mx.save_safetensors(str(path), {'weight': mx.array(merged)})
    (directory / 'commerce.json').write_text(json.dumps(joined, ensure_ascii=False, indent=2) + '\n')
    return {'preserved_input_signatures': len(old.keys), 'new_input_signatures': len(supplemental.keys),
            'training_observations': len(samples), 'verbs': sorted(ADDED_VERBS),
            'source_sha256': sha256(source.read_bytes()).hexdigest()}


def seed(row, settings, flow):
    new = graph(row['channel'], row['product'], {**initial(row), **dict.fromkeys(FIELDS, 0)},
                {**settings, 'flow.checkpoint': flow.identity})
    new['workflow_effects'] = []
    return new


def entered(before, after, event):
    source, target = root_of(before)['fields'], root_of(after)['fields']
    # Only actual transitions of existing business objects produce state
    # enter events. Initial fields created during checkout have no old value.
    return [{'id': f"{event['id']}:{field}", 'root': event['root'],
             'kind': f'{label}.{target[field]}', 'args': {},
             'source': {'field': field, 'before': source[field], 'after': target[field]}}
            for field, label in ENTERED_FIELDS.items()
            if field in source and field in target and source[field] != target[field]]


class JoinedDeck:
    def __init__(self, commerce, flow, *, deliver=True):
        self.commerce = Deck(commerce, 'learned-commerce')
        self.flow = FlowDeck(flow)
        self.deliver = deliver

    def forward(self, state, event, tenant):
        result = self.commerce.forward(state, event, tenant)
        after = result['graph']
        transitions = entered(state, after, event)
        outputs, applied = [], []
        for child in transitions:
            if not self.deliver or child['kind'] not in self.flow.checkpoint.alphabet:
                continue
            step = self.flow.forward(after, child, tenant)
            after = step['graph']
            applied.append({'event': child['kind'], 'program': step['program']})
            if step['writes'][EMIT]:
                identity = sha256(encode((tenant, event['root'], self.flow.checkpoint.identity,
                                          child['id'])).encode()).hexdigest()
                output = {'id': identity, 'kind': 'order.mark', 'root': event['root'],
                          'caused_by': child['id'], 'checkpoint': self.flow.checkpoint.identity}
                after['workflow_effects'].append(output)
                outputs.append(output)
        return {**result, 'graph': after, 'state_events': transitions,
                'workflow_steps': applied, 'workflow_effects': outputs}
