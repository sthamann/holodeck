"""Independent learned facets for the mixed shop's order workspace."""
from collections import Counter
import json
import re

import numpy as np


WRITE_FACETS = ('pay', 'reopen', 'ship')
FILTERS = {'payment_filter': 'payment', 'delivery_filter': 'delivery'}
FACETS = ('gross', 'payment', 'delivery', 'stage', 'sort', 'search',
          *FILTERS, 'details', 'workflow', 'order_taxes', 'pay', 'reopen', 'ship')
CLAUSES = re.compile(r'[.!?,;\n]+')
WORDS = re.compile(r'\w+')
FULL = ('Alle Bestellspalten und Positionen mit Suche, Sortierung und Statusfiltern. '
        'Zeig die Workflow-Markierung. Aktionen für Zahlung und Versand.')

# Annotations on the normalized Shopware vocabulary. The multi-label examples
# deliberately share words between read-only fields, filters, and write actions.
EXAMPLES = {
    'gross': (
        'Zeige den Gesamtbetrag', 'Brutto als Spalte', 'Betrag pro Auftrag anzeigen',
        'Was kostet jede Bestellung', 'Bestellsumme als Zahl', 'Bestellwert sehen',
        'Rechnungsbetrag in der Liste', 'Mit Summen in Euro', 'Show the order total',
    ),
    'payment': (
        'Zahlungsstatus als Spalte', 'Ist die Bestellung bezahlt', 'Zeig den Stand der Zahlung',
        'Zahlungszustand ansehen', 'Ob die Zahlung eingegangen ist', 'Zahlung in der Tabelle',
        'Payment status in the list',
    ),
    'delivery': (
        'Lieferstatus als Spalte', 'Wie weit ist der Versand', 'Zeige den Lieferzustand',
        'Ist das Paket schon unterwegs', 'Stand der Lieferung sehen', 'Versandstatus ansehen',
        'Show the delivery status',
    ),
    'stage': (
        'Auftragsstatus als Spalte', 'Bestellstatus ansehen', 'Ist der Auftrag noch offen',
        'Stand der Bestellung zeigen', 'In welchem Zustand ist der Auftrag', 'Order status column',
    ),
    'sort': (
        'Die Tabelle soll sortierbar sein', 'Erlaube das Sortieren', 'Spalten sortierbar machen',
        'Ich will die Zeilen auf und absteigend ordnen', 'Zeilen umsortieren', 'Reihenfolge ändern',
        'Sortierfunktion für die Liste', 'Make columns sortable',
    ),
    'search': (
        'Ein Suchfeld für die Bestellnummer', 'Bestellungen durchsuchen', 'Suche anhand der Auftragsnummer',
        'Ich will nach einer Nummer suchen', 'Auftrag über die Referenznummer finden', 'Suche einblenden',
        'Search by order number',
    ),
    'payment_filter': (
        'Nach Zahlungsstatus filtern', 'Zahlungen nach Status eingrenzen', 'Filter für bezahlt und offen',
        'Nur bezahlte Aufträge herausfiltern können', 'Zahlungszustand als Auswahlfilter',
        'Bestellungen nach der Zahlung eingrenzen', 'Filter by payment status',
    ),
    'delivery_filter': (
        'Nach Lieferstatus filtern', 'Versand nach Status eingrenzen', 'Filter für versandt und offen',
        'Nur unversandte Aufträge herausfiltern können', 'Lieferzustand als Auswahlfilter',
        'Bestellungen nach dem Versand eingrenzen', 'Filter by delivery status',
    ),
    'details': (
        'Bestellpositionen ausklappen', 'Enthaltene Artikel anzeigen', 'Welche Produkte sind in der Bestellung',
        'Ich möchte die Artikelzeilen sehen', 'Inhalt des Auftrags ansehen', 'Was wurde gekauft',
        'Zeig die Bestellpositionen im Detail', 'Show line items',
    ),
    'workflow': (
        'Workflow-Markierung anzeigen', 'Automationsstatus ansehen', 'Hat der Ablauf die Bestellung markiert',
        'Ist die Workflow-Regel ausgelöst worden', 'Zeig den Eintrag der Automatisierung',
        'Ergebnis der Ablaufregel', 'Show the workflow marker',
    ),
    'order_taxes': (
        'Mehrwertsteuer nach Satz aufschlüsseln', 'Aufteilung der Umsatzsteuer nach Steuersatz',
        'Wie viel Steuer entfällt auf jede Rate', 'Aufschlüsselung der Steuerbeträge', 'Steuer je Steuerklasse',
        'Welche Umsatzsteuer fällt pro Steuersatz an', 'Zeige die unterschiedlichen Steueranteile',
        'Steuerbeträge getrennt nach Prozentsatz', 'Show the tax breakdown',
    ),
    'pay': (
        'Zahlung als bezahlt markieren können', 'Schaltfläche zum Bestätigen der Zahlung',
        'Ich möchte den Zahlungseingang verbuchen', 'Aktion für bezahlt setzen', 'Zahlung als eingegangen erfassen',
        'Bezahlstatus auf bezahlt setzen', 'Mark payment as paid',
    ),
    'reopen': (
        'Zahlung wieder öffnen können', 'Zahlungsstatus zurück auf offen setzen',
        'Zahlungsbestätigung rückgängig machen', 'Aktion um die Zahlung erneut zu öffnen',
        'Bezahlte Zahlung auf offen zurücksetzen', 'Bezahlstatus zurücknehmen', 'Reopen payment',
    ),
    'ship': (
        'Versand bestätigen können', 'Schaltfläche zum Versenden', 'Lieferung als versandt markieren',
        'Ich möchte das Paket als verschickt erfassen', 'Versand auslösen', 'Aktion zum Abschließen des Versands',
        'Auslieferung buchen', 'Mark delivery as shipped',
    ),
}
COMBINED = {
    'Bruttobetrag und Lieferstatus': ('gross', 'delivery'),
    'Stand der Zahlung und der Lieferung': ('payment', 'delivery'),
    'Bestellwert in einer sortierbaren Tabelle': ('gross', 'sort'),
    'Zahlungsstatus anzeigen und nach ihm filtern': ('payment', 'payment_filter'),
    'Lieferstatus anzeigen und nach ihm filtern': ('delivery', 'delivery_filter'),
    'Bezahlen markieren und den Versand bestätigen': ('pay', 'ship'),
    'Zahlung buchen und wieder öffnen': ('pay', 'reopen'),
    'Zahlung anzeigen ohne sie zu ändern': ('payment',),
    'Lieferstatus anzeigen ohne Versandaktion': ('delivery',),
    'Nur Steueraufschlüsselung ohne Zahlung oder Versand': ('order_taxes',),
    'Alle Bestellspalten und Positionen mit Suche': ('gross', 'payment', 'delivery', 'stage', 'details', 'search'),
    'Sortierung und Statusfiltern': ('sort', 'payment_filter', 'delivery_filter'),
    'Aktionen für Zahlung und Versand': ('pay', 'reopen', 'ship'),
    'Zeig eine vollständige Bestellübersicht': tuple(label for label in FACETS if label != 'order_taxes'),
    'Nach Bruttobetrag absteigend sortieren': ('gross', 'sort'),
    'Gesamtbeträge der Größe nach ordnen': ('gross', 'sort'),
    'Bitte keine Steueraufschlüsselung': (),
    'Mehrwertsteuer nicht einzeln aufschlüsseln': (),
    'Ohne Aufschlüsselung der Steuerbeträge': (),
    'Keinen Versand auslösen': (),
    'Keine Schaltfläche zum Versenden': (),
    'Ich will keine Lieferung als versandt markieren': (),
    'Versandstatus zeigen aber den Versand nicht bestätigen': ('delivery',),
    'Versandstatus ohne Bestätigungsbutton anzeigen': ('delivery',),
    'Nur ablesen ob bezahlt wurde ohne es zu bearbeiten': ('payment',),
    'Zahlungszustand nur ansehen bitte keine Filter': ('payment',),
    'Keine Aktion um die Zahlung wieder zu öffnen': (),
    'Die Zahlung nicht auf offen zurücksetzen': (),
    'Zahlung nicht als bezahlt markieren': (),
    'Keine Aktion zum Zahlungseingang': (),
    'Ohne Sortierung oder Filter': (),
    'Keine Änderungen an Zahlung und Versand': (),
    'Nur die Auftragsnummer': (),
    'Liste die Bestellungen auf': (),
}


