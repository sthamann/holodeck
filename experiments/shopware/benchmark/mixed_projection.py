"""Learn component, field and event bindings for the collection prototype."""
import json
import re
from pathlib import Path

import numpy as np

from admin_projection import AdminProjection, FILTERS
from composition import TextFeatures


HERE = Path(__file__).resolve().parent
# Typed projection grammar. A descriptor binds exactly one generic component
# to a source, or adds one field or command to that component.
ATOMS = {
    'products': ('catalog', 'quantity-grid'),
    'cart': ('cart', 'lines'),
    'cart_totals': ('cart', 'totals'),
    'coupon': ('cart', 'form'),
    'receipt': ('receipt', 'totals'),
    'orders': ('orders', 'grid'),
    'details': ('selection', 'lines'),
    'workflow': ('workflow', 'status'),
    'taxes': ('cart', 'taxes'),
    'order_taxes': ('selection', 'taxes'),
}
COMMANDS = {
    'update': ('products', 'cart.add', 'quantities'),
    'discount': ('coupon', 'cart.promo', 'code'),
    'place': ('cart_totals', 'checkout.place', 'cart'),
    'pay': ('details', 'order_transaction.paid', 'row.id'),
    'reopen': ('details', 'order_transaction.reopen', 'row.id'),
    'ship': ('details', 'order_delivery.ship', 'row.id'),
}
COLUMNS = ('number', 'gross', 'payment', 'delivery', 'stage')
CONTROLS = ('sort', 'filter', 'select')
SCOPES = {
    'storefront': ('products', 'cart', 'cart_totals', 'coupon', 'update', 'discount'),
    'checkout': ('cart', 'cart_totals', 'place'),
    'receipt': ('receipt',),
    'admin': ('orders', 'details', 'workflow', *COLUMNS, *CONTROLS, 'pay', 'reopen', 'ship'),
}
OPTIONAL = {'storefront': ('taxes',), 'checkout': ('taxes',), 'receipt': (), 'admin': ('order_taxes',)}
LABELS = tuple(dict.fromkeys(label for labels in SCOPES.values() for label in labels))
CLAUSES = re.compile(r'[.!?,;]+')

PHRASES = {
    'storefront': ('Show the products and put items into my shopping basket.',
                   'I want to buy several catalog items and see the basket.',
                   'Zeig mir das Sortiment und den Warenkorb mit Gutschein.',
                   'Artikel stöbern, Mengen wählen und Rabattcode eingeben.'),
    'checkout': ('Review the basket total and place the order.',
                 'Open checkout with the selected items and payment summary.',
                 'Ich möchte den Warenkorb prüfen und verbindlich bestellen.',
                 'Zur Kasse mit allen Positionen und der Summe.'),
    'receipt': ('Show the order confirmation.', 'I want to see what I just ordered.',
                'Zeig die Bestellbestätigung.', 'Ist meine Bestellung angekommen?'),
    'admin': ('Open the orders grid with status and shipping actions.',
              'Find my orders, inspect items and manage payment and delivery.',
              'Zeig Bestellungen mit Zahlung und Lieferung. Ich will die Zeilen öffnen und versenden.',
              'Bestellungen filtern und sortieren, Details ansehen und Versand bearbeiten.'),
}
TAX_WORDS = (
    (' Include the tax breakdown.', ' Show VAT by rate.'),
    (' Mit Steueraufschlüsselung.', ' Die Mehrwertsteuer bitte nach Satz aufschlüsseln.',
     ' Welche Umsatzsteuer fällt pro Steuersatz an?'),
)
OTHER_WORDS = (
    (' Show all the amounts.', ' Make the list easy to scan.', ' Add the status and price.',
     ' Open the orders.', ' Display the shipment and payment.'),
    (' Bitte zeig die Beträge.', ' Die Liste bitte übersichtlich und sortierbar.', ' Mit Status und Summen.',
     ' Bestellung und Warenkorb öffnen.', ' Zeige Zahlung und Versand.', ' Positionen und Brutto anzeigen.',
     ' Aufträge nach dem Gesamtbetrag ordnen.', ' Gesamtbetrag, Zahlung und Lieferung.'),
)


