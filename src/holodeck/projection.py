"""A learned UI program decoder and its typed interpreter."""
from functools import lru_cache

import numpy as np

from .runtime import Refusal

SLOTS = 8
WIDTH = 10
TREE_AXES = ('kind', 'parent', 'source', 'title', 'text')
BINDING_AXES = ('fields', 'events', 'tools', 'filters', 'sorts', 'details')
LAYOUTS = {'stack', 'split'}
COLLECTIONS = {'grid', 'table', 'list'}
WIDGETS = COLLECTIONS | {'stats', 'detail', 'hero', 'message', 'form', 'nav'}
TOOLS = {'search', 'sort', 'filter', 'expand'}
INTENTS = {
    'storefront': ('browse', 'compare'),
    'checkout': ('pay',),
    'order': ('status',),
    'admin': ('overview', 'orders', 'catalog'),
}

# Semantic types belong to the record interface. The model chooses which fields to display.
FIELDS = {
    'image': ('Foto', 'image'), 'name': ('Name', 'text'), 'sku': ('Artikelnummer', 'text'),
    'variant': ('Variante', 'text'), 'description': ('Beschreibung', 'text'),
    'price': ('Preis', 'money'), 'stock': ('Bestand', 'number'), 'availability': ('Verfügbarkeit', 'tag'),
    'quantity': ('Menge', 'number'), 'unit': ('Stückpreis', 'money'), 'gross': ('Gesamt', 'money'),
    'goods': ('Warenwert', 'money'), 'shipping': ('Versand', 'money'), 'tax': ('Enthaltene MwSt.', 'money'),
    'net': ('Netto', 'money'), 'id': ('Bestellung', 'order-id'), 'address': ('Lieferadresse', 'text'),
    'stage': ('Bestellstatus', 'stage'), 'payment': ('Zahlung', 'payment'), 'delivery': ('Versandstatus', 'delivery'),
    'tracking': ('Sendungsnummer', 'text'), 'items': ('Positionen', 'items'),
    'total_orders': ('Bestellungen', 'number'), 'paid_orders': ('Bezahlt', 'number'),
    'open_deliveries': ('Versand offen', 'number'), 'active_products': ('Aktive Artikel', 'number'),
}
SOURCE_FIELDS = {
    'catalog': {'image', 'name', 'sku', 'variant', 'description', 'price', 'stock', 'availability'},
    'cart': {'sku', 'name', 'quantity', 'unit', 'gross'},
    'quote': {'goods', 'shipping', 'tax', 'gross', 'net'},
    'orders': {'id', 'name', 'address', 'stage', 'payment', 'delivery', 'tracking', 'gross', 'items'},
    'receipt': {'id', 'name', 'address', 'stage', 'payment', 'delivery', 'tracking', 'gross', 'items'},
    'metrics': {'total_orders', 'paid_orders', 'open_deliveries', 'active_products'},
}
SOURCES = {
    'buyer': {'catalog', 'cart', 'quote', 'receipt'},
    'admin': {'catalog', 'orders', 'metrics'},
}

# Transport signatures and human labels, independent of any page or widget.
PARAMETERS = {
    'sku': ('Artikelnummer', 'key'), 'order_id': ('Bestellung', 'key'),
    'quantity': ('Menge', 'number'), 'name': ('Vollständiger Name', 'text'),
    'email': ('E-Mail', 'email'), 'address': ('Lieferadresse', 'text'),
    'quote_key': ('Preisbindung', 'quote'), 'consent': ('Ich bestätige die Testbestellung zum angezeigten Preis.', 'checkbox'),
    'tracking': ('Sendungsnummer', 'text'),
}
EVENTS = {
    'cart.add': ('In den Warenkorb', ('sku', 'quantity')),
    'cart.set': ('Menge ändern', ('sku', 'quantity')),
    'cart.remove': ('Entfernen', ('sku',)),
    'checkout.place': ('Testbestellung aufgeben', ('quote_key', 'name', 'email', 'address', 'consent')),
    'order.process': ('In Bearbeitung nehmen', ('order_id',)),
    'order.complete': ('Bestellung abschließen', ('order_id',)),
    'order_transaction.paid': ('Testzahlung bestätigen', ('order_id',)),
    'order_transaction.refund': ('Testzahlung erstatten', ('order_id',)),
    'order_delivery.ship': ('Als versendet markieren', ('order_id',)),
    'order_delivery.retour': ('Retoure erfassen', ('order_id',)),
    'order_transaction.fail': ('Zahlung als fehlgeschlagen markieren', ('order_id',)),
    'order_transaction.reopen': ('Zahlung wieder öffnen', ('order_id',)),
    'order_delivery.ship_partially': ('Teilversandstatus setzen', ('order_id',)),
    'order_delivery.reopen': ('Versandstatus wieder öffnen', ('order_id',)),
    'order_delivery.track': ('Sendungsnummer speichern', ('order_id', 'tracking')),
}
NAV_LABELS = {
    'storefront:browse': 'Entdecken', 'storefront:compare': 'Produkte vergleichen',
    'checkout:pay': 'Zur Kasse', 'order:status': 'Bestellung ansehen',
    'admin:overview': 'Übersicht', 'admin:orders': 'Bestellungen', 'admin:catalog': 'Sortiment',
}


