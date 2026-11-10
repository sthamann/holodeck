"""Train and replay a durable workflow on recorded native Shopware events."""
from argparse import ArgumentParser
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

import numpy as np

from flow_learning import (EMIT, FIELDS, READY, FlowCheckpoint, catalogue, compile_flow,
                           fingerprint, observed_result, replay_weights)
from graph_contract import Store, encode, graph, literal, retrieve, root_of, value
from graph_ir import GRAPH, evaluate_graph, share
from learn_policy import json_lines, require_offline


HERE = Path(__file__).resolve().parent


def sources():
    paths = (HERE / 'flow_transfer.py', HERE / 'flow_learning.py', HERE / 'graph_contract.py',
             HERE / 'graph_ir.py', HERE.parent / 'core/vm.py')
    return {str(path.relative_to(HERE.parent)): sha256(path.read_bytes()).hexdigest() for path in paths}


def train(args):
    require_offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    rows = json_lines(args.source)
    channels = {row['channel'] for row in rows}
    if len(channels) != 1 or any(row['split'] != 'train' for row in rows):
        raise ValueError('Expected one source shop without holdout rows')
    alphabet = sorted({step['event'] for row in rows for step in row['steps']})
    candidates = catalogue(alphabet)
    bank = {'alphabet': alphabet, 'candidates': candidates}
    (args.output / 'vocabulary.json').write_text(encode(bank) + '\n')
    info = {'training_channels': sorted(channels), 'training_shop': rows[0]['shop'],
            'training_sequences': len(rows), 'training_events': sum(len(r['steps']) for r in rows),
            'runtime_sources': sources(), 'vocabulary_digest': fingerprint(bank),
            'candidate_count': len(candidates), 'rules': {}}
    for label in ('both', 'either'):
        started = perf_counter()
        weights, timeline, index = replay_weights(candidates, alphabet, rows, label)
        file = args.output / f'{label}.npz'
        with file.open('xb') as output:
            np.savez_compressed(output, logits=weights, digest=fingerprint(bank))
        model = FlowCheckpoint(args.output / 'vocabulary.json', file)
        # Separately solve the exact symbolic consistency problem over the
        # same grammar, selecting the smallest reachable automaton.
        cases = [([alphabet.index(s['event']) for s in row['steps']],
                  [s['observed'][label] for s in row['steps']]) for row in rows]
        symbolic = [candidate for candidate in candidates if all(
            observed_result(candidate['automaton'], events) == tags for events, tags in cases)]
        minimum = min(len(candidate['automaton']) for candidate in symbolic)
        symbolic = [candidate for candidate in symbolic if len(candidate['automaton']) == minimum]
        if len(symbolic) != 1 or fingerprint(symbolic[0]) != fingerprint(candidates[index]):
            raise AssertionError('The independent symbolic solution disagrees')
        info['rules'][label] = {'selected': model.selected, 'program': model.program,
                               'weight_digest': model.identity, 'timeline': timeline,
                               'symbolic': symbolic[0], 'local_fit_ms': (perf_counter() - started) * 1000}
        print(label, 'states', len(model.selected['automaton']), 'remaining', timeline[-1], flush=True)
    require_offline(args.teacher_port)
    (args.output / 'training.json').write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n')


def execute_flow(program, state, event, tenant):
    retrieve(state, event, tenant)
    current = root_of(state)
    writes = evaluate_graph(program[GRAPH], lambda expr: value(expr, current, state, event['args']))
    if any(field not in FIELDS or type(result) is not FIELDS[field] or result not in (-1, 0, 1)
           for field, result in writes.items()):
        raise ValueError('Invalid workflow state effect')
    after = deepcopy(state)
    root_of(after)['fields'].update(writes)
    return {'graph': after, 'writes': writes, 'program': program}


class FlowDeck:
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint

    def forward(self, state, event, tenant):
        if state['config']['flow.checkpoint'] != self.checkpoint.identity:
            raise ValueError('Existing continuation is pinned to another checkpoint')
        token = self.checkpoint.alphabet.index(event['kind'])
        bound = {**event, 'args': {'token': token}}
        result = execute_flow(self.checkpoint.program, state, bound, tenant)
        return {**result, 'flow_checkpoint': self.checkpoint.identity}


class CurrentState:
    def __init__(self, label):
        self.label = label

    def forward(self, state, event, tenant):
        facts = event['args']['current']
        active = (facts['payment'] == 'paid', facts['delivery'] == 'shipped')
        previous = root_of(state)['fields'][READY]
        ready = int(previous or (all(active) if self.label == 'both' else any(active)))
        program = {GRAPH: share({READY: literal(ready), EMIT: literal(ready - previous)})}
        return execute_flow(program, state, event, tenant)


