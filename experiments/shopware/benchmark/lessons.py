"""Offline induction and labels. The serving path never imports Shopware."""
import gzip
import json
from pathlib import Path
import sys

from graph_contract import FIELD_TYPES, config, context, execute, graph, initial, literal, read

SHOPWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SHOPWARE / 'qwen'))
from prepare import fit_laws

Q = lambda field: read('root', field)
R = lambda field: read('config', field)
A = lambda field: read('event', field)


def compile_arithmetic():
    with gzip.open(SHOPWARE / 'scale/journeys.jsonl.gz', 'rt') as source:
        rows = [r for line in source if (r := json.loads(line))['split'] == 'train']
    # Reuse the prior Shopware-trace compiler. Select the base rounding
    # placement by observing both line and cart amounts in these same traces.
    def tax(gross, rate):
        denominator = 100 + rate
        return (2 * gross * rate + denominator) // (2 * denominator)

    tax_candidates = (
        ('combined', lambda r, b: tax(b['gross'], r)),
        ('separate', lambda r, b: tax(b['goods'], r) + tax(b['shipping'], r)),
    )
    matches = [name for name, fun in tax_candidates
               if all(fun(r['rate'], r['before']) == r['before']['tax'] for r in rows)]
    if len(matches) != 1:
        raise ValueError('The training traces do not identify the base rounding placement')
    return fit_laws(rows), matches[0], len(rows)


def regular(rounding, quantity=None):
    quantity = A('quantity') if quantity is None else quantity
    goods = ['mul', Q('product.price'), quantity]
    shipping = R('shipping')
    gross = ['add', goods, shipping]
    def tax(amount):
        return ['muldiv', amount, R('rate'), ['add', literal(100), R('rate')]]
    taxes = (tax(gross) if rounding == 'combined' else
             ['add', tax(goods), tax(shipping)])
    return {'cart.quantity': quantity, 'cart.goods': goods, 'cart.gross': gross,
            'cart.shipping': shipping, 'cart.tax': taxes, 'cart.net': ['sub', gross, taxes]}


def discounted(laws, settings):
    # The label compiler may know the training mode. At inference the
    # controller must choose the program from the input configuration.
    policy = settings.get('coupon.mode') if settings.get('coupon.active') else 'none'
    inputs = {'g': Q('cart.goods'), 'b': Q('cart.gross'), 't': Q('cart.tax'),
              'r': R('rate'), 'v': literal(0) if policy == 'none' else R('coupon.value')}

    def lower(expr):
        if isinstance(expr, str):
            return inputs[expr]
        if isinstance(expr, int):
            return literal(expr)
        return [expr[0], *(lower(part) for part in expr[1:])]

    for register, expr in zip(('d', 'x', 'a', 'c', 'j', 'n'), laws[policy], strict=True):
        inputs[register] = lower(expr)
    return {'cart.discount': inputs['d'], 'cart.goods': inputs['a'],
            'cart.gross': inputs['c'], 'cart.tax': inputs['j'],
            'cart.net': inputs['n'], 'cart.accepted': literal(policy != 'none')}


def expected(step):
    kind, observed = step['event'], step['observed']
    if kind.startswith('catalog.'):
        return {'product.' + key: value for key, value in observed.items()}
    if kind.startswith('cart.'):
        return {'cart.' + key: value for key, value in observed.items()}
    if kind == 'checkout.place':
        return {('product.' if key == 'stock' else 'order.') + key: value
                for key, value in observed.items()}
    return {({'accepted': 'result.', 'stock': 'product.'}.get(key, 'order.')) + key: value
            for key, value in observed.items()}


def label(step, fields, settings, laws, rounding, quantity=None):
    kind = step['event']
    target = expected(step)
    if kind == 'catalog.edit':
        return {'product.' + key: A(key) for key in step['args']}
    if kind == 'cart.add':
        return regular(rounding, quantity)
    if kind == 'cart.promo':
        return discounted(laws, settings)
    if kind == 'checkout.place':
        return {'order.gross': Q('cart.gross'), 'order.net': Q('cart.net'),
                'product.stock': ['sub', Q('product.stock'), Q('cart.quantity')],
                **{f'order.{name}': literal(target[f'order.{name}'])
                   for name in ('stage', 'delivery', 'payment')}}
    program = {key: literal(result) for key, result in target.items()
               if key == 'result.accepted' or fields.get(key) != result}
    if 'product.stock' in program:
        before, quantity = fields['product.stock'], fields['cart.quantity']
        operators = [op for op, after in (('add', before + quantity), ('sub', before - quantity))
                     if after == target['product.stock']]
        if len(operators) != 1:
            raise ValueError('Observed inventory effect has no unique relative program')
        program['product.stock'] = [operators[0], Q('product.stock'), Q('cart.quantity')]
    return program