# Pairs keep the object and verb constant so the polarity fit learns words
# that distinguish a request for a control from a request to omit it.
POLARITY = (
    ('Versand bestätigen', 'Versand nicht bestätigen'),
    ('Versand auslösen', 'Keinen Versand auslösen'),
    ('Eine Versandaktion', 'Keine Versandaktion'),
    ('Zahlung als bezahlt markieren', 'Zahlung nie als bezahlt markieren'),
    ('Zahlungsstatus ändern', 'Ohne Zahlungsstatus zu ändern'),
    ('Zahlung wieder öffnen', 'Zahlung keinesfalls wieder öffnen'),
    ('Zahlung bestätigen', 'Zahlung bitte nicht bestätigen'),
    ('Versand freigeben', 'Versand darf niemand freigeben'),
    ('Zahlung setzen', 'Nur lesen ohne Zahlung zu setzen'),
    ('Zahlungsoptionen', 'Keine Zahlungsoptionen'),
    ('Versand ausführen', 'Weder Versand ausführen noch abschließen'),
    ('Mark as paid', 'Do not mark as paid'),
    ('Reopen the payment', 'Never reopen the payment'),
    ('Allow shipment', 'Without allowing shipment'),
    ('Show the shipment button', 'No shipment button'),
)


def fit_polarity():
    odds = Counter()
    for positive, negative in POLARITY:
        odds.update(set(WORDS.findall(negative.casefold())))
        odds.subtract(set(WORDS.findall(positive.casefold())))
    # Shared words cancel within each pair.
    return dict(odds)


