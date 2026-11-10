"""Build compositional labels from native UI controls and Shopware trace deltas."""
from itertools import combinations

from graph_contract import FIELD_TYPES, context, literal
from lessons import A, Q, views


NAMES = {
    'product.name': 'Name', 'product.price': 'Preis', 'product.stock': 'Bestand',
    'cart.goods': 'Warenwert', 'cart.discount': 'Rabatt',
    'cart.shipping': 'Versandkosten', 'cart.gross': 'Gesamtbetrag',
    'order.gross': 'Auftragswert', 'order.stage': 'Auftragsstatus',
    'order.payment': 'Zahlungsstatus', 'order.delivery': 'Lieferstatus',
}
SCOPE_FIELDS = {
    'catalog': ('product.name', 'product.stock'),
    'storefront': ('product.name', 'product.price', 'product.stock'),
    'checkout': ('cart.goods', 'cart.discount', 'cart.shipping', 'cart.gross'),
    'orders': ('order.gross', 'order.stage', 'order.payment', 'order.delivery'),
}
ACTIONS = {
    'catalog': 'Artikelverwaltung', 'storefront': 'In den Warenkorb legen',
    'checkout': 'Bestellung abschließen', 'orders': 'Bestellung versenden',
}


def quantity_program(rows):
    """Discriminate operand copy from a bounded request using only training deltas."""
    values = []
    for row in rows:
        stock = row['initial']['stock']
        for step in row['events']:
            if step['event'] == 'catalog.edit':
                stock = step['observed']['stock']
            if step['event'] == 'cart.add':
                values.append((step['args']['quantity'], stock, step['observed']['quantity']))
    candidates = [
        ('copy', lambda requested, stock: requested, A('quantity')),
        ('minimum', min, ['min', A('quantity'), Q('product.stock')]),
    ]
    fits = [(name, expr) for name, operation, expr in candidates
            if all(operation(requested, stock) == quantity for requested, stock, quantity in values)]
    if len(fits) != 1:
        raise ValueError('Training traces must uniquely distinguish the quantity candidates')
    return fits[0][1], sum(requested > stock for requested, stock, _ in values)


def view_prompt(scope, columns, sort):
    labels = ', '.join(f'Spalte {NAMES[column]}' for column in columns)
    return f'{ACTIONS[scope]}. {labels}. Sortiere nach {NAMES[sort]}.'


def spec(base, columns, sort):
    return {**base, 'columns': [field for field in FIELD_TYPES if field in columns], 'sort': sort}


def reserved_cases():
    bases = views()
    selections = {
        'storefront': ('product.name', 'product.price', 'product.stock'),
        'checkout': ('cart.discount', 'cart.shipping', 'cart.gross'),
        'orders': ('order.gross', 'order.payment', 'order.delivery'),
    }
    return {scope: (view_prompt(scope, columns, columns[1] if scope == 'orders' else columns[0]),
                    spec(bases[scope], columns, columns[1] if scope == 'orders' else columns[0]))
            for scope, columns in selections.items()}


class ViewLessons:
    def __init__(self):
        self.bases = views()

    def __call__(self, fields, settings, names):
        for scope in names:
            columns = SCOPE_FIELDS[scope]
            if not all(key in fields for key in columns):
                continue
            for count in (1, 2):
                for shown in combinations(columns, count):
                    for sort in shown:
                        request = {'kind': 'project', 'args': {'text': view_prompt(scope, shown, sort)}}
                        yield context(fields, settings, request), {'holo.view': literal(spec(self.bases[scope], shown, sort))}
