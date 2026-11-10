"""Induce collection programs on Shopware traces, then evaluate new shops offline."""
from argparse import ArgumentParser
from collections import Counter
from hashlib import sha256
import gzip
import json
from pathlib import Path

import numpy as np

from flow_learning import EMIT, FIELDS, READY
from flow_transfer import verified_model
from graph_contract import Store, encode, graph, retrieve, root_of
from joined_runtime import JoinedDeck
from joint_effects import JointEffects
from learn_policy import json_lines, require_offline
from mixed_programs import (base_candidates, checkout_program, predict_order, predict_quote,
                            promo_candidates, promo_options, quote_program)


HERE = Path(__file__).resolve().parent


def checksum(path):
    return sha256(path.read_bytes()).hexdigest()


def sources():
    return {name: checksum(HERE / name) for name in
            ('mixed_transfer.py', 'mixed_programs.py', 'collection_vm.py', 'joined_runtime.py')}


def fit(name, choices, failures):
    errors = np.asarray([failures(choice) for choice in choices], dtype=np.int32)
    winners = np.flatnonzero(errors == 0)
    if len(winners) != 1:
        best = int(errors.argmin())
        raise ValueError(f'{name} has {len(winners)} exact candidates, minimum errors {int(errors[best])}, {choices[best]}')
    return -errors, int(winners[0])


def archive_absolute():
    archive = HERE.parent / 'scale/journeys.jsonl.gz'
    with gzip.open(archive, 'rt') as stream:
        samples = [r for line in stream if (r := json.loads(line))['split'] == 'train'
                   and r['coupon']['accepted'] and r['coupon']['mode'] == 'absolute']
    choices = ['cap', 'copy']
    def errors(choice):
        mismatches = 0
        for row in samples:
            line = next(item for item in row['after']['lines'] if item['type'] == 'promotion')
            goods, amount = row['before']['goods'], row['coupon']['value']
            estimate = min(goods, amount) if choice == 'cap' else amount
            mismatches += -line['total'] != estimate
        return mismatches
    return choices, fit('archived absolute amount', choices, errors), len(samples)


