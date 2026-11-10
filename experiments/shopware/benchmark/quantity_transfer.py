"""Fit one progression from Cart or Storefront labels and score its other readout."""
from argparse import ArgumentParser
from hashlib import sha256
from itertools import combinations, product
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from collection_vm import evaluate, literal as L, record, ref as R
from flow_learning import FIELDS as FLOW_FIELDS
from graph_contract import Store, graph, root_of
from joint_deck import JointDeck, program_hash
from learn_policy import require_offline
from program_deck_sparse import load
from quantity_program import FIELDS, MODES, install, options, project, values_for, with_quantity


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def native_options(control):
    if control is None:
        return []
    return list(range(control['min'], control['max'] + 1, control['step']))


def bounds(products):
    """Enumerate simple typed expressions, merging identical observed vectors."""
    leaves = [R('product', field) for field in FIELDS if field != 'closeout'] + [L(1)]
    candidates = [(1, leaf) for leaf in leaves]
    candidates += [(3, [op, left, right])
                   for op, (left, right) in product(('min', 'max'), combinations(leaves, 2))]
    candidates += sorted(((1 + left_size + right_size,
                           ['select_value', R('product', 'closeout'), left, right])
                          for (left_size, left), (right_size, right) in product(candidates, repeat=2)
                          if left != right), key=lambda pair: pair[0])
    seen = set()
    for cost, tree in candidates:
        vector = tuple(evaluate(tree, {'product': item}) for item in products)
        if vector not in seen:
            seen.add(vector)
            yield cost, tree, vector


def fit(source, supervision, destination):
    install()
    observations = rows(source)
    forbidden = 'storefront' if supervision == 'cart' else 'cart'
    if not observations or any(row['split'] != 'train' or forbidden in row or supervision not in row
                               for row in observations):
        raise ValueError('The fit input must contain only its designated training labels')
    products = [row['product'] for row in observations]
    bound_candidates = list(bounds(products))
    positive = [entry for entry in bound_candidates if entry[0] == 1 and min(entry[2]) > 0]
    best_cost, winners, tested = None, [], 0
    for (a, start, starts), (b, end, ends), (c, stride, strides) in product(
            positive, bound_candidates, positive):
        cost = a + b + c
        if best_cost is not None and cost > best_cost:
            continue
        possible = [dict(start=lo, end=hi, stride=step) for lo, hi, step in zip(starts, ends, strides, strict=True)]
        domain = record(start=start, end=end, stride=stride)
        modes = []
        if supervision == 'storefront':
            tested += 1
            if all(list(options(grid)) == native_options(row['storefront'])
                   for grid, row in zip(possible, observations, strict=True)):
                modes = list(MODES)
        else:
            for mode in MODES:
                tested += 1
                if all(project(grid, event['request'], mode) == event['quantity']
                       for grid, row in zip(possible, observations, strict=True) for event in row['cart']):
                    modes.append(mode)
        if not modes:
            continue
        if best_cost is None or cost < best_cost:
            best_cost, winners = cost, []
        winners.append({'domain': domain, 'compatible_modes': modes})
    if not winners:
        raise ValueError('No compatible progression in the bounded grammar')
    result = {'supervision': supervision, 'source_sha256': digest(source),
              'training_channels': sorted({row['channel'] for row in observations}),
              'products': len(products), 'cost': best_cost, 'tested': tested, 'exact_domains': len(winners),
              'domain': winners[0]['domain'], 'compatible_modes': winners[0]['compatible_modes'],
              'mode': winners[0]['compatible_modes'][0] if len(winners[0]['compatible_modes']) == 1 else None}
    with destination.open('x') as file:
        file.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


def split(source, directory):
    observations = rows(source)
    directory.mkdir(parents=True, exist_ok=True)
    for label, withheld in (('cart', 'storefront'), ('storefront', 'cart')):
        with (directory / (label + '.jsonl')).open('x') as file:
            for row in observations:
                file.write(json.dumps({k: v for k, v in row.items()
                                       if k not in {withheld, 'storefront_path'}}) + '\n')


def import_profiles(source, destination):
    shops = {}
    for row in rows(source):
        shop, product_row = row['shop'], dict(row['product'])
        name = product_row.pop('name')
        profile = shops.setdefault(shop.lower(), {'name': shop, 'tenant': row['channel'], 'catalog': [],
            'config': {'shipping': 575, 'mode': 'none', 'discount_value': 0,
                       'shipping_tax': 'auto', 'shipping_rate': None},
            'code': '', 'names': {}})
        if profile['tenant'] != row['channel'] or product_row['id'] in profile['names']:
            raise ValueError('Inconsistent product or sales channel')
        profile['catalog'].append(product_row)
        profile['names'][product_row['id']] = name
    with destination.open('x') as file:
        file.write(json.dumps(shops, indent=2) + '\n')