def encode_features(vocabularies, keys):
    values = np.zeros(sum(map(len, vocabularies)), dtype=np.float32)
    offset = 0
    for key, vocabulary in zip(keys, vocabularies, strict=True):
        if key in vocabulary:
            values[offset + vocabulary.index(key)] = 1
        offset += len(vocabulary)
    return values


class Projector:
    """Decode node attributes and pointer slots directly from trained scores."""

    def __init__(self, archive):
        import json
        self.schema = json.loads(str(archive['ui.schema']))
        if self.schema['version'] != 3:
            raise ValueError('Unsupported projection weights')
        for head, size in (('tree', SLOTS*len(TREE_AXES)), ('bindings', WIDTH*len(BINDING_AXES)), ('argument', 1)):
            spec = self.schema[head]
            if len(spec['outputs']) != size:
                raise ValueError('Incompatible projection head shape')
        self.values = {k: v for k, v in archive.items() if k.startswith('ui.')}

    def predict(self, head, keys):
        spec = self.schema[head]
        masks = spec.get('invariant', {}).get(keys[0])
        output, start, predictions = [], 0, {}
        for position, vocabulary in enumerate(spec['outputs']):
            if len(vocabulary) == 1:
                output.append(vocabulary[0])
                start += 1
                continue
            mask = masks[position] if masks else ()
            view = tuple(None if i in mask else key for i,key in enumerate(keys))
            if view not in predictions:
                vector = encode_features(spec['inputs'], view)
                for layer in (0, 2):
                    vector = np.maximum(self.values[f'ui.{head}.{layer}.weight'] @ vector +
                                        self.values[f'ui.{head}.{layer}.bias'], 0)
                predictions[view] = self.values[f'ui.{head}.4.weight'] @ vector + self.values[f'ui.{head}.4.bias']
            row = predictions[view][start:start+len(vocabulary)]
            index = int(row.argmax())
            if not np.isfinite(row).all() or np.count_nonzero(row == row[index]) != 1:
                raise Refusal('Das Modell liefert keine eindeutige Oberfläche.')
            output.append(vocabulary[index])
            start += len(vocabulary)
        return output

    @lru_cache(maxsize=192)
    def decode(self, context):
        raw = self.predict('tree', context)
        nodes = []
        for position in range(SLOTS):
            begin = position * len(TREE_AXES)
            node = dict(zip(TREE_AXES, raw[begin:begin+len(TREE_AXES)], strict=True))
            if node['kind'] == '_':
                break
            if node['kind'] in WIDGETS:
                bindings = self.predict('bindings', (node['kind'], node["source"], node["title"], context[0], context[1], context[2]))
                for index, axis in enumerate(BINDING_AXES):
                    node[axis] = [x for x in bindings[index*WIDTH:(index+1)*WIDTH] if x != '_']
                node['wires'] = {}
                for event in node['events']:
                    if event not in EVENTS:
                        continue
                    params = EVENTS[event][1]
                    node['wires'][event] = {
                        name: self.predict('argument', (event, node['source'], name))[0] for name in params
                    }
                    node['wires'][event]['navigate'] = self.predict('argument', (event, node['source'], 'navigate'))[0]
            nodes.append(node)
        return nodes


def destination(value):
    task, _, intent = value.partition(':')
    if intent not in INTENTS.get(task, ()):
        raise Refusal('Ungültiges Navigationsziel im Modell.')
    return {'task': task, 'intent': intent}


def bind_action(event, wires, row, quote_key):
    label, params = EVENTS[event]
    args, inputs = {}, []
    for name in params:
        pointer = wires[name]
        caption, kind = PARAMETERS[name]
        if pointer == 'input' and kind not in ('key', 'quote'):
            initial = row.get(name, False if kind == 'checkbox' else '')
            inputs.append({'name': name, 'label': caption, 'type': kind, 'value': initial})
        elif pointer == 'row.sku' and name == 'sku' and type(row.get('sku')) is str:
            args[name] = row['sku']
        elif pointer == 'row.id' and name == 'order_id' and type(row.get('id')) is str:
            args[name] = row['id']
        elif pointer == 'const.one' and kind == 'number':
            args[name] = 1
        elif pointer == 'context.quote' and kind == 'quote' and quote_key:
            args[name] = quote_key
        else:
            raise Refusal('Ungültige Aktionsbindung im Modell.')
    if event == 'checkout.place' and {item['name'] for item in inputs} != {'name', 'email', 'address', 'consent'}:
        raise Refusal('Der Checkout benötigt eine sichtbare Zustimmung.')
    return {'event': event, 'title': label, 'args': args, 'fields': inputs,
            'navigate': destination(wires['navigate']) if wires['navigate'] != '_' else None}


