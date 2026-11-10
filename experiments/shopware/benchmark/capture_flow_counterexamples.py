"""Resolve ambiguous temporal programs by asking Shopware valid event sequences."""
from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path

from capture_counterexamples import attach
from capture_flows import capture_sequence
from flow_learning import catalogue, observed_result


EVENTS = dict(zip('pPsSxcf', ('payment.paid', 'payment.open', 'delivery.shipped', 'delivery.open',
                              'stage.in_progress', 'delivery.cancelled', 'payment.failed'), strict=True))


def paths(length, payment_entered=False, delivery_entered=False, stage_changed=False, prefix=''):
    if length == 0:
        yield prefix
        return
    # Native command domain. A failed payment or cancelled delivery can
    # reopen. An already entered open state is not another input event.
    payments = (('P', False),) if payment_entered else (('p', True), ('f', True))
    deliveries = (('S', False),) if delivery_entered else (('s', True), ('c', True))
    for letter, after in payments:
        yield from paths(length - 1, after, delivery_entered, stage_changed, prefix + letter)
    for letter, after in deliveries:
        yield from paths(length - 1, payment_entered, after, stage_changed, prefix + letter)
    if not stage_changed:
        yield from paths(length - 1, payment_entered, delivery_entered, True, prefix + 'x')


def queries(candidates, alphabet, rows, label):
    compiled = [([alphabet.index(s['event']) for s in row['steps']],
                 [s['observed'][label] for s in row['steps']]) for row in rows]
    matches = [candidate for candidate in candidates if all(
        observed_result(candidate['automaton'], seq) == labels for seq, labels in compiled)]
    if not matches:
        raise ValueError('Observed behavior is outside the hypothesis language')
    minimum = min(len(candidate['automaton']) for candidate in matches)
    return [candidate for candidate in matches if len(candidate['automaton']) == minimum]


def shortest_distinction(groups, alphabet):
    ids = {letter: alphabet.index(name) for letter, name in EVENTS.items()}
    for length in range(1, 9):
        best_path, best_score = None, 0
        for commands in paths(length):
            events = [ids[letter] for letter in commands]
            score = 0
            for candidates in groups.values():
                if len(candidates) > 1:
                    outcomes = Counter(tuple(observed_result(c['automaton'], events)) for c in candidates)
                    score += 1 - sum((count / len(candidates)) ** 2 for count in outcomes.values())
            if score > best_score:
                best_path, best_score = commands, score
        if best_path:
            return best_path, best_score
    raise ValueError('No valid distinguishing sequence in the query budget')


def native_anchors(oracle, first):
    oracle.states = {row['id']: row['technicalName'] for row in oracle.search('state-machine-state', {'limit': 200})}
    order = oracle.search('order', {'ids': [first['native_order']], 'associations': {'tags': {}}})[0]
    order_tags = {tag['id'] for tag in order['tags']}
    tags = oracle.search('tag', {'limit': 100, 'sort': [{'field': 'createdAt', 'order': 'DESC'}]})
    names = {tag['name']: tag['id'] for tag in tags}
    for tag in tags:
        title = tag['name']
        if title.startswith('Holodeck ') and title.endswith(' both') and tag['id'] in order_tags:
            other = names.get(title.removesuffix(' both') + ' either')
            if other in order_tags:
                tagged = {'both': tag['id'], 'either': other}
                break
    else:
        raise ValueError('Cannot find the native marker pair in the source order')
    items = oracle.search('order-line-item', {'filter': [
        {'type': 'equals', 'field': 'orderId', 'value': first['native_order']}]})
    product = next(row['productId'] for row in items if row['type'] == 'product')
    return tagged, product


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('source', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--max-queries', type=int, default=8)
    args = cli.parse_args()
    rows = [json.loads(line) for line in args.source.read_text().splitlines()]
    if len({r['channel'] for r in rows}) != 1 or any(r['split'] != 'train' for r in rows):
        raise ValueError('Active queries must use the single teaching channel')
    first = rows[0]
    alphabet = sorted({s['event'] for row in rows for s in row['steps']})
    candidates = catalogue(alphabet)
    oracle = attach(first['channel'])
    tags, product = native_anchors(oracle, first)
    with args.output.open('w') as output:
        for i in range(args.max_queries + 1):
            groups = {label: queries(candidates, alphabet, rows, label) for label in ('both', 'either')}
            print(i, {label: len(options) for label, options in groups.items()}, flush=True)
            if all(len(options) == 1 for options in groups.values()):
                return
            if i == args.max_queries:
                raise ValueError('Ambiguous workflow after query budget')
            commands, score = shortest_distinction(groups, alphabet)
            print('Query', commands, score, flush=True)
            row = capture_sequence(oracle, first['shop'], product, tags, commands, 100 + i, 'train')
            row['query_disagreement'] = score
            rows.append(row)
            output.write(json.dumps(row, ensure_ascii=False) + '\n')
            output.flush()


if __name__ == '__main__':
    main()