def replay_cart(observations, baseline, fit_path, database):
    deck = JointDeck(with_quantity(load(baseline), fit_path))
    store = Store(database)
    exact, empty_blocked, misses = 0, 0, []
    try:
        for row in observations:
            tenant, product_row = row['channel'], row['product']
            catalog = {key: value for key, value in product_row.items() if key != 'name'}
            identity = product_row['id']
            for probe in row['cart']:
                root = f"{identity}:{probe['request']}"
                state = graph(tenant, root, {'mixed.catalog': [catalog], **dict.fromkeys(FLOW_FIELDS, 0)},
                              {'mode': 'none', 'discount_value': 0, 'shipping': 575,
                               'shipping_tax': 'auto', 'shipping_rate': None, 'joint.checkpoint': deck.identity})
                state['workflow_effects'] = []
                store.seed(state)
                request = {'id': root + ':add', 'root': root, 'kind': 'cart.add',
                           'args': {'items': [{'id': identity, 'quantity': probe['request']}]}}
                plan = deck.plan(state, request, tenant)
                store.apply(tenant, {**request, 'offer': program_hash(plan['program'])}, deck, 0)
                saved = store.read(tenant, root)
                fields = root_of(saved)['fields']
                line = fields['mixed.quote']['items'].get(identity)
                quantity = line['quantity'] if line else 0
                items = [{'id': identity, 'quantity': quantity}] if quantity else []
                matched = quantity == probe['quantity'] and fields['mixed.items'] == items
                exact += matched
                if not matched:
                    misses.append({'product': product_row['name'], 'request': probe['request'],
                                   'expected': probe['quantity'], 'actual': quantity})
                if not quantity:
                    try:
                        deck.plan(saved, {'id': root + ':order', 'root': root,
                                          'kind': 'checkout.place', 'args': {}}, tenant)
                    except ValueError as error:
                        if 'bestellbare Menge' not in str(error):
                            raise
                        empty_blocked += 1
                    else:
                        raise AssertionError('An empty cart produced a checkout program')
    finally:
        store.close()
    return {'cart_exact': exact, 'empty_checkout_blocked': empty_blocked, 'misses': misses}


def stock_control(observations, baseline, fit_path, destination):
    with TemporaryDirectory() as temp:
        checkpoint = json.loads(fit_path.read_text())
        checkpoint['domain'][1]['end'] = R('product', 'maximum')
        path = Path(temp) / 'control.json'
        path.write_text(json.dumps(checkpoint))
        result = replay_cart(observations, baseline, path, destination)
        result['storefront_exact'] = sum(list(values_for(checkpoint['domain'], row['product'])) ==
                                         native_options(row['storefront']) for row in observations)
        return result


def evaluate_holdout(source, fits, destination, runtime_checkpoint=None):
    install()
    observations = rows(source)
    if not observations or any(row['split'] != 'holdout' for row in observations):
        raise ValueError('Expected a separate holdout partition')
    channels = {row['channel'] for row in observations}
    report = {'holdout_sha256': digest(source), 'products': len(observations), 'shops': len(channels),
              'cart_requests': sum(len(row['cart']) for row in observations), 'models': {}}
    cart_fit = None
    for path in fits:
        model = json.loads(path.read_text())
        if channels & set(model['training_channels']):
            raise ValueError('Training and holdout channels overlap')
        if model['supervision'] == 'cart':
            cart_fit = path
        by_mode = {mode: 0 for mode in MODES}
        controls, misses, ambiguous, correct_consensus, disagreements = 0, [], 0, 0, 0
        for row in observations:
            grid = evaluate(model['domain'], {'product': row['product']})
            predicted = list(values_for(model['domain'], row['product']))
            expected = native_options(row['storefront'])
            controls += predicted == expected
            if predicted != expected:
                misses.append({'shop': row['shop'], 'product': row['product']['name'],
                               'actual': predicted, 'expected': expected})
            for event in row['cart']:
                results = {mode: project(grid, event['request'], mode) for mode in MODES}
                for mode, value in results.items():
                    by_mode[mode] += value == event['quantity']
                allowed = {results[mode] for mode in model['compatible_modes']}
                ambiguous += len(allowed) != 1
                correct_consensus += allowed == {event['quantity']}
                disagreements += event['quantity'] not in allowed
        report['models'][model['supervision']] = {'storefront_exact': controls, 'storefront_misses': misses,
            'cart_exact_by_mode': by_mode, 'cart_ambiguous': ambiguous, 'cart_exact_consensus': correct_consensus,
            'cart_no_compatible_outcome': disagreements, 'fit': model}
    if runtime_checkpoint is not None:
        if cart_fit is None:
            raise ValueError('Runtime comparison requires the cart-trained checkpoint')
        report['runtime'] = replay_cart(observations, runtime_checkpoint, cart_fit,
                                        destination.with_suffix('.sqlite'))
        report['without_stock_dependency'] = stock_control(observations, runtime_checkpoint, cart_fit,
                                                            destination.with_suffix('.control.sqlite'))
    with destination.open('x') as file:
        file.write(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('mode', choices=('split', 'fit', 'evaluate', 'import'))
    cli.add_argument('source', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--supervision', choices=('cart', 'storefront'))
    cli.add_argument('--fit', type=Path, action='append', default=[])
    cli.add_argument('--runtime-checkpoint', type=Path)
    cli.add_argument('--teacher-port', type=int, default=55729)
    args = cli.parse_args()
    if args.mode == 'split':
        split(args.source, args.output)
    elif args.mode == 'fit' and args.supervision:
        fit(args.source, args.supervision, args.output)
    elif args.mode == 'import':
        import_profiles(args.source, args.output)
    elif args.mode == 'evaluate' and args.fit:
        require_offline(args.teacher_port)
        evaluate_holdout(args.source, args.fit, args.output, args.runtime_checkpoint)
    else:
        cli.error('Provide --supervision for fit and --fit for evaluation')
