"""Translate trace-checked teacher programs into independent VM cells."""
from hashlib import sha256
import json

from collection_vm import literal as L, record, ref as R, run
from flow_learning import FIELDS
from flow_transfer import verified_model
from graph_contract import encode, graph
from graph_ir import GRAPH, expand
from learn_policy import json_lines
from mixed_programs import rounded, tax_raw, term
from mixed_transfer import Programs


def quote_labels(plan, source):
    return dict([('catalog', R('state', 'mixed.catalog')), ('items', source), *plan,
                 ('write:mixed.quote', R('quote')), ('write:mixed.items', R('items'))])


def fixed_shipping(rate):
    shipping = R('config', 'shipping')
    return term('vector', record(rate=rate, gross=shipping, tax=rounded(tax_raw(shipping, rate))))


def without_keys(rows, name):
    return {key: {k: v for k, v in row.items() if k != name} for key, row in rows.items()}


def canonical_quote(quote):
    return {**quote, 'items': without_keys(quote['items'], 'id')}


def canonical_order(order):
    return {**order, 'lines': without_keys(order['lines'], 'id'),
            'stock': {key: value['stock'] for key, value in order['stock'].items()}}


def expected_quote(plan, row):
    return canonical_quote(run(plan, {'config': row['config'], 'catalog': row['catalog'],
                                      'items': row['cart_input']})['quote'])


def shipping_candidates(program):
    cells = dict(program)
    original = cells['shipping_bands']
    for term in (original, fixed_shipping(R('config', 'shipping_rate')),
                 fixed_shipping(L(7)), fixed_shipping(L(19))):
        yield [(name, term if name == 'shipping_bands' else expression) for name, expression in program]


def find_shipping_program(rows, ordinary):
    fixed_rows = [row for row in rows if row['config']['shipping_tax'] == 'fixed']
    if len({row['config']['shipping_rate'] for row in fixed_rows}) < 2:
        raise ValueError('Two training rates must distinguish an operand from a constant')
    errors = []
    for option in range(4):
        count = 0
        for row in fixed_rows:
            before = list(shipping_candidates(ordinary.cart['none']))[option]
            after = list(shipping_candidates(ordinary.cart[row['config']['mode']]))[option]
            count += expected_quote(before, row) != row['before']
            count += expected_quote(after, row) != row['after']
        errors.append(count)
    winners = [i for i, error in enumerate(errors) if error == 0]
    if len(winners) != 1:
        raise ValueError(f'Fixed shipping traces do not identify one candidate: {errors}')
    return winners[0], errors


def scalar_tree(expression):
    op, *args = expression
    if op == 'const':
        return L(args[0])
    if op == 'read':
        scope, field = args
        return R({'root': 'state', 'self': 'state', 'event': 'event', 'config': 'config'}[scope], field)
    operands = [scalar_tree(arg) for arg in args]
    if op == 'eq':
        a, b = operands
        return term('max', L(0), term('sub', L(1), term('max', term('sub', a, b), term('sub', b, a))))
    if op not in ('min', 'max', 'add', 'sub', 'mul'):
        raise ValueError(f'Unsupported scalar operation {op}')
    return term(op, *operands)


def flow_cells(flow, event):
    result = {'token': L(flow.alphabet.index(event))}
    for key, value in expand(flow.program[GRAPH]).items():
        tree = scalar_tree(value)
        def substitute(node):
            if node == R('event', 'token'):
                return R('token')
            if node[0] in ('ref', 'literal'):
                return node
            return [node[0], *(substitute(child) for child in node[1:])]
        result['write:' + key] = substitute(tree)
    return result


def commands(row):
    return [('cart.add', {'items': row['cart_input']}), ('cart.promo', {}), ('checkout.place', {}),
            *((step['command'], {}) for step in row['events'])]


def seed(row):
    return graph(row['channel'], f"episode:{row['case']}",
                 {'mixed.catalog': row['catalog'], **dict.fromkeys(FIELDS, 0)}, row['config'])


def cases(rows, lab):
    path = lab / 'mixed-weights-v2'
    bank = path / 'programs.json'
    ordinary = Programs(bank, path / 'learned.npz')
    choice, errors = find_shipping_program(rows, ordinary)
    flow_dir = lab / 'flow-weights'
    flow = verified_model(flow_dir, 'both', json.loads((flow_dir / 'training.json').read_text()))
    samples = []
    from program_deck import execute, signature
    for row in rows:
        state = seed(row)
        state['workflow_effects'] = []
        tax = row['config']['shipping_tax']
        for index, (kind, args) in enumerate(commands(row)):
            event = {'kind': kind, 'id': str(index), 'root': state['root'], 'args': args}
            if index < 2:
                mode = 'none' if index == 0 else row['config']['mode']
                program = ordinary.cart[mode]
                if tax == 'fixed':
                    program = list(shipping_candidates(program))[choice]
                source = R('event', 'items') if index == 0 else R('state', 'mixed.items')
                cells = quote_labels(program, source)
            elif index == 2:
                cells = dict([('catalog', R('state', 'mixed.catalog')),
                              ('quote_in', R('state', 'mixed.quote')), *ordinary.checkout])
                # Rewrite the checkout input name recursively, leaving output
                # bindings and quoted records intact.
                def rename(value):
                    if isinstance(value, list):
                        return (R('quote_in', *value[2:]) if value[:2] == R('quote')
                                else [rename(item) for item in value])
                    if isinstance(value, dict):
                        return {key: rename(item) for key, item in value.items()}
                    return value
                cells = {key: rename(expr) for key, expr in cells.items()}
                cells.update({'write:mixed.catalog': R('updated'), 'write:mixed.order': R('order'),
                              **{'write:order.' + key: R('order', key)
                                 for key in ('gross', 'net', 'stage', 'payment', 'delivery')}})
            else:
                step = row['events'][index - 3]
                before = state['objects'][0]['fields']
                changed = {'write:order.' + key: L(value) for key, value in step['state'].items()
                           if before['order.' + key] != value}
                cells = {**changed, 'write:result.accepted': L(True)}
            samples.append((signature(state, event), cells))
            after, writes = execute(state, event, list(cells.items()))
            if index < 2:
                if canonical_quote(writes['mixed.quote']) != row['before' if index == 0 else 'after']:
                    raise ValueError(f'Quote teaching graph does not match Shopware: {row["shop"]}/{row["case"]}')
            elif index == 2:
                if canonical_order(writes['mixed.order']) != row['order']:
                    raise ValueError('Checkout teaching graph does not match Shopware')
            else:
                expected = row['events'][index - 3]
                child = {'kind': expected['event'], 'id': f'{index}:state', 'root': state['root'], 'args': {}}
                child_cells = flow_cells(flow, child['kind'])
                samples.append((signature(after, child), child_cells))
                after, internal = execute(after, child, list(child_cells.items()))
                if internal['effect.ready'] != expected['markers']['both']:
                    raise ValueError('Workflow teaching graph does not match Shopware')
            state = after
    return samples, {'shipping_candidate_errors': errors, 'shipping_candidate_selected': choice,
                     'collection_source': sha256((path / 'learned.npz').read_bytes()).hexdigest(),
                     'workflow_source': flow.identity}