def root_for(row):
    # Deliberate ID collisions across tenants test isolation.
    return f"order-{row['episode']}"


def initial(row, identity):
    return graph(row['channel'], root_for(row), dict.fromkeys(FIELDS, 0), {'flow.checkpoint': identity})


def event_for(row, step, index, current=False):
    root = root_for(row)
    return {'id': f'{root}:{index}', 'root': root, 'kind': step['event'],
            'args': {'current': step['current']} if current else {}}


def metrics(details, rows):
    by_order = {(r['shop'], r['episode']): True for r in rows}
    for d in details:
        by_order[(d['shop'], d['episode'])] &= d['exact']
    return {'sequences': len(rows), 'exact_sequences': sum(by_order.values()), 'steps': len(details),
            'exact_steps': sum(d['exact'] for d in details),
            'emitted': sum(d['emitted'] for d in details),
            'expected_emissions': sum(d['expected_emitted'] for d in details),
            'duplicate_receipts': sum(d['duplicate'] for d in details), 'details': details}


def measure(rows, label, model, identity, path, current=False):
    store = Store(path)
    for row in rows:
        store.seed(initial(row, identity))
    store.close()
    details = []
    for index in range(max(len(row['steps']) for row in rows)):
        for row in rows:
            if index >= len(row['steps']):
                continue
            step = row['steps'][index]
            event = event_for(row, step, index, current)
            store = Store(path)
            try:
                receipt = store.apply(row['channel'], event, model, index)
                after = store.read(row['channel'], root_for(row))
                values = root_of(after)['fields']
                duplicate = store.apply(row['channel'], event, model, index) == receipt
            finally:
                store.close()
            details.append({'shop': row['shop'], 'episode': row['episode'], 'index': index,
                            'event': step['event'], 'current_reference': step['current'],
                            'ready': values[READY], 'expected_ready': step['observed'][label],
                            'emitted': values[EMIT], 'expected_emitted': step['delta'][label],
                            'exact': (values[READY], values[EMIT]) == (
                                step['observed'][label], step['delta'][label]),
                            'duplicate': duplicate})
    return metrics(details, rows)


def verified_model(directory, label, manifest):
    result = FlowCheckpoint(directory / 'vocabulary.json', directory / f'{label}.npz')
    if result.identity != manifest['rules'][label]['weight_digest']:
        raise ValueError('Checkpoint digest mismatch')
    return result


def evaluate(args):
    require_offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.checkpoint / 'training.json').read_text())
    if sources() != manifest['runtime_sources']:
        raise ValueError('Execution sources changed between training and evaluation')
    rows = json_lines(args.source)
    if len({r['channel'] for r in rows}) < 2 or any(
            r['channel'] in manifest['training_channels'] or r['split'] != 'holdout' for r in rows):
        raise ValueError('Expected independent blind shops')
    models = {label: verified_model(args.checkpoint, label, manifest) for label in ('both', 'either')}
    out = {'heldout_shops': sorted({row['shop'] for row in rows}), 'runtime_sources': sources(),
           'teacher_port_closed': args.teacher_port, 'arms': {}}
    for label, alternative in (('both', 'either'), ('either', 'both')):
        symbolic = manifest['rules'][label]['symbolic']
        symbolic_model = FlowCheckpoint.__new__(FlowCheckpoint)
        symbolic_model.alphabet = models[label].alphabet
        symbolic_model.identity = 'symbolic-' + label
        symbolic_model.program = compile_flow(symbolic['prototype'], symbolic_model.alphabet)
        variants = [
            ('learned', FlowDeck(models[label]), models[label].identity, False),
            ('swapped', FlowDeck(models[alternative]), models[alternative].identity, False),
            ('current', CurrentState(label), 'current-' + label, True),
            ('symbolic', FlowDeck(symbolic_model), symbolic_model.identity, False),
        ]
        for name, deck, identity, current in variants:
            arm = f'{label}-{name}'
            result = measure(rows, label, deck, identity, args.output / f'{arm}.sqlite', current)
            out['arms'][arm] = result
            print(arm, result['exact_sequences'], '/', result['sequences'],
                  'sequences', result['exact_steps'], '/', result['steps'], 'steps', flush=True)
    require_offline(args.teacher_port)
    (args.output / 'report.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')


def work(args):
    request = json.load(sys.stdin)
    manifest = json.loads((args.checkpoint / 'training.json').read_text())
    if sources() != manifest['runtime_sources']:
        raise ValueError('Unexpected worker runtime')
    model = FlowDeck(verified_model(args.checkpoint, request['label'], manifest))
    store = Store(args.db)
    try:
        receipt = store.apply(request['tenant'], request['event'], model, request['revision'])
        state = store.read(request['tenant'], request['event']['root'])
        print(json.dumps({'pid': os.getpid(), 'receipt': receipt,
                          'state': root_of(state)['fields']}))
    finally:
        store.close()


