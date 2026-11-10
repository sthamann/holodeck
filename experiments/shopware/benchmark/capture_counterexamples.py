"""Ask the isolated teacher about carts chosen by hypothesis disagreement."""
from argparse import ArgumentParser
from itertools import product
import json
from pathlib import Path
from threading import Lock

from capture_learning import api, change_shipping, observation, seed_items
from policy_learning import minimal_consistent, truth, vocabulary


def attach(channel):
    oracle = api.Oracle.__new__(api.Oracle)
    oracle.auth_lock = Lock()
    oracle.headers = oracle.authenticate()
    oracle.channel = oracle.search('sales-channel', {'ids': [channel]})[0]
    oracle.country = oracle.search('country', {'filter': [{'type': 'equals', 'field': 'iso', 'value': 'DE'}]})[0]['id']
    oracle.salutation = oracle.search('salutation', {'limit': 1})[0]['id']
    oracle.tax = {row['taxRate']: row['id'] for row in oracle.search('tax', {'limit': 100})}
    return oracle


def rounded_tax(amount, rate):
    divisor = 100 + rate
    return (2 * amount * rate + divisor) // (2 * divisor)


def proposal_facts(goods, quantity, shipping, rate):
    unit, extra = divmod(goods, quantity)
    tax = (rounded_tax(unit * (quantity - 1), rate) + rounded_tax(unit + extra, rate)
           + rounded_tax(shipping, rate))
    return {'cart.goods': goods, 'cart.quantity': quantity, 'cart.shipping': shipping,
            'cart.gross': goods + shipping, 'cart.tax': tax, 'cart.net': goods + shipping - tax}


def proposal_space(source):
    goods = sorted({row['facts']['cart.goods'] + offset for row in source
                    for offset in (-2800, -1, 0, 1, 2800) if row['facts']['cart.goods'] + offset >= 100})
    quantity = sorted({row['facts']['cart.quantity'] + offset for row in source
                       for offset in (-1, 0, 1) if row['facts']['cart.quantity'] + offset >= 2})
    shipping = sorted({row['merchant']['shipping'] for row in source})
    rates = sorted({row['merchant']['rate'] for row in source})
    return list(product(goods, quantity, (shipping[0], shipping[-1]), rates))


def input_key(row):
    facts, merchant = row['facts'], row['merchant']
    return facts['cart.goods'], facts['cart.quantity'], merchant['shipping'], merchant['rate']


def ask_next(groups, menu, used):
    selected = None
    highest = 0
    for case in menu:
        if case in used:
            continue
        facts = proposal_facts(*case)
        score = 0
        for hypotheses in groups.values():
            n = len(hypotheses)
            if n > 1:
                yes = sum(truth(candidate, facts) for candidate in hypotheses)
                score += 4 * yes * (n - yes) / n**2
        if score > highest:
            selected, highest = case, score
    return selected, highest


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('source', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--max-queries', type=int, default=12)
    args = cli.parse_args()
    initial = [json.loads(line) for line in args.source.read_text().splitlines()]
    if len({r['channel'] for r in initial}) != 1 or any(r['split'] != 'train' for r in initial):
        raise ValueError('Active queries must use a single training channel')
    candidates = vocabulary([row['facts'] for row in initial])
    oracle = attach(initial[0]['channel'])
    rows = list(initial)
    menu = proposal_space(initial)
    used = {input_key(row) for row in initial}
    sources = {row['policy']: row for row in initial}
    with args.output.open('w') as target:
        for i in range(args.max_queries + 1):
            groups = {policy: minimal_consistent(candidates, [r for r in rows if r['policy'] == policy])
                      for policy in sources}
            print(f'After {i} active carts, remaining minimal rules:',
                  {policy: len(options) for policy, options in groups.items()}, flush=True)
            if all(len(options) == 1 for options in groups.values()):
                return
            if any(not options for options in groups.values()):
                raise ValueError('Teacher evidence falsified the predicate grammar')
            selected, score = ask_next(groups, menu, used)
            if selected is None or i == args.max_queries:
                raise ValueError('More evidence needed to distinguish the remaining programs')
            used.add(selected)
            goods, quantity, shipping, rate = selected
            change_shipping(oracle, shipping)
            for policy, reference in sources.items():
                products = seed_items(oracle, reference['shop'], goods, quantity, rate, multi=True)
                facts, actual, _ = observation(oracle, products, reference['code'], False,
                                                goods, quantity, shipping)
                row = {**reference, 'case': 100 + i, 'facts': facts, 'observed': actual,
                       'merchant': {**reference['merchant'], 'shipping': shipping, 'rate': rate},
                       'query_disagreement': score}
                rows.append(row)
                target.write(json.dumps(row, ensure_ascii=False) + '\n')
                target.flush()


if __name__ == '__main__':
    main()