def query_rows(source, rows, node, query):
    """Apply only the list interactions present in the generated program."""
    if source != 'orders':
        return rows, None
    fields, tools = node['fields'], node['tools']
    text = str(query.get('q', ''))[:120].casefold() if 'search' in tools else ''
    filters = query.get('filters', {})
    if not isinstance(filters, dict):
        filters = {}
    filter_keys = node['filters'] if 'filter' in tools else ()
    options = {key: list(dict.fromkeys(row[key] for row in rows)) for key in filter_keys}
    visible = [row for row in rows if (not text or any(text in str(row[key]).casefold() for key in fields))
               and all(not filters.get(key) or str(row[key]) == filters[key] for key in filter_keys)]
    sort = query.get('sort')
    if 'sort' in tools and sort in node['sorts']:
        visible.sort(key=lambda row: row[sort].casefold() if isinstance(row[sort],str) else row[sort],
                     reverse=query.get('direction') == -1)
    size = 20
    page = query.get('page', 0)
    page = max(0,min(page, max(0,(len(visible)-1)//size))) if type(page) is int else 0
    return visible[page*size:page*size+size], {'page':page,'size':size,'total':len(visible),'options':options}


def materialize(program, role, sources, affordances, quote_key, brand, queries=None, order_actions=None):
    """Only the emitted graph determines component order, data and capabilities."""
    if not program or program[0]['kind'] not in LAYOUTS or program[0]['parent'] != '_':
        raise Refusal('Das Modell hat keine gültige Oberfläche erzeugt.')
    built = []
    shown_quote = has_checkout = False
    queries = queries or {}
    for index, node in enumerate(program):
        kind, source = node['kind'], node['source']
        result = {'component': kind, 'title': node['title'].replace('{brand}', brand['name'])}
        if kind in LAYOUTS:
            if source != '_':
                raise Refusal('Layout enthält eine ungültige Datenbindung.')
            result['children'] = []
        elif kind in WIDGETS:
            if source != '_' and source not in SOURCES[role]:
                raise Refusal('Diese Datenquelle ist für die Ansicht gesperrt.')
            fields = node['fields']
            if any(key not in SOURCE_FIELDS.get(source, ()) for key in fields) or len(set(fields)) != len(fields):
                raise Refusal('Ungültige Feldbindung im Modell.')
            if any(tool not in TOOLS for tool in node['tools']) or any(key not in fields for key in [*node['filters'], *node['sorts'], *node['details']]):
                raise Refusal('Ungültige Interaktionsbindung im Modell.')
            result['text'] = node['text'] if node['text'] != '_' else ''
            result['fields'] = [{'key': key, 'label': FIELDS[key][0], 'format': FIELDS[key][1]} for key in fields]
            result['tools'] = node['tools']
            result['filters'] = node['filters']
            result['sorts'] = node['sorts']
            result['details'] = node['details']
            result['source'] = source
            result['links'] = []
            for event in node['events']:
                if event.startswith('go.'):
                    target = event[3:]
                    dest = destination(target)
                    if (dest['task'] == 'admin') != (role == 'admin'):
                        raise Refusal('Navigationsziel liegt außerhalb der Rolle.')
                    if target == 'checkout:pay' and 'checkout.place' not in affordances.get(('_', None), ()):
                        continue
                    result['links'].append({'title': NAV_LABELS[target], **dest})
                elif event not in EVENTS:
                    raise Refusal('Unbekannte Aktion im Modell.')
            uses_source = kind in COLLECTIONS | {'stats', 'detail'}
            if uses_source != (source != '_'):
                raise Refusal('Ungültige Datenquelle für diesen Baustein.')
            if uses_source:
                query = queries.get(source, {})
                selected, paging = query_rows(source, sources[source], node, query if isinstance(query, dict) else {})
                rows = []
                for row in selected:
                    key = row.get('sku') if source in ('catalog', 'cart') else row.get('id')
                    allowed = order_actions(key) if source == 'orders' else affordances.get((source, key), ())
                    actions = [bind_action(event, node['wires'][event], row, quote_key)
                               for event in node['events'] if event in allowed]
                    rows.append({'values': {k: row[k] for k in fields}, 'actions': actions})
                result['rows'] = rows
                result['paging'] = paging
                shown_quote |= source == 'quote' and 'gross' in fields and bool(rows)
            elif kind == 'form':
                result['actions'] = [
                    bind_action(event, node['wires'][event], {}, quote_key)
                    for event in node['events'] if event in affordances.get(('_', None), ())
                ]
                has_checkout |= any(a['event']=='checkout.place' for a in result['actions'])
                if not result['actions']:
                    raise Refusal('Die angeforderte Aktion steht gerade nicht zur Verfügung.')
        else:
            raise Refusal('Unbekannter Baustein im Modell.')
        if index:
            parent = node['parent']
            if type(parent) is not int or not 0 <= parent < index or 'children' not in built[parent]:
                raise Refusal('Ungültige Anordnung im Modell.')
            built[parent]['children'].append(result)
        built.append(result)
    if has_checkout and not shown_quote:
        raise Refusal('Der Checkout benötigt einen sichtbaren Gesamtpreis.')
    return built[0]