def restart(args):
    require_offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.checkpoint / 'training.json').read_text())
    if sources() != manifest['runtime_sources']:
        raise ValueError('Unexpected restart runtime')
    model = verified_model(args.checkpoint, 'both', manifest)
    alternative = verified_model(args.checkpoint, 'either', manifest)
    source = json_lines(args.source)
    # One episode with an interrupted history per shop, using deliberately
    # identical order IDs across tenants.
    rows = [{**next(row for row in source if row['shop'] == shop and row['episode'] == index),
             'episode': 0}
            for index, shop in enumerate(sorted({row['shop'] for row in source}))]
    if len(rows) != 2 or len({row['channel'] for row in rows}) != 2:
        raise ValueError('Expected two independent orders')
    db = args.output / 'restart.sqlite'
    store = Store(db)
    for row in rows:
        store.seed(initial(row, model.identity))
    store.close()
    operations = []
    original = {}
    new_policy = None
    for i in range(max(len(row['steps']) for row in rows)):
        for row in rows:
            if i >= len(row['steps']):
                continue
            step = row['steps'][i]
            request = {'tenant': row['channel'], 'event': event_for(row, step, i), 'revision': i, 'label': 'both'}
            response = worker_call(args.checkpoint, db, request)
            state = response['state']
            assert (state[READY], state[EMIT]) == (step['observed']['both'], step['delta']['both'])
            operations.append({'shop': row['shop'], 'step': i, 'event': step['event'], **response})
            original.setdefault(row['channel'], (request, response['receipt']))
            if i == 1 and new_policy is None:
                assert state[READY] == 0
                probe = {'tenant': row['channel'], 'revision': i + 1, 'label': 'either',
                         'event': {'id': 'wrong-checkpoint', 'root': root_for(row),
                                   'kind': 'stage.in_progress', 'args': {}}}
                rejected = worker_call(args.checkpoint, db, probe, expect_failure=True)
                if 'pinned to another checkpoint' not in rejected:
                    raise AssertionError('The worker accepted an incompatible checkpoint')
                seeded = {**row, 'episode': 99}
                store = Store(db)
                try:
                    unchanged = store.read(row['channel'], root_for(row))
                    assert unchanged['revision'] == i + 1 and root_of(unchanged)['fields'] == state
                    store.seed(initial(seeded, alternative.identity))
                finally:
                    store.close()
                new_request = {'tenant': row['channel'], 'event': event_for(seeded, step, 0),
                               'revision': 0, 'label': 'either'}
                active = worker_call(args.checkpoint, db, new_request)
                assert active['state'][READY] == step['observed']['either']
                assert active['state'][EMIT] == step['delta']['either']
                new_policy = {'pid': active['pid'], 'root': root_for(seeded),
                              'ready': active['state'][READY], 'old_pending_preserved': True}
    for request, receipt in original.values():
        assert worker_call(args.checkpoint, db, request)['receipt'] == receipt
    assert new_policy is not None
    assert len({operation['pid'] for operation in operations}) == len(operations)
    require_offline(args.teacher_port)
    (args.output / 'report.json').write_text(json.dumps({
        'teacher_port_closed': args.teacher_port, 'workers': len(operations),
        'replayed_prior_receipts': len(original), 'incompatible_checkpoint_rejected': True,
        'new_policy_order': new_policy, 'operations': operations}, ensure_ascii=False, indent=2) + '\n')
    print(len(operations), 'separate worker processes, exact effects and preserved receipts', flush=True)


def worker_call(checkpoint, db, request, expect_failure=False):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), 'worker', str(checkpoint), str(db)],
                            input=json.dumps(request), text=True, capture_output=True, timeout=15)
    if expect_failure and result.returncode != 0:
        return result.stderr
    result.check_returncode()
    return json.loads(result.stdout)


def main():
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    teach = commands.add_parser('train')
    test = commands.add_parser('evaluate')
    restore = commands.add_parser('restart')
    worker = commands.add_parser('worker')
    worker.add_argument('checkpoint', type=Path)
    worker.add_argument('db', type=Path)
    for command in (test, restore):
        command.add_argument('checkpoint', type=Path)
    for command in (teach, test, restore):
        command.add_argument('source', type=Path)
        command.add_argument('output', type=Path)
        command.add_argument('--teacher-port', type=int, required=True)
    args = parser.parse_args()
    {'train': train, 'evaluate': evaluate, 'restart': restart, 'worker': work}[args.command](args)


if __name__ == '__main__':
    main()
