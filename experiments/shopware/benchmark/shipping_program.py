"""Install a recovered availability and pricing program in the joint deck."""
from hashlib import sha256

from collection_vm import ARITHMETIC, literal as L, record, ref as R
from graph_contract import encode
from program_deck import ProgramDeck
from shipping_rules import install as install_rules, offer_program
from trace_induction import substitute


def require(condition, value):
    if not condition:
        raise ValueError('Shipping method is unavailable for this cart')
    return value


class ShippingPredictor:
    def __init__(self, model, snapshot):
        self.model = model
        self.tags = model.tags
        self.offers = offer_program(snapshot, 'delivery.facts')

    def __call__(self, context):
        original = self.model(context)
        revised = []
        for name, expression in original:
            if name == 'updated':
                op, source, variable, row = expression
                expression = [op, source, variable, record(**row[1], weight=R(variable, 'weight'))]
            if name == 'order':
                expression = record(**expression[1], shipping=R('quote_in', 'shipping'))
            if name == 'quote':
                expression = record(**{**expression[1], 'shipping': record(
                    **expression[1]['shipping'][1], method=R('config', 'shipping.method'))},
                    shipping_options=R('delivery.offers'))
            revised.append((name, substitute(expression, R('config', 'shipping'), R('delivery.price'))))
            if name == 'subtotal':
                product = ['get', R('catalog_index'), R('line', 'id')]
                weight = ['sum', ['map', R('lines'), 'line',
                                  ['mul', R('line', 'quantity'), ['get', product, L('weight')]]]]
                revised.extend([
                    ('delivery.facts', record(country=R('config', 'shipping.country'),
                                              group=R('config', 'shipping.group'),
                                              amount=R('subtotal'), weight=weight)),
                    ('delivery.offers', self.offers),
                    ('delivery.selected', ['get_default', R('delivery.offers'), R('config', 'shipping.method'),
                                           L({'available': False, 'shipping': 0})]),
                    ('delivery.price', ['require', R('delivery.selected', 'available'),
                                        R('delivery.selected', 'shipping')]),
                ])
        return revised


def with_shipping(deck, snapshot):
    install_rules()
    if ARITHMETIC.setdefault('require', require) is not require:
        raise ValueError('Conflicting VM operation require')
    identity = sha256(encode((deck.checkpoint, snapshot)).encode()).hexdigest()
    return ProgramDeck(ShippingPredictor(deck.predict, snapshot), identity)
