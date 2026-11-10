"""Score independent Shopware checkout labels against frozen shipping programs."""
from argparse import ArgumentParser
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

import numpy as np

from flow_learning import FIELDS as FLOW_FIELDS
from graph_contract import Store, graph, root_of
from joint_deck import JointDeck, program_hash
from learn_policy import require_offline
from program_deck_sparse import load
from shipping_cases import CONTEXTS, HOLDOUT_CONTEXTS
from shipping_program import with_shipping
from shipping_rules import (BUDGETS, STRATEGIES, Hypotheses, acquire, build_checkpoint, fit, offers,
                            observed_masks, populations, read_rows, shape, write)


def measure(hyp, prior, cohort, reference, checkpoints):
    counts = {strategy: {budget: Counter() for budget in BUDGETS} for strategy in STRATEGIES}
    known = set(prior['training_masks'])
    shapes = {shape(hyp.trees[hyp.index[mask]]) for mask in known}
    coverage = Counter()
    for channel, rows in cohort.items():
        masks = observed_masks(rows)
        if known & set(masks.values()):
            raise ValueError('A test method matches a training method throughout the support grid')
        independent = reference[channel]
        for method, mask in masks.items():
            in_family = mask in hyp.index
            coverage['in_family' if in_family else 'out_of_family'] += 1
            if in_family and shape(hyp.trees[hyp.index[mask]]) not in shapes:
                coverage['unseen_shapes'] += 1
            for strategy in STRATEGIES:
                for budget in BUDGETS:
                    snapshot = checkpoints[strategy][channel][budget]
                    candidate = snapshot['methods'][method]
                    n = counts[strategy][budget]
                    remaining = candidate['remaining']
                    admitted = remaining == 1 and snapshot['price'] is not None
                    n['admitted'] += admitted
                    n['abstained'] += remaining > 1
                    n['contradicted'] += remaining == 0
                    n['ood_rejected'] += remaining == 0 and not in_family
                    n['ood_admitted'] += admitted and not in_family
                    exact = in_family and candidate.get('mask') == mask
                    n['in_family_identified'] += exact
                    n['in_family_correct_with_price'] += admitted and exact
                    n['in_family_wrong'] += in_family and remaining == 1 and not exact
        for strategy in STRATEGIES:
            for budget in BUDGETS:
                snapshot = checkpoints[strategy][channel][budget]
                n = counts[strategy][budget]
                n['price_models'] += snapshot['price'] is not None
                for row in independent:
                    predicted = offers(snapshot, row['case'])
                    expected = set(row['available'])
                    n['correct_offers'] += len(expected & predicted.keys())
                    n['false_offers'] += len(predicted.keys() - expected)
                    n['withheld_offers'] += len(expected - predicted.keys())
                    n['full_option_sets'] += predicted.keys() == expected
                    if row['quote']:
                        quote = row['quote']
                        n['exact_quotes'] += predicted.get(quote['method']) == quote['shipping']
                        n['total_quotes'] += 1
    return {'coverage': dict(coverage), 'budgets': {
        arm: {str(budget): dict(result) for budget, result in rounds.items()} for arm, rounds in counts.items()}}


def verify_offers(row, projected, contradicted):
    expected = set(row['available']) - contradicted
    if projected.keys() != expected:
        raise AssertionError({'unexpected': sorted(projected.keys() - expected),
                              'missing': sorted(expected - projected.keys())})


def new_cart(deck, tenant, row, method):
    country, group, amount, grams = row['case']
    root = tenant + ':' + ':'.join(map(str, row['case']))
    product = {'id': 'sample', 'unit': amount, 'weight': grams, 'stock': 40, 'rate': 19}
    config = {'mode': 'none', 'discount_value': 0, 'shipping_tax': 'auto', 'shipping_rate': None,
              'joint.checkpoint': deck.identity, 'shipping.country': country, 'shipping.group': group,
              'shipping.method': method}
    state = graph(tenant, root, {'mixed.catalog': [product], **dict.fromkeys(FLOW_FIELDS, 0)}, config)
    state['workflow_effects'] = []
    cart = {'kind': 'cart.add', 'root': root, 'id': root + ':cart',
            'args': {'items': [{'id': product['id'], 'quantity': 1}]}}
    return state, cart


def checkout_event(root):
    return {'kind': 'checkout.place', 'root': root, 'id': root + ':order', 'args': {}}


