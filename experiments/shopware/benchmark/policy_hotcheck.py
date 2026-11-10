"""Verify immutable checkout receipts across hot policy activation and rollback."""
from argparse import ArgumentParser
import json
import os
from pathlib import Path
from time import perf_counter

from graph_contract import Store, graph, initial
from learn_policy import GuardedEffects, invoke, json_lines, require_offline, runtime_hashes
from policy_learning import PolicyCheckpoint


class PolicySlot:
    def __init__(self, joint):
        self.joint = joint
        self.activate('before', None)

    def activate(self, name, checkpoint):
        self.active = GuardedEffects(self.joint, checkpoint, name)

    def forward(self, state, event, tenant):
        return self.active.forward(state, event, tenant)


def purchase(store, slot, row, label):
    root = f"{row['case']}:{label}"
    store.seed(graph(row['channel'], root, initial(row), row['merchant']))
    invoke(store, slot, row, root, 'cart.add', {'quantity': row['facts']['cart.quantity']})
    _, cart, _, _ = invoke(store, slot, row, root, 'cart.promo', {'code': row['code']})
    receipt, final, event, revision = invoke(store, slot, row, root, 'checkout.place', {})
    return {'accepts': cart['cart.accepted'],
            'order': {key: final[key] for key in row['order']},
            'receipt': receipt, 'event': event, 'revision': revision, 'root': root}


def replay(store, slot, tenant, row):
    return store.apply(tenant, row['event'], slot, row['revision']) == row['receipt']


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('checkpoint', type=Path)
    cli.add_argument('holdout', type=Path)
    cli.add_argument('base', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--teacher-port', type=int, required=True)
    args = cli.parse_args()
    require_offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.checkpoint / 'training.json').read_text())
    if manifest['runtime_hashes'] != runtime_hashes():
        raise ValueError('Hot activation must use the same runtime')
    from joint_effects import JointEffects
    metadata = json.loads((args.base / 'report.json').read_text())['joint_effect_training']
    joint = JointEffects.restore(metadata, args.base / 'joint-effects.safetensors')
    vocab = args.checkpoint / 'predicate-vocabulary.json'
    conjunction = PolicyCheckpoint(vocab, args.checkpoint / 'and.npz')
    disjunction = PolicyCheckpoint(vocab, args.checkpoint / 'or.npz')
    rows = json_lines(args.holdout)
    slot = PolicySlot(joint)
    traces = []
    store = Store(args.output / 'worker.sqlite')
    try:
        for shop in sorted({r['shop'] for r in rows}):
            pairs = {(r['case'], r['policy']): r for r in rows if r['shop'] == shop and r['order']}
            and_row, or_row = next((r, pairs[(case, 'or')]) for (case, policy), r in pairs.items()
                                   if policy == 'and' and not r['observed']['cart.accepted']
                                   and pairs[(case, 'or')]['observed']['cart.accepted'])
            if and_row['facts'] != or_row['facts']:
                raise ValueError('The policy interventions require identical cart facts')
            slot.activate('before', None)
            old = purchase(store, slot, and_row, 'before')
            start = perf_counter()
            slot.activate('and', conjunction)
            activate_ms = (perf_counter() - start) * 1000
            learned = purchase(store, slot, and_row, 'learned')
            slot.activate('or', disjunction)
            swapped = purchase(store, slot, and_row, 'swapped')
            slot.activate('and', conjunction)
            rollback = purchase(store, slot, and_row, 'rollback')
            stable = all(replay(store, slot, and_row['channel'], case)
                         for case in (old, learned, swapped, rollback))
            if not (old['accepts'] and not learned['accepts'] and swapped['accepts']
                    and not rollback['accepts'] and learned['order'] == rollback['order'] == and_row['order']
                    and swapped['order'] == or_row['order'] and stable):
                raise AssertionError('The live policy or a previous receipt diverged')
            traces.append({'shop': shop, 'case': and_row['case'], 'facts': and_row['facts'],
                           'old_gross': old['order']['order.gross'], 'and_gross': learned['order']['order.gross'],
                           'or_gross': swapped['order']['order.gross'], 'rollback_gross': rollback['order']['order.gross'],
                           'receipts_unchanged': stable, 'activate_preloaded_ms': activate_ms})
    finally:
        store.close()
    require_offline(args.teacher_port)
    result = {'worker_pid': os.getpid(), 'teacher_port_closed': args.teacher_port, 'traces': traces}
    (args.output / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