def train(args):
    require_offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    rows = json_lines(args.source)
    if any(row['split'] != 'train' for row in rows) or len({row['channel'] for row in rows}) < 2:
        raise ValueError('Expected teaching rows from two channels only')
    base = list(base_candidates())
    def before_errors(choice):
        program = quote_program('none', choice)
        return sum(predict_quote(program, row) != row['before'] for row in rows)

    base_weights, base_id = fit('cart and shipping', base, before_errors)
    absolute, (absolute_weights, absolute_id), old_count = archive_absolute()
    promotions = list(promo_candidates())
    def promo_errors(choice):
        programs = {mode: quote_program(mode, promo_options(mode, base[base_id], choice, absolute[absolute_id]))
                    for mode in ('percentage', 'absolute')}
        return sum(predict_quote(programs[row['config']['mode']], row) != row['after']
                   for row in rows if row['config']['mode'] in programs)

    promo_weights, promo_id = fit('promotion', promotions, promo_errors)
    observed_states = {tuple(row['order'][key] for key in ('stage', 'payment', 'delivery')) for row in rows}
    if len(observed_states) != 1:
        raise ValueError('Initial checkout state is ambiguous')
    initial = dict(zip(('stage', 'payment', 'delivery'), next(iter(observed_states)), strict=True))
    checkouts = [{'stock': 'subtract'}, {'stock': 'keep'}]
    def order_errors(choice):
        program = checkout_program(choice, initial)
        return sum(predict_order(program, row['catalog'], row['after'])[0] != row['order'] for row in rows)

    checkout_weights, checkout_id = fit('checkout', checkouts, order_errors)
    bank = {'base': base, 'promo': promotions, 'absolute': absolute, 'checkout': checkouts, 'initial': initial}
    vocab = args.output / 'programs.json'
    vocab.write_text(encode(bank) + '\n')
    weights = {'base': base_weights, 'promo': promo_weights, 'absolute': absolute_weights,
               'checkout': checkout_weights}
    np.savez_compressed(args.output / 'learned.npz', **weights, vocabulary=checksum(vocab))
    # Controlled intervention: change the preference for proportional allocation
    # while retaining the same grammar, traces and runtime.
    swapped = next(i for i, candidate in enumerate(base)
                   if candidate == {**base[base_id], 'shipping_basis': 'equal'})
    intervention = base_weights.copy()
    intervention[swapped] = int(intervention.max()) + 1
    np.savez_compressed(args.output / 'weight-control.npz', **{**weights, 'base': intervention},
                        vocabulary=checksum(vocab))
    result = {'training_shops': sorted({r['shop'] for r in rows}), 'channels': sorted({r['channel'] for r in rows}),
              'training_carts': len(rows), 'archived_single_rate_discounts': old_count,
              'runtime': sources(), 'choices': {'base': base[base_id], 'promo': promotions[promo_id],
                                               'absolute': absolute[absolute_id], 'checkout': checkouts[checkout_id]},
              'training_errors': {key: (-vals).tolist() for key, vals in weights.items()}}
    (args.output / 'training.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['choices']), flush=True)


class Programs:
    def __init__(self, vocab, path):
        candidates = json.loads(vocab.read_text())
        with np.load(path, allow_pickle=False) as model:
            if str(model['vocabulary']) != checksum(vocab):
                raise ValueError('Weights do not match the collection grammar')
            options = {key: candidates[key][int(model[key].argmax())]
                       for key in ('base', 'promo', 'absolute', 'checkout')}
        self.cart = {'none': quote_program('none', options['base'])}
        for mode in ('percentage', 'absolute'):
            self.cart[mode] = quote_program(mode, promo_options(
                mode, options['base'], options['promo'], options['absolute']))
        self.checkout = checkout_program(options['checkout'], candidates['initial'])
        self.identity = checksum(path)


class MixedDeck:
    def __init__(self, programs, joined):
        self.programs = programs
        self.joined = joined

    def forward(self, state, event, tenant):
        retrieve(state, event, tenant)
        if state['config']['mixed.checkpoint'] != self.programs.identity:
            raise ValueError('Order is pinned to another collection checkpoint')
        fields = root_of(state)['fields']
        kind = event['kind']
        if kind in ('cart.add', 'cart.promo'):
            items = event['args']['items'] if kind == 'cart.add' else fields['mixed.items']
            mode = 'none' if kind == 'cart.add' else state['config']['mode']
            program = self.programs.cart[mode]
            quote = predict_quote(program, {'catalog': fields['mixed.catalog'], 'cart_input': items,
                                             'config': state['config']})
            writes = {'mixed.items': items, 'mixed.quote': quote}
        elif kind == 'checkout.place':
            if 'order.gross' in fields:
                raise ValueError('An order already exists for this cart')
            program = self.programs.checkout
            order, updated = predict_order(program, fields['mixed.catalog'], fields['mixed.quote'])
            writes = {'mixed.catalog': updated, 'mixed.order': order,
                      **{f'order.{key}': order[key] for key in ('stage', 'payment', 'delivery', 'gross', 'net')}}
        else:
            return self.joined.forward(state, event, tenant)
        root_of(state)['fields'] = {**fields, **writes}
        return {'graph': state, 'program': [list(binding) for binding in program], 'writes': writes,
                'state_events': [], 'workflow_effects': []}


def measure(rows, programs, commerce, flow, path, *, reverse=False):
    deck = MixedDeck(programs, JoinedDeck(commerce.select, flow))
    store = Store(path)
    results = []
    try:
        for row in rows:
            root = f"mixed:{row['case']}"
            tenant = row['channel']
            initial = graph(tenant, root, {'mixed.catalog': row['catalog'], **dict.fromkeys(FIELDS, 0)},
                            {**row['config'], 'mixed.checkpoint': programs.identity, 'flow.checkpoint': flow.identity})
            initial['workflow_effects'] = []
            store.seed(initial)
            checks = {}
            calls = [('cart.add', {'items': list(reversed(row['cart_input'])) if reverse else row['cart_input']}),
                     ('cart.promo', {}), ('checkout.place', {}),
                     *((event['command'], {}) for event in row['events'])]
            seen = []
            for index, (command, args) in enumerate(calls):
                event = {'id': f'{root}:{index}', 'root': root, 'kind': command, 'args': args}
                receipt = store.apply(tenant, event, deck, index)
                saved = store.read(tenant, root)
                state = root_of(saved)['fields']
                if index < 2:
                    ref = row['before' if index == 0 else 'after']
                    checks['cart' if index == 0 else 'promotion'] = {k: state['mixed.quote'][k] == v for k, v in ref.items()}
                elif index == 2:
                    checks['order'] = {k: state['mixed.order'][k] == v for k, v in row['order'].items()}
                    store.close()
                    store = Store(path)
                    assert store.apply(tenant, event, deck, index) == receipt
                else:
                    ref = row['events'][index - 3]
                    seen.append({'event': receipt['state_events'][0]['kind'], 'expected': ref['event'],
                                 'exact': ([item['kind'] for item in receipt['state_events']] == [ref['event']]
                                           and all(state['order.' + key] == value for key, value in ref['state'].items())
                                           and state[READY] == ref['markers']['both'])})
            final = store.read(tenant, root)
            checks['flow'] = {str(i): result['exact'] for i, result in enumerate(seen)}
            results.append({'shop': row['shop'], 'case': row['case'], 'items': len(row['catalog']),
                            'mode': row['config']['mode'], 'checks': checks,
                            'exact': all(all(stage.values()) for stage in checks.values()),
                            'flow_records': len(final['workflow_effects']),
                            'expected_gross': row['order']['gross'],
                            'actual_gross': root_of(final)['fields']['mixed.order']['gross']})
    finally:
        store.close()
    failures = Counter(stage + '.' + field for row in results for stage, checks in row['checks'].items()
                       for field, correct in checks.items() if not correct)
    return {'journeys': len(results), 'exact': sum(row['exact'] for row in results),
            'fields_failed': dict(failures), 'effects': sum(row['flow_records'] for row in results), 'rows': results}


def evaluate(args):
    require_offline(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.checkpoint / 'training.json').read_text())
    if manifest['runtime'] != sources():
        raise ValueError('Collection runtime changed after learning')
    rows = json_lines(args.source)
    if len({row['channel'] for row in rows}) < 2 or any(
            row['split'] != 'holdout' or row['channel'] in manifest['channels'] for row in rows):
        raise ValueError('Expected two unseen evaluation channels')
    commerce_dir = args.lab / 'joined-result'
    commerce = JointEffects.restore(json.loads((commerce_dir / 'commerce.json').read_text()),
                                   commerce_dir / 'commerce.safetensors')
    flow_dir = args.lab / 'flow-weights'
    flow = verified_model(flow_dir, 'both', json.loads((flow_dir / 'training.json').read_text()))
    bank = args.checkpoint / 'programs.json'
    results = {'shops': sorted({r['shop'] for r in rows}), 'teacher_port_closed': args.teacher_port, 'arms': {}}
    for label, file, reverse in (('learned', 'learned.npz', False), ('reversed', 'learned.npz', True),
                                  ('weight-control', 'weight-control.npz', False)):
        programs = Programs(bank, args.checkpoint / file)
        report = measure(rows, programs, commerce, flow, args.output / f'{label}.sqlite', reverse=reverse)
        results['arms'][label] = report
        print(label, report['exact'], '/', report['journeys'], report['fields_failed'], flush=True)
    require_offline(args.teacher_port)
    (args.output / 'report.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    learn = commands.add_parser('train')
    test = commands.add_parser('evaluate')
    test.add_argument('checkpoint', type=Path)
    test.add_argument('lab', type=Path)
    for command in (learn, test):
        command.add_argument('source', type=Path)
        command.add_argument('output', type=Path)
        command.add_argument('--teacher-port', type=int, required=True)
    args = parser.parse_args()
    (train if args.command == 'train' else evaluate)(args)


if __name__ == '__main__':
    main()
