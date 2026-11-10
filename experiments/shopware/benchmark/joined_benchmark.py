"""Verify joined native commerce and workflow through one durable execution path."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from browser_probe import SCOPE_EVENTS, click, page
from composition import Composed, TextFeatures
from composition_lessons import ViewLessons, quantity_program, reserved_cases
from dataflow import ScopedAtoms, include_native_transitions
from flow_learning import FIELDS, READY
from flow_transfer import verified_model
from graph_contract import Store, config, encode, root_of
from joined_runtime import JoinedDeck, extend_native_transitions, seed
from joint_effects import Coupled, JointEffects
from learn_policy import json_lines, require_offline
from lessons import expected, request_for, training


def restore_projection(lab, base):
    saved = json.loads((lab / 'dataflow-confirmed/report.json').read_text())
    rows = []
    for filename, checksum in saved['source_sha256'].items():
        path = lab / filename
        if sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError('The frozen projection vocabulary requires the original teaching traces')
        rows.extend(row for row in json_lines(path) if row['split'] == 'train')
    quantity, _ = quantity_program(rows)
    examples, diagnostics = training(rows, quantity=quantity, ui_samples=ViewLessons())
    if diagnostics['label_errors']:
        raise ValueError('The original commerce training set is invalid')
    examples += include_native_transitions(examples)
    unique = {(ctx, encode(plan)): plan for ctx, plan in examples}
    samples = [(ctx, plan) for (ctx, _), plan in unique.items()]
    contexts = [ctx for ctx, _ in samples]
    features = TextFeatures(base, [], contexts)
    path = lab / 'dataflow01/scoped-tree.safetensors'
    if sha256(path.read_bytes()).hexdigest() != saved['weights_sha256']['scoped-tree.safetensors']:
        raise ValueError('Projection weights changed')
    return Composed(features, ScopedAtoms(samples), path), {row['channel'] for row in rows}


def observe(store, model, row, step, index, label, request=None):
    tenant, root = row['channel'], row['product']
    before = store.read(tenant, root)
    event = request or request_for(step, root, index)
    receipt = store.apply(tenant, event, model, before['revision'])
    after = store.read(tenant, root)
    status = [item['kind'] for item in receipt['state_events']]
    target_events = [step['native_event']] if step.get('native_event') else []
    old_fields = {k: v for k, v in root_of(before)['fields'].items() if k not in FIELDS}
    after_fields = root_of(after)['fields']
    actual = {k: v for k, v in after_fields.items() if k not in FIELDS}
    target = expected(step)
    marker = step.get('markers', {}).get(label, 0)
    delta = step.get('delta', {}).get(label, 0)
    outbox_size = len(after['workflow_effects'])
    return {
        'shop': row['shop'], 'episode': row['episode'], 'step': index, 'command': event['kind'],
        'commerce_exact': actual == {**old_fields, **target},
        'event_exact': status == target_events, 'actual_events': status, 'native_events': target_events,
        'workflow_exact': (after_fields[READY], len(receipt['workflow_effects']), outbox_size) == (marker, delta, marker),
        'ready': after_fields[READY], 'expected_ready': marker, 'emitted': len(receipt['workflow_effects']),
        'expected_emitted': delta, 'outbox_size': outbox_size,
        'actual_commerce': {key: actual.get(key) for key in target}, 'expected_commerce': target,
    }, receipt, event, before['revision']


def exact(result):
    return all(result[key] for key in ('commerce_exact', 'event_exact', 'workflow_exact'))


def measure(rows, model, flow, label, path):
    store = Store(path)
    for row in rows:
        store.seed(seed(row, config(row), flow))
    store.close()
    details = []
    for index in range(max(len(row['events']) for row in rows)):
        for row in rows:
            if index >= len(row['events']):
                continue
            store = Store(path)
            try:
                result, receipt, event, revision = observe(store, model, row, row['events'][index], index, label)
            finally:
                store.close()
            store = Store(path)
            try:
                unchanged = store.apply(row['channel'], event, model, revision) == receipt
            finally:
                store.close()
            result['duplicate_receipt'] = unchanged
            if not unchanged:
                raise AssertionError('Repeating a committed command changed the receipt')
            details.append(result)
    return {'journeys': len(rows), 'complete_journeys': sum(
        all(exact(step) for step in details if (step['shop'], step['episode']) == (row['shop'], row['episode']))
        for row in rows), 'steps': len(details),
        'commerce_exact': sum(s['commerce_exact'] for s in details),
        'event_exact': sum(s['event_exact'] for s in details),
        'workflow_exact': sum(s['workflow_exact'] for s in details),
        'state_events': sum(len(s['actual_events']) for s in details),
        'effects': sum(s['emitted'] for s in details), 'expected_effects': sum(s['expected_emitted'] for s in details),
        'duplicate_receipts': sum(s['duplicate_receipt'] for s in details), 'details': details}


def assert_rollback(store, model, row, event):
    tenant, root = row['channel'], row['product']
    before = store.read(tenant, root)
    # Trigger after the graph UPDATE and at receipt INSERT. The existing
    # transaction kernel must revert the commerce write and the flow output.
    store.db.execute("CREATE TEMP TRIGGER joined_fault BEFORE INSERT ON receipts "
                     "BEGIN SELECT RAISE(ABORT, 'joined rollback probe'); END")
    try:
        try:
            store.apply(tenant, event, model, before['revision'])
        except sqlite3.IntegrityError as error:
            if 'joined rollback probe' not in str(error):
                raise
        else:
            raise AssertionError('Forced persistence failure did not abort')
    finally:
        store.db.execute('DROP TRIGGER joined_fault')
    assert store.read(tenant, root) == before
    assert not store.db.execute('SELECT 1 FROM receipts WHERE tenant=? AND event_id=?',
                                (tenant, event['id'])).fetchone()


def browser_chain(chrome, model, flow, row, directory):
    directory.mkdir()
    db = directory / 'same-order.sqlite'
    store = Store(db)
    tenant, root = row['channel'], row['product']
    cases = reserved_cases()
    by_event = {event: scope for scope, event in SCOPE_EVENTS.items()}
    clicks, details, rollback = {}, [], False
    try:
        store.seed(seed(row, config(row), flow))
        for index, step in enumerate(row['events']):
            scope = by_event.get(step['event'])
            if scope and scope not in clicks:
                before = store.read(tenant, root)
                prompt = {'id': 'view', 'root': root, 'kind': 'project', 'args': {'text': cases[scope][0]}}
                view = model.forward(before, prompt, tenant)['writes']['holo.view']
                if view != cases[scope][1] or view['binding'] != 'row.root':
                    raise AssertionError(f"{row['shop']} projected an incorrect {scope} view: {view}")
                event = {'id': f'{root}:{index}:browser', 'root': root, 'kind': view['action'], 'args': step['args']}
                if scope == 'orders' and step['delta']['both'] == 1:
                    assert_rollback(store, model, row, event)
                    rollback = True

                def apply():
                    checked, receipt, _, _ = observe(store, model, row, step, index, 'both', event)
                    if not exact(checked):
                        raise ValueError(f'Incorrect browser effect: {checked}')
                    details.append(checked)
                    clicks[scope] = {'action': view['action'], 'columns': view['columns'],
                                     'event': receipt['event'], 'workflow_effects': receipt['workflow_effects']}

                rendered = click(chrome, page(view, root_of(before)['fields'], root), root, apply)
                (directory / f'{scope}.html').write_text(rendered)
            else:
                checked, _, _, _ = observe(store, model, row, step, index, 'both')
                if not exact(checked):
                    raise AssertionError(checked)
                details.append(checked)
            store.close()
            store = Store(db)
        final = store.read(tenant, root)
        if set(clicks) != set(cases) or not rollback or len(final['workflow_effects']) != 1:
            raise AssertionError('Browser did not exercise the complete joined path')
        return {'shop': row['shop'], 'order': root, 'native_order': row['native_order'],
                'verified_steps': len(details), 'browser': clicks, 'rollback_verified': rollback,
                'final_fields': root_of(final)['fields'], 'outbox': final['workflow_effects'],
                'details': details}
    finally:
        store.close()


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('lab', type=Path)
    cli.add_argument('base_model', type=Path)
    cli.add_argument('capture', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--teacher-port', type=int, required=True)
    cli.add_argument('--chrome', type=Path, required=True)
    args = cli.parse_args()
    require_offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    extension = extend_native_transitions(args.lab / 'dataflow-confirmed', args.output)
    metadata = json.loads((args.output / 'commerce.json').read_text())
    selector = JointEffects.restore(metadata, args.output / 'commerce.safetensors')
    projection, train_channels = restore_projection(args.lab, args.base_model)
    commerce = Coupled(projection, selector)
    flow_dir = args.lab / 'flow-weights'
    flow_manifest = json.loads((flow_dir / 'training.json').read_text())
    models = {label: verified_model(flow_dir, label, flow_manifest) for label in ('both', 'either')}
    rows = json_lines(args.capture)
    actual_ids = {row['channel'] for row in rows}
    if len(actual_ids) < 2 or actual_ids & (train_channels | set(flow_manifest['training_channels'])):
        raise ValueError('The joined evaluation needs new independent shops')
    out = {'teacher_port_closed': args.teacher_port,
           'shops': sorted({r['shop'] for r in rows}), 'extension': extension,
           'commercial_checkpoint_sha256': sha256((args.output / 'commerce.safetensors').read_bytes()).hexdigest(),
           'flow_checkpoints': {name: model.identity for name, model in models.items()}, 'arms': {}}
    for name, actual, expected_label, deliver in (
        ('both', 'both', 'both', True), ('either', 'either', 'either', True),
        ('swapped', 'either', 'both', True), ('muted', 'both', 'both', False),
    ):
        model = JoinedDeck(commerce, models[actual], deliver=deliver)
        result = measure(rows, model, models[actual], expected_label, args.output / f'{name}.sqlite')
        out['arms'][name] = result
        print(name, result['complete_journeys'], '/', result['journeys'], 'steps', result['steps'],
              'commercial', result['commerce_exact'], 'events', result['event_exact'],
              'flow', result['workflow_exact'], 'effects', result['effects'], flush=True)
        (args.output / 'report.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    model = JoinedDeck(commerce, models['both'])
    out['browser'] = [browser_chain(args.chrome, model, models['both'], row, args.output / f'browser-{i}')
                      for i, row in enumerate((r for r in rows if r['episode'] == 0), start=1)]
    require_offline(args.teacher_port)
    (args.output / 'report.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    print('Browser and rollback verified in', [row['shop'] for row in out['browser']], flush=True)


if __name__ == '__main__':
    main()
