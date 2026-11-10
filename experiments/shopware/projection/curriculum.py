"""UI teaching examples transcribed into typed graphs.

The lessons describe product cards, order administration, cart and checkout
from the repository's shopper and merchant references. They are offline
supervision. The serving package never imports this module.
"""
from itertools import product
import json
from pathlib import Path

from holodeck.projection import EVENTS, INTENTS

TREE_INPUTS = (tuple(INTENTS), tuple(intent for modes in INTENTS.values() for intent in modes),
               ('visual', 'dense'), ('empty', 'ready', 'blocked'), ('absent', 'present'))
TRACE = Path(__file__).resolve().parents[1] / 'neural/shopware.jsonl'
OBSERVED = {event['event'] for line in TRACE.read_text().splitlines()
            if (journey := json.loads(line))['split'] == 'train'
            for event in journey['events'] if event['status'] == 200}
ORDER_ACTIONS = tuple(e for e in EVENTS if e.startswith('order') and e in OBSERVED)


def item(kind, parent, source='_', title='_', text='_', *, fields=(), events=(), tools=(), filters=(), sorts=(), details=()):
    return {'kind': kind, 'parent': parent, 'source': source, 'title': title, 'text': text,
            'fields': fields, 'events': events, 'tools': tools, 'filters': filters, 'sorts': sorts, 'details': details}


def lesson(context):
    task, intent, style, cart, receipt = context
    root = item('stack', '_')
    if task == 'storefront':
        compare = intent == 'compare'
        catalog = item(
            'table' if compare or style == 'dense' else 'grid', 0, 'catalog',
            'Artikel im Vergleich' if compare else 'Das Sortiment',
            fields=('image', 'name', 'variant', 'sku', 'price', 'stock', 'availability') if compare or style == 'dense'
            else ('image', 'name', 'variant', 'description', 'price', 'availability'),
            events=('cart.add',), tools=('search', 'sort', 'filter') if compare or style == 'dense' else ('search',),
            filters=('availability',) if compare or style == 'dense' else (),
            sorts=('name','price','stock') if compare or style == 'dense' else (),
        )
        result = [root, item('hero', 0, title='{brand}',
                            text='Preise, Varianten und Bestand auf einen Blick.' if compare or style == 'dense'
                            else 'Gute Dinge für deinen Kaffee. Finde das Modell, das zu dir passt.'),
                  item('nav', 0, events=('go.storefront:browse', 'go.storefront:compare')), catalog]
        if cart != 'empty':
            result.append(item('list', 0, 'cart', 'Dein Warenkorb',
                               fields=('name', 'quantity', 'unit', 'gross'),
                               events=('cart.set', 'cart.remove', 'go.checkout:pay')))
        return result

    if task == 'checkout':
        if cart == 'empty':
            return [root, item('message', 0, title='Dein Warenkorb ist leer',
                               text='Wähle zuerst einen Artikel aus.', events=('go.storefront:browse',))]
        lines = item('list', 0, 'cart', 'Deine Auswahl', fields=('name', 'quantity', 'unit', 'gross'),
                     events=('cart.set', 'cart.remove'))
        if cart == 'blocked':
            return [root, item('message', 0, title='Der Bestand hat sich geändert',
                               text='Passe die Menge an oder entferne den Artikel.'), lines]
        costs = item('stats', 0, 'quote', 'Zusammenfassung',
                     fields=('goods', 'shipping', 'tax', 'gross') if style == 'visual' else ('net', 'tax', 'shipping', 'gross'))
        form = item('form', 0, title='Lieferung und Testzahlung', events=('checkout.place',))
        if style == 'dense':
            return [root, costs, lines, form]
        return [item('split', '_'), item('stack', 0),
                {**lines, 'parent': 1}, {**form, 'parent': 1}, costs]

    if task == 'order':
        return [root, item('detail', 0, 'receipt', 'Deine Bestellung',
                           fields=('id', 'stage', 'payment', 'delivery', 'name', 'address', 'gross', 'items', 'tracking'),
                           events=('go.storefront:browse',))] if receipt == 'present' else [
            root, item('message', 0, title='Hier erscheint deine Bestellung', text='Im Sortiment kannst du einen Kauf starten.',
                       events=('go.storefront:browse',))]

    nav = item('nav', 0, events=('go.admin:overview', 'go.admin:orders', 'go.admin:catalog'))
    inventory = item('table', 0, 'catalog', 'Sortiment und Bestand',
                     fields=('image', 'name', 'sku', 'variant', 'stock', 'price', 'availability'),
                     tools=('search', 'sort', 'filter'), filters=('availability',), sorts=('name','sku','stock','price'))
    orders = item('table', 0, 'orders', 'Bestellungen',
                  fields=('id', 'name', 'gross', 'stage', 'payment', 'delivery', 'tracking', 'items', 'address') if style == 'visual'
                  else ('id', 'delivery', 'name', 'stage', 'payment', 'gross', 'tracking', 'items', 'address'),
                  events=ORDER_ACTIONS, tools=('search', 'sort', 'filter', 'expand'),
                  filters=('stage', 'payment') if style == 'visual' else ('delivery', 'payment'),
                  sorts=('id','name','gross','stage','payment','delivery'), details=('address','items'))
    if intent == 'catalog':
        return [root, nav, inventory]
    if intent == 'orders':
        return [root, nav, orders]
    metrics = item('stats', 0, 'metrics', 'Auf einen Blick',
                   fields=('total_orders', 'paid_orders', 'open_deliveries', 'active_products') if style == 'visual'
                   else ('open_deliveries', 'active_products', 'total_orders', 'paid_orders'))
    return [root, nav, metrics, orders, inventory] if style == 'visual' else [root, nav, metrics, inventory, orders]


def lessons():
    for task, intents in INTENTS.items():
        for intent, style, cart, receipt in product(intents, *TREE_INPUTS[2:]):
            context = (task, intent, style, cart, receipt)
            # Hold out both ordinary cross product cells and an entire unseen graph.
            held = (cart == 'blocked' and receipt == 'present') or (
                task == 'storefront' and intent == 'compare' and cart != 'empty'
            )
            yield held, context, lesson(context)


def wires(event):
    # From the reference forms and event payloads. Only training reads these.
    return {name: {
        'sku': 'row.sku', 'order_id': 'row.id', 'quote_key': 'context.quote',
        'quantity': 'const.one' if event == 'cart.add' else 'input',
    }.get(name, 'input') for name in EVENTS[event][1]} | {
        'navigate': 'order:status' if event == 'checkout.place' else '_'
    }