VIEW_REQUESTS = {
    'catalog': ('Produktliste mit Bestand sortieren', 'Zeige mir die Artikelverwaltung',
                'Welche Artikel sind noch auf Lager?', 'Ich brauche die Übersicht der Waren im Lager'),
    'orders': ('Zeig mir die Bestellungen und öffne den Versand', 'Versandtisch mit Bestellliste',
               'Ich will die bezahlten Aufträge zum Verschicken sehen', 'Welche Aufträge kann ich abschicken?'),
    'storefront': ('Zeig mir ein Produkt zum Einkaufen', 'Artikel ansehen und in den Warenkorb legen',
                   'Produktdetails und Kaufoptionen', 'Was kann ich hier in meinen Warenkorb legen?'),
    'checkout': ('Zum Checkout gehen und bestellen', 'Zeig mir die Bestellübersicht und Kasse',
                 'Warenkorb abschließen und bezahlen', 'Wie kann ich den Einkauf jetzt abschließen?'),
}


def views():
    native = [json.loads(line) for line in (SHOPWARE / 'workspace/ui.jsonl').read_text().splitlines()]
    view_specs = {}
    for scope, mapping in (
        ('catalog', {'Name': 'product.name', 'In stock': 'product.stock'}),
        ('orders', {'Total': 'order.gross', 'Order status': 'order.stage',
                    'Payment status': 'order.payment', 'Delivery status': 'order.delivery'}),
    ):
        observed = [row for row in native if row['source'] == scope]
        grid = next(row for row in observed if row['kind'] == 'grid')
        sort = next(row for row in observed if row['kind'] == 'sort')
        action = next((row['event'] for row in observed if row['kind'] == 'command'), None)
        view_specs[scope] = {'kind': grid['role'], 'columns': [
            mapping[col['label']] for col in grid['headers'] if col['label'] in mapping],
            'sort': mapping[sort['label']], 'action': action, 'binding': 'row.root' if action else None}
    interactions = [json.loads(line) for line in (SHOPWARE / 'scale/ui.jsonl').read_text().splitlines()]
    cart = next(row['event'] for row in interactions if row['event'] == 'cart.add' and row['verified'])
    view_specs['storefront'] = {'kind': 'table', 'columns': ['product.name', 'product.price'],
                               'sort': 'product.price', 'action': cart, 'binding': 'row.root'}
    # The checkout layout/action binding are annotations grounded in the
    # observed cart/order pair. The archived browser data stops at the cart.
    view_specs['checkout'] = {'kind': 'table', 'columns': ['cart.goods', 'cart.discount',
                                                          'cart.shipping', 'cart.gross'],
                              'sort': 'cart.gross', 'action': 'checkout.place', 'binding': 'row.root'}
    return view_specs


def request_for(step, root, ordinal):
    return {'id': f'{root}:{ordinal}', 'kind': step['event'], 'root': root, 'args': step['args']}


def training(rows, *, quantity=None, ui_samples=None):
    laws, rounding, archived = compile_arithmetic()
    samples = []
    view_specs = views()
    errors = []
    for row in rows:
        fields, settings = initial(row), config(row)
        def add_views(*names):
            if ui_samples is not None:
                samples.extend(ui_samples(fields, settings, names))
                return
            for name in names:
                for text in VIEW_REQUESTS[name][:-1]:
                    q = {'kind': 'project', 'args': {'text': text}}
                    samples.append((context(fields, settings, q), {'holo.view': literal(view_specs[name])}))

        add_views('catalog', 'storefront')
        for i, step in enumerate(row['events']):
            request = request_for(step, row['product'], i)
            program = label(step, fields, settings, laws, rounding, quantity)
            assert set(program) <= FIELD_TYPES.keys()
            actual = execute(program, graph(row['channel'], row['product'], fields, settings),
                             request, row['channel'])['writes']
            gold = expected(step)
            projected = {**fields, **actual}
            if any(projected.get(k) != v for k, v in gold.items()):
                errors.append({'shop': row['shop'], 'event': step['event'], 'actual': actual, 'target': gold})
            else:
                samples.append((context(fields, settings, request), program))
            fields.update(gold)
            if step['event'] == 'cart.promo':
                add_views('checkout')
            if step['event'] == 'order_transaction.paid' and fields.get('result.accepted'):
                add_views('catalog', 'orders')
    return samples, {'archive_compiler_rows': archived, 'rounding': rounding, 'label_errors': errors}
