"""Adapt a predicate, restore it in a separate process, and replay blind Shopware carts."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
import socket
from time import perf_counter
from types import SimpleNamespace

from graph_contract import Store, context, encode, execute, graph, initial, retrieve, root_of, value
from graph_ir import evaluate_graph, share
from policy_learning import OnlinePosterior, PolicyCheckpoint, digest, expression, synthesize, vocabulary


HERE = Path(__file__).resolve().parent


def json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest()


def runtime_hashes():
    paths = (HERE / 'learn_policy.py', HERE / 'graph_contract.py', HERE / 'graph_ir.py',
             HERE.parent / 'core/vm.py')
    return {str(path.relative_to(HERE.parent)): fingerprint(path) for path in paths}


def require_offline(port):
    try:
        connection = socket.create_connection(('127.0.0.1', port), timeout=1)
    except ConnectionRefusedError:
        return
    with connection:
        raise RuntimeError('Shopware teacher is still accepting connections')


def train(args):
    require_offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    seed = json_lines(args.initial)
    extra = json_lines(args.counterexamples)
    observations = seed + extra
    source_channels = {row['channel'] for row in observations}
    if len(source_channels) != 1 or any(row['split'] != 'train' for row in observations):
        raise ValueError('Learning needs a single non-holdout Shopware channel')
    candidates = vocabulary([row['facts'] for row in seed])
    (args.output / 'predicate-vocabulary.json').write_text(encode(candidates) + '\n')
    report = {'train_channels': sorted(source_channels), 'train_shop': observations[0]['shop'],
              'candidate_count': len(candidates), 'grammar_hash': digest(candidates),
              'runtime_hashes': runtime_hashes(), 'weights': {}, 'observations': {},
              'symbolic_comparison': {}, 'activation': {}}
    for policy in sorted({row['policy'] for row in observations}):
        examples = [row for row in observations if row['policy'] == policy]
        if {row['observed']['cart.accepted'] for row in examples} != {True, False}:
            raise ValueError('Activation requires examples of acceptance and refusal')
        learner = OnlinePosterior(candidates)
        first = perf_counter()
        for row in examples:
            identity = f"{row['channel']}:{row['case']}:{policy}"
            learner.observe(identity, row['facts'], row['observed']['cart.accepted'])
        # A retried observation must not change learned weights or the count.
        previous = learner.weights.copy()
        learner.observe(identity, row['facts'], row['observed']['cart.accepted'])
        if not (previous == learner.weights).all():
            raise AssertionError('Retry changed the learned policy')
        checkpoint = args.output / f'{policy}.npz'
        learner.save(checkpoint)
        elapsed = perf_counter() - first
        restored = PolicyCheckpoint(args.output / 'predicate-vocabulary.json', checkpoint)
        symbolic, tied = synthesize(candidates, examples)
        report['weights'][policy] = fingerprint(checkpoint)
        report['activation'][policy] = {'evidence': restored.evidence, 'candidate': restored.candidate,
                                       'program': restored.program, 'update_and_save_ms': elapsed * 1000,
                                       'timeline': learner.timeline}
        report['observations'][policy] = len(examples)
        report['symbolic_comparison'][policy] = {'candidate': symbolic, 'minimal_tied': tied}
        print(policy, len(examples), restored.candidate, f'{elapsed:.3f}s', flush=True)
    require_offline(args.teacher_port)
    (args.output / 'training.json').write_text(json.dumps(report, indent=2) + '\n')


class GuardedEffects:
    def __init__(self, joint, policy, name):
        self.joint, self.policy, self.name = joint, policy, name

    def forward(self, state, event, tenant):
        retrieve(state, event, tenant)
        root = root_of(state)
        fields = root['fields']
        settings = state['config']
        eligible = None
        if self.policy is not None and event['kind'] == 'cart.promo':
            request = {'id': event['id'], 'root': event['root'], **event['args']}
            eligible = bool(evaluate_graph(self.policy.program,
                lambda expr: value(expr, root, state, request))['eligible'])
            if not eligible:
                settings = {key: val for key, val in settings.items() if not key.startswith('coupon.')}
        proposals = self.joint.select(context(fields, settings, event))
        if proposals is None or len(proposals) != 1:
            raise ValueError('No known effect for this event')
        result = execute(proposals[0], state, event, tenant)
        return {**result, 'policy': self.name, 'eligibility': eligible,
                'predicate': self.policy.program if eligible is not None else None}


def invoke(store, model, row, root, kind, args):
    tenant = row['channel']
    before = store.read(tenant, root)
    event = {'id': f'{root}:{kind}', 'kind': kind, 'root': root, 'args': args}
    receipt = store.apply(tenant, event, model, before['revision'])
    return receipt, root_of(store.read(tenant, root))['fields'], event, before['revision']


def measure(model, rows, path):
    store = Store(path)
    results = []
    elapsed = []
    try:
        for row in rows:
            root = f"{row['case']}:{row['policy']}"
            fields = initial(row) if row['order'] is not None else row['facts']
            store.seed(graph(row['channel'], root, fields, row['merchant']))
            restored_receipt = None
            if row['order'] is not None:
                _, cart, _, _ = invoke(store, model, row, root, 'cart.add',
                                       {'quantity': row['facts']['cart.quantity']})
                if any(cart.get(key) != target for key, target in row['facts'].items()):
                    raise AssertionError('Restored pre-trained cart differs from native starting snapshot')
            started = perf_counter()
            receipt, computed, _, _ = invoke(store, model, row, root, 'cart.promo', {'code': row['code']})
            elapsed.append((perf_counter() - started) * 1000)
            expected = row['observed']
            actual = {key: computed.get(key) for key in expected}
            order_actual = None
            if row['order'] is not None:
                order_receipt, saved, event, revision = invoke(store, model, row, root, 'checkout.place', {})
                order_actual = {key: saved.get(key) for key in row['order']}
                store.close()
                store = Store(path)
                restored_receipt = store.apply(row['channel'], event, model, revision) == order_receipt
            results.append({'shop': row['shop'], 'case': row['case'], 'reference_policy': row['policy'],
                            'facts': row['facts'], 'expected': expected, 'actual': actual,
                            'exact': actual == expected, 'applied_plan': receipt['program'],
                            'eligibility': receipt['eligibility'], 'order_expected': row['order'],
                            'order_actual': order_actual, 'same_receipt_after_restore': restored_receipt})
    finally:
        store.close()
    orders = [row for row in results if row['order_expected'] is not None]
    return {'cases': len(results), 'exact': sum(row['exact'] for row in results),
            'eligibility_exact': sum(row['expected']['cart.accepted'] == row['actual']['cart.accepted']
                                     for row in results),
            'unauthorized_discounts': sum(not row['expected']['cart.accepted'] and row['actual']['cart.accepted']
                                          for row in results),
            'orders': len(orders), 'orders_exact': sum(row['order_expected'] == row['order_actual'] for row in orders),
            'checkout_receipt_replayed': sum(row['same_receipt_after_restore'] for row in orders),
            'median_local_ms': sorted(elapsed)[len(elapsed)//2],
            'details': results}


def evaluate(args):
    require_offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    train_report = json.loads((args.checkpoint / 'training.json').read_text())
    if runtime_hashes() != train_report['runtime_hashes']:
        raise ValueError('The execution sources changed between learning and replay')
    rows = json_lines(args.holdout)
    channels = {row['channel'] for row in rows}
    if len(channels) < 2 or channels & set(train_report['train_channels']) or any(
            row['split'] != 'holdout' for row in rows):
        raise ValueError('Expected separate blind channels')
    from joint_effects import JointEffects
    base_meta = json.loads((args.base / 'report.json').read_text())['joint_effect_training']
    joint = JointEffects.restore(base_meta, args.base / 'joint-effects.safetensors')
    candidates = args.checkpoint / 'predicate-vocabulary.json'
    checkpoints = {}
    for name in ('and', 'or'):
        weights = args.checkpoint / f'{name}.npz'
        if fingerprint(weights) != train_report['weights'][name]:
            raise ValueError('The learned weights changed after checkpointing')
        checkpoints[name] = PolicyCheckpoint(candidates, weights)
    report = {'teacher_port_closed': args.teacher_port, 'heldout_shops': sorted({r['shop'] for r in rows}),
              'runtime_hashes': runtime_hashes(), 'base_weight_sha256': fingerprint(args.base / 'joint-effects.safetensors'),
              'grammar_hash': train_report['grammar_hash'], 'weight_sha256': train_report['weights'], 'arms': {}}
    for reference in ('and', 'or'):
        subset = [row for row in rows if row['policy'] == reference]
        symbolic = train_report['symbolic_comparison'][reference]['candidate']
        programs = {'before': None, 'learned': checkpoints[reference],
                    'swapped': checkpoints['or' if reference == 'and' else 'and']}
        if symbolic is not None:
            programs['symbolic'] = SimpleNamespace(program=share({'eligible': expression(symbolic)}))
        for arm, policy in programs.items():
            name = reference + '-' + arm
            measured = measure(GuardedEffects(joint, policy, name), subset,
                               args.output / f'{name}.sqlite')
            report['arms'][name] = measured
            print(name, measured['exact'], '/', measured['cases'],
                  'orders', measured['orders_exact'], '/', measured['orders'], flush=True)
    require_offline(args.teacher_port)
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


def main():
    cli = ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    teach = commands.add_parser('train')
    teach.add_argument('initial', type=Path)
    teach.add_argument('counterexamples', type=Path)
    test = commands.add_parser('evaluate')
    test.add_argument('checkpoint', type=Path)
    test.add_argument('holdout', type=Path)
    test.add_argument('base', type=Path)
    for parser in (teach, test):
        parser.add_argument('output', type=Path)
        parser.add_argument('--teacher-port', type=int, required=True)
    args = cli.parse_args()
    (train if args.command == 'train' else evaluate)(args)


if __name__ == '__main__':
    main()