def replay(arm, joint, reference, database, budget):
    """Re-evaluate method eligibility from cart lines and commit through JointDeck."""
    base = load(joint)
    outcome = Counter()
    store = Store(database)
    comparisons = []
    try:
        for tenant, rows in reference.items():
            snapshot = arm[tenant][budget]
            contradicted = {key for key, result in snapshot['methods'].items() if result['remaining'] == 0}
            deck = JointDeck(with_shipping(base, snapshot))
            tested_rejection = False
            for row in rows:
                model_options = offers(snapshot, row['case'])
                verify_offers(row, model_options, contradicted)
                outcome['option_sets_checked'] += 1
                outcome['native_offers_withheld'] += len(set(row['available']) & contradicted)
                native_order = row.get('order')
                expected = native_order or row['quote']
                if not tested_rejection:
                    foreign = next((key for key in row['methods'] if key not in row['available']), None)
                    if foreign is not None:
                        try:
                            state, cart = new_cart(deck, tenant, row, foreign)
                            deck.plan(state, cart, tenant)
                        except ValueError as error:
                            if str(error) != 'Shipping method is unavailable for this cart':
                                raise
                            tested_rejection = True
                            outcome['native_unavailable_rejected'] += 1
                        else:
                            raise AssertionError('A native unavailable method produced a commit program')
                if expected is None:
                    continue
                method = expected['method']
                if method in contradicted:
                    outcome['withheld_quotes'] += 1
                    outcome['withheld_native_orders'] += native_order is not None
                    continue
                state, cart = new_cart(deck, tenant, row, method)
                root = state['root']
                store.seed(state)
                plan = deck.plan(state, cart, tenant)
                store.apply(tenant, {**cart, 'offer': program_hash(plan['program'])}, deck, 0)
                draft = store.read(tenant, root)
                quote = root_of(draft)['fields']['mixed.quote']
                offered = {key: value['shipping'] for key, value in quote['shipping_options'].items()
                           if value['available']}
                verify_offers(row, offered, contradicted)
                if offered != model_options:
                    raise AssertionError('Projection and cart used different shipping prices')
                checkout = checkout_event(root)
                plan = deck.plan(draft, checkout, tenant)
                store.apply(tenant, {**checkout, 'offer': program_hash(plan['program'])}, deck, draft['revision'])
                comparisons.append((tenant, root, expected, native_order is not None))
            if not tested_rejection:
                raise AssertionError('The shop has no native-unavailable method in its holdout')
    finally:
        store.close()
    reopened = Store(database)
    try:
        for tenant, root, expected, native_order in comparisons:
            state = reopened.read(tenant, root)
            order = root_of(state)['fields']['mixed.order']
            actual = {'method': order['shipping']['method'], 'shipping': order['shipping']['gross'],
                      'total': order['gross']}
            outcome['orders_reopened'] += 1
            outcome['orders_exact'] += actual == expected
            outcome['native_orders_compared'] += native_order
            outcome['native_orders_exact'] += native_order and actual == expected
            if actual != expected:
                raise AssertionError((actual, expected))
    finally:
        reopened.close()
    return dict(outcome)


def verify_false_accept(arm, reference, joint):
    for tenant, rows in reference.items():
        snapshot = arm[tenant][8]
        for row in rows:
            options = offers(snapshot, row['case'])
            false_options = options.keys() - set(row['available'])
            if false_options:
                deck = JointDeck(with_shipping(load(joint), snapshot))
                false_method = min(false_options)
                state, cart = new_cart(deck, tenant, row, false_method)
                draft = deck.plan(state, cart, tenant)['graph']
                place = deck.plan(draft, checkout_event(state['root']), tenant)
                if place['writes']['mixed.order']['shipping']['method'] != false_method:
                    raise AssertionError('The runtime did not use the falsely offered method')
                try:
                    verify_offers(row, options, set())
                except AssertionError:
                    return {'rejected_by_parity_check': True, 'checkout_program_generated': True,
                            'shop': row['shop'], 'case': row['case'],
                            'false_method_count': len(false_options)}
    raise AssertionError('The early false offer is missing from the evaluation')

def fixed_ensemble(hyp, cohort):
    results = []
    for seed in range(241, 257):
        by_budget = Counter()
        for rows in cohort.values():
            masks = observed_masks(rows)
            for budget, snap in acquire(hyp, np.zeros(len(hyp.masks)), rows, 'fixed', seed=seed).items():
                if snap['price'] is not None:
                    by_budget[budget] += sum(value.get('mask', -1) == masks[key]
                                             for key, value in snap['methods'].items())
        results.append(by_budget)
    return {str(b): {'min': min(x[b] for x in results), 'median': median(x[b] for x in results),
                     'max': max(x[b] for x in results)} for b in BUDGETS}


def evaluate(data, joint, output):
    hyp = Hypotheses()
    files = ('train.jsonl', 'support.jsonl', 'holdout.jsonl')
    train, support, holdout = (read_rows(data / name) for name in files)
    prior = json.loads((data / 'prior.json').read_text())
    frozen = json.loads((data / 'programs.json').read_text())
    fresh = build_checkpoint(hyp, prior, support)
    if fit(hyp, train) != prior or json.dumps(fresh, sort_keys=True) != json.dumps(
            {a: {c: {int(b): value for b, value in steps.items()} for c, steps in channels.items()}
             for a, channels in frozen.items()}, sort_keys=True):
        raise ValueError('Frozen weights or programs do not reproduce')
    cohort = populations(support, CONTEXTS)
    reference = populations(holdout, HOLDOUT_CONTEXTS)
    if cohort.keys() != reference.keys():
        raise ValueError('Support and independent observations must use the same shops')
    for channel, rows in cohort.items():
        if rows[0]['methods'] != reference[channel][0]['methods']:
            raise ValueError('Support and independent observations must use the same method IDs')
    report = measure(hyp, prior, cohort, reference, fresh)
    report['fixed_seeds_241_to_256'] = fixed_ensemble(hyp, cohort)
    report['training_methods'] = prior['training_methods']
    report['hypotheses'] = len(hyp.masks)
    report['learned_parameters'] = len(hyp.names)
    report['support_shops'] = len(cohort)
    report['independent_contexts'] = len(holdout)
    report['archive_sha256'] = {name: sha256((data / name).read_bytes()).hexdigest() for name in files}
    output.mkdir(parents=True, exist_ok=False)
    report['early_false_offer_control'] = verify_false_accept(fresh['learned'], reference, joint)
    report['joint_checkout_after_24_queries'] = {
        strategy: replay(fresh[strategy], joint, reference, output / (strategy + '.sqlite'), 24)
        for strategy in ('learned', 'uniform', 'size')}
    write(output / 'report.json', report)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('capture', type=Path)
    cli.add_argument('joint', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--teacher-port', type=int, default=55729)
    args = cli.parse_args()
    require_offline(args.teacher_port)
    evaluate(args.capture, args.joint, args.output)
    require_offline(args.teacher_port)