def lessons():
    admin = [json.loads(line) for line in (HERE.parent / 'workspace/ui.jsonl').read_text().splitlines()]
    orders = [row for row in admin if row['source'] == 'orders']
    headers = next(row['headers'] for row in orders if row['kind'] == 'grid')
    native_fields = {'Order number': 'number', 'Total': 'gross', 'Payment status': 'payment',
                     'Delivery status': 'delivery', 'Order status': 'stage'}
    if set(native_fields) - {item['label'] for item in headers if item['sortable']}:
        raise ValueError('Missing native sortable order columns')
    if {'sort', 'filter', 'expand'} - {row['kind'] for row in orders}:
        raise ValueError('Missing native order interactions')
    native = [json.loads(line) for line in (HERE.parent / 'scale/ui.jsonl').read_text().splitlines()]
    observed = {row['event'] for row in native}
    if {'cart.add', 'cart.promo'} - observed or not any(
            row.get('event') == 'order_delivery.ship' for row in orders):
        raise ValueError('Missing native action capture')
    for scope, utterances in PHRASES.items():
        targets = set(SCOPES[scope])
        if scope == 'admin':
            targets = (targets - set(COLUMNS)) | set(native_fields.values())
        for i, utterance in enumerate(utterances):
            yield ((scope,), utterance), targets
            if OPTIONAL[scope]:
                for addition in (*TAX_WORDS[i // 2], *OTHER_WORDS[i // 2]):
                    yield ((scope,), utterance + addition), targets


def ridge(x, y):
    return x.T @ np.linalg.solve(x @ x.T + np.eye(len(x)) * 0.1, y)


class Projector:
    def __init__(self, base, weights, *, fit=False, admin_weights=None):
        self.features = TextFeatures(base, (), [((scope,), '') for scope in SCOPES])
        if fit:
            examples = list(lessons())
            self.features.warm([ctx for ctx, _ in examples])
            x = np.stack([self.vector(ctx) for ctx, _ in examples])
            y = np.array([[1 if label in targets else -1 for label in LABELS]
                          for _, targets in examples])
            # The independent outputs share a frozen representation. Ridge in
            # the dual keeps this small even with hashed lexical features.
            self.weights = ridge(x, y)
            # Learn optional details at clause granularity. A new clause can
            # change one panel without changing the remainder of the view.
            clauses = [((scope,), clause.strip()) for scope in SCOPES if OPTIONAL[scope]
                       for groups in (TAX_WORDS, OTHER_WORDS) for group in groups for clause in group]
            self.features.warm(clauses)
            targets = {item.strip() for group in TAX_WORDS for item in group}
            self.detail = ridge(np.stack([self.clause_vector(ctx) for ctx in clauses]),
                                np.array([1 if text in targets else -1 for _, text in clauses]))
            np.savez_compressed(weights, weights=self.weights, detail=self.detail, labels=LABELS)
        else:
            with np.load(weights, allow_pickle=False) as archive:
                if tuple(archive['labels']) != LABELS:
                    raise ValueError('Projection checkpoint grammar mismatch')
                self.weights = archive['weights']
                self.detail = archive['detail']
        self.admin = (AdminProjection(self.features, admin_weights, HERE.parent / 'workspace/ui.jsonl')
                      if admin_weights else None)

    def clause_vector(self, context):
        x = self.vector(context)
        x[-1 - self.features.WIDTH:-1] *= 4
        return x

    def vector(self, context):
        x = self.features(context)
        # Keep the permission context explicit. The optional fields still need
        # to be inferred from the utterance within that context.
        x[128:128 + len(self.features.tags)] *= 4
        return np.append(x, 1)

    def scores(self, scope, text, *, control=False):
        x = self.vector(((scope,), text))
        return x @ (np.zeros_like(self.weights) if control else self.weights)

    def emit(self, scope, text, *, control=False):
        if scope == 'admin' and self.admin is not None:
            if control:
                return []
            facets = self.admin.choose(text)
            selected = {'orders', 'number', *facets}
            if facets & {'pay', 'reopen', 'ship'}:
                selected.add('details')
            if facets & {'details', 'pay', 'reopen', 'ship', 'workflow', 'order_taxes'}:
                selected.add('select')
            return self.compose(selected, {value for key, value in FILTERS.items() if key in facets})

        scores = self.scores(scope, text, control=control)
        allowed = set(SCOPES[scope])
        selected = {label for label, score in zip(LABELS, scores, strict=True)
                    if label in allowed and score > 0.05}
        if not control and OPTIONAL[scope]:
            clauses = [((scope,), clause.strip()) for clause in CLAUSES.split(text) if clause.strip()]
            self.features.warm(clauses)
            if any(self.clause_vector(ctx) @ self.detail > 0.25 for ctx in clauses):
                selected.update(OPTIONAL[scope])
        return self.compose(selected)

    @staticmethod
    def compose(selected, filters=None):
        result = []
        for name, (source, component) in ATOMS.items():
            if name not in selected:
                continue
            node = {'id': name, 'source': source, 'component': component}
            if component == 'grid':
                node['columns'] = [field for field in COLUMNS if field in selected]
                node['controls'] = [key for key in (*CONTROLS, 'search') if key in selected]
                if filters is not None:
                    node['filters'] = [key for key in ('payment', 'delivery') if key in filters]
            node['actions'] = [{'id': action, 'event': event, 'binding': binding}
                               for action, (parent, event, binding) in COMMANDS.items()
                               if parent == name and action in selected]
            result.append(node)
        return result
