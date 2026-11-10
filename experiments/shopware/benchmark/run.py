"""Train and compare all three architectures on the same Shopware holdouts."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import socket
import time

from graph_contract import Deck, Store, config, graph, initial, root_of
from lessons import VIEW_REQUESTS, expected, request_for, training, views
from models import Features, Learned, Retrieval, Vocabulary, fit


def read_rows(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    trained = [row for row in rows if row['split'] == 'train']
    holdout = [row for row in rows if row['split'] == 'holdout']
    train_ids = {row['channel'] for row in trained}
    holdout_ids = {row['channel'] for row in holdout}
    if not train_ids or len(holdout_ids) < 2 or train_ids & holdout_ids:
        raise ValueError('Need at least two completely reserved sales channels')
    return trained, holdout


def ports_closed(ports):
    for port in ports:
        try:
            connection = socket.create_connection(('127.0.0.1', port), timeout=1)
        except ConnectionRefusedError:
            continue
        with connection:
            raise RuntimeError(f'Teacher port {port} is still accepting connections')


def score_snapshots(model, rows):
    """Compare interventions on identical teacher pre-states, without error propagation."""
    counts = defaultdict(lambda: [0, 0])
    decisions = []
    for row in rows:
        fields = initial(row)
        tenant, root = row['channel'], row['product']
        for index, step in enumerate(row['events']):
            request = request_for(step, root, index)
            gold = expected(step)
            state = graph(tenant, root, fields, config(row))
            result = None
            try:
                result = model.forward(state, request, tenant)
            except (ValueError, KeyError, TypeError):
                pass
            correct = result is not None and root_of(result['graph'])['fields'] == {**fields, **gold}
            counts[request['kind']][0] += correct
            counts[request['kind']][1] += 1
            decisions.append({'shop': row['shop'], 'episode': row['episode'], 'step': index,
                              'event': request['kind'], 'correct': correct,
                              'program': result['program'] if result is not None else None})
            fields = {**fields, **gold}
    return {'by_event': dict(counts), 'decisions': decisions}


def measure(predict, name, rows, directory, *, ui_cases=None):
    db = directory / (name + '.sqlite')
    if db.exists():
        raise FileExistsError(f'Use a fresh result directory: {db}')
    store = Store(db)
    model = Deck(predict, name)
    details, ui_results, journeys = [], [], []
    cases = ui_cases or {name: (VIEW_REQUESTS[name][-1], spec) for name, spec in views().items()
                         if name in VIEW_REQUESTS}
    checkpoints = {'cart.add': 'storefront', 'checkout.place': 'checkout',
                   'order_delivery.ship': 'orders'}
    times = []
    replays = 0
    for row in rows:
        tenant, root = row['channel'], row['product']
        store.seed(graph(tenant, root, initial(row), config(row)))
        success = True
        for i, step in enumerate(row['events']):
            before = store.read(tenant, root)
            request = request_for(step, root, i)
            scope = checkpoints.get(step['event'])
            ui_result = None
            if scope:
                prompt_text, view_spec = cases[scope]
                prompt = {'id': f'{root}:view:{i}', 'root': root, 'kind': 'project',
                          'args': {'text': prompt_text}}
                try:
                    projection = model.forward(before, prompt, tenant)
                    spec = projection['writes']['holo.view']
                    fields = root_of(before)['fields']
                    verified = (set(projection['writes']) == {'holo.view'} and spec == view_spec
                                and all(column in fields for column in spec['columns'])
                                and spec['action'] == request['kind'] and spec['binding'] == 'row.root')
                    ui_result = {'shop': row['shop'], 'scope': scope, 'correct': verified,
                                 'emitted': spec, 'field_values': {f: fields.get(f) for f in spec.get('columns', [])},
                                 'root': root, 'request': prompt['args']['text']}
                    if verified:
                        # A renderer submits the model's action and the selected
                        # row identity back through the same transaction entry.
                        request = {**request, 'kind': spec['action'], 'root': root_of(before)['id']}
                except (ValueError, KeyError, TypeError) as error:
                    ui_result = {'shop': row['shop'], 'scope': scope, 'correct': False, 'error': str(error)}
            start = time.perf_counter()
            receipt = None
            error_message = None
            try:
                receipt = store.apply(tenant, request, model, before['revision'])
            except (ValueError, KeyError, TypeError) as error:
                error_message = str(error)
            times.append((time.perf_counter() - start) * 1000)
            after = store.read(tenant, root)
            expected_fields = expected(step)
            expected_snapshot = {**root_of(before)['fields'], **expected_fields}
            actual = root_of(after)['fields']
            # Values outside the observed effect must remain unchanged as well.
            ok = receipt is not None and actual == expected_snapshot
            success &= ok
            details.append({'shop': row['shop'], 'episode': row['episode'], 'step': i,
                            'event': request['kind'], 'correct': ok, 'expected': expected_fields,
                            'actual': {key: actual.get(key) for key in expected_fields},
                            'program': receipt['program'] if receipt else None,
                            'attempts': receipt['candidate_attempts'] if receipt else 0,
                            'before': root_of(before)['fields'], 'error': error_message})
            if ui_result is not None:
                ui_results.append({**ui_result, 'bound_action_correct': ui_result['correct'] and ok})
            if request['kind'] == 'checkout.place' and receipt is not None:
                store.close()
                store = Store(db)
                repeated = store.apply(tenant, request, model, before['revision'])
                if repeated != receipt or store.read(tenant, root) != after:
                    raise AssertionError('Receipt changed after reopen and duplicate delivery')
                replays += 1
        journeys.append({'shop': row['shop'], 'episode': row['episode'], 'correct': success})
    store.close()
    groups = defaultdict(lambda: [0, 0])
    for row in details:
        family = row['event'].split('.')[0]
        groups[family][0] += row['correct']
        groups[family][1] += 1
    ui_groups = defaultdict(lambda: [0, 0])
    for row in ui_results:
        ui_groups[row['scope']][0] += row['bound_action_correct']
        ui_groups[row['scope']][1] += 1
    ordered = sorted(times)
    return {'journeys': journeys, 'steps': details, 'ui': ui_results, 'by_family': dict(groups),
            'by_ui': dict(ui_groups), 'duplicate_after_reopen': replays,
            'latency_ms': {str(p): ordered[int((len(ordered)-1)*p/100)] for p in (50, 95, 99)},
            'expert_pairs': dict(Counter(str(x) for x in getattr(predict, 'routes', {}).values())),
            'fixed_prestate_diagnostic': score_snapshots(model, rows)}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--teacher-port', type=int, action='append', required=True)
    parser.add_argument('--steps', type=int, default=440)
    parser.add_argument('--weights-from', type=Path)
    parser.add_argument('--challenge', type=Path)
    args = parser.parse_args()
    ports_closed(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    train, holdout = read_rows(args.capture)
    if args.challenge:
        original_ids = {row['channel'] for row in train + holdout}
        holdout = [json.loads(line) for line in args.challenge.read_text().splitlines()]
        new_ids = {row['channel'] for row in holdout}
        if len(new_ids) < 2 or original_ids & new_ids or any(row['split'] != 'holdout' for row in holdout):
            raise ValueError('Challenge channels must be fresh and reserved')
    samples, diagnostics = training(train)
    vocab = Vocabulary(samples)
    features = Features(args.base, [ctx for ctx, _ in samples], [ctx for ctx, _ in samples])
    print('Training', len(samples), 'samples from', len(train), 'journeys;',
          len(diagnostics['label_errors']), 'unsupported trace labels', flush=True)
    weights_dir = args.weights_from or args.output
    if not args.weights_from:
        for filename, routed in (('joint.safetensors', False), ('routed.safetensors', True)):
            fit(samples, features, vocab, weights_dir / filename, routed, args.steps, seed=71)
    else:
        previous = json.loads((weights_dir / 'report.json').read_text())
        if previous['capture_sha256'] != hashlib.sha256(args.capture.read_bytes()).hexdigest():
            raise ValueError('Checkpoint requires the same original training population')
    arms = {
        'A-retrieval': Retrieval(samples, features),
        'B-joint': Learned(features, vocab, weights_dir / 'joint.safetensors'),
        'C-routed': Learned(features, vocab, weights_dir / 'routed.safetensors', routed=True),
        'B-untrained': Learned(features, vocab, None),
        'C-swapped-experts': Learned(features, vocab, weights_dir / 'routed.safetensors', routed=True, reroute=True),
    }
    report = {'capture_sha256': hashlib.sha256(args.capture.read_bytes()).hexdigest(),
              'base_revision': args.base.name,
              'teacher_ports_closed': args.teacher_port,
              'challenge_sha256': hashlib.sha256(args.challenge.read_bytes()).hexdigest() if args.challenge else None,
              'weights_from': str(weights_dir),
              'weight_sha256': {stem: hashlib.sha256((weights_dir / (stem + '.safetensors')).read_bytes()).hexdigest()
                                for stem in ('joint', 'routed')},
              'train_shops': sorted({row['shop'] for row in train}),
              'held_out_shops': sorted({row['shop'] for row in holdout}),
              'train_journeys': len(train), 'holdout_journeys': len(holdout),
              'training_pairs': len(samples), 'steps_per_fit': None if args.weights_from else args.steps,
              'diagnostics': diagnostics,
              'arms': {}}
    for name, arm in arms.items():
        # All arms begin with the same activation cache. No program is cached.
        features.cache = {ctx: features.cache[ctx] for ctx, _ in samples}
        ports_closed(args.teacher_port)
        result = measure(arm, name, holdout, args.output)
        report['arms'][name] = result
        print(name, 'journeys', sum(row['correct'] for row in result['journeys']), '/', len(result['journeys']),
              'steps', result['by_family'], 'views with action', result['by_ui'], flush=True)
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    ports_closed(args.teacher_port)


if __name__ == '__main__':
    main()