def clauses(text):
    return [part.strip() for part in CLAUSES.split(text) if part.strip()]


def grams(text):
    result = set()
    for word in WORDS.findall(text.casefold()):
        token = '^' + word + '$'
        result.update(token[i:i + size] for size in (3, 4, 5) for i in range(len(token) - size + 1))
    return result


def examples(workspace):
    native = [json.loads(line) for line in workspace.read_text().splitlines()]
    orders = [row for row in native if row['source'] == 'orders']
    observed = {row['kind'] for row in orders}
    grid = next(row for row in orders if row['kind'] == 'grid')
    columns = {field['label'] for field in grid['headers'] if field['sortable']}
    filters = {label for row in orders if row['kind'] == 'filter' for label in row['labels']}
    if (not {'sort', 'search', 'filter', 'expand', 'command'} <= observed
            or not {'Order number', 'Total', 'Order status', 'Delivery status', 'Payment status'} <= columns
            or not {'Payment status', 'Delivery status'} <= filters
            or not any(row.get('event') == 'order_delivery.ship' for row in orders)):
        raise ValueError('Order projection grammar is missing Shopware observations')
    rows = {text: (label,) for label, texts in EXAMPLES.items() for text in texts}
    rows.update(COMBINED)
    return rows


class AdminProjection:
    def __init__(self, features, path, workspace, *, fit=False, extra=None):
        self.features = features
        self.facets = (*FACETS, *(extra or {}))
        if fit:
            rows = examples(workspace)
            for facet, phrases in (extra or {}).items():
                for phrase in phrases:
                    rows[phrase] = (*rows.get(phrase, ()), facet)
            self.texts = list(rows)
        else:
            with np.load(path, allow_pickle=False) as archive:
                if tuple(archive['facets']) != self.facets:
                    raise ValueError('Admin checkpoint does not match the projection grammar')
                self.texts = archive['texts'].tolist()
                self.weights = archive['weights']
                self.lexical_weights = archive['lexical_weights']
                self.dense = archive['dense']
                self.polarity = json.loads(str(archive['polarity']))
        self.grams = [grams(text) for text in self.texts]
        if fit:
            dense = self.encode(self.texts)
            self.dense = dense
            semantic, lexical = self.kernels(self.texts, dense)
            y = np.array([[float(label in labels) for label in self.facets] for labels in rows.values()])
            penalty = 0.15 * np.eye(len(y))
            self.weights = np.linalg.solve(semantic + lexical + penalty, y)
            self.lexical_weights = np.linalg.solve(lexical + penalty, y)
            self.polarity = fit_polarity()
            np.savez_compressed(path, texts=self.texts, facets=self.facets, weights=self.weights,
                                lexical_weights=self.lexical_weights, dense=self.dense,
                                polarity=json.dumps(self.polarity, sort_keys=True))

    def encode(self, texts):
        contexts = [(('admin',), f'Händleranfrage: {text}\nBenötigte Bedienelemente:') for text in texts]
        self.features.warm(contexts)
        dense = np.stack([self.features.cache[ctx][:128] for ctx in contexts])
        return dense / (np.linalg.norm(dense, axis=-1, keepdims=True) + 1e-8)

    def kernels(self, texts, dense):
        query = [grams(text) for text in texts]
        lexical = np.array([[len(a & b) / max(1, (len(a) * len(b)) ** 0.5)
                             for b in self.grams] for a in query])
        semantic = 0.65 * np.exp(-3 * (1 - dense @ self.dense.T))
        return semantic, lexical

    def choose(self, text, *, lexical_only=False):
        parts = clauses(text)
        if not parts:
            return set()
        dense = self.encode(parts)
        semantic, lexical = self.kernels(parts, dense)
        values = lexical @ self.lexical_weights if lexical_only else (semantic + lexical) @ self.weights
        selected = set()
        for part, scores in zip(parts, values, strict=True):
            deny_write = sum(self.polarity.get(word, 0)
                             for word in set(WORDS.findall(part.casefold()))) >= 1
            selected.update(label for label, value in zip(self.facets, scores, strict=True)
                            if value >= 0.28 and not (deny_write and label in (*WRITE_FACETS, 'returns', 'refunds')))
        return selected
