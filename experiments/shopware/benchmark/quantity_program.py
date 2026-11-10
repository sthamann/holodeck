"""One executable finite progression with a read and a write projection."""
from hashlib import sha256
import json

from collection_vm import ARITHMETIC, evaluate, literal as L, record, ref as R
from program_deck import ProgramDeck
from trace_induction import substitute


INPUT_MAX = 999
FIELDS = ('minimum', 'maximum', 'step', 'stock', 'closeout')
MODES = ('floor', 'ceil', 'nearest', 'reject')


def options(bounds):
    lo, hi, stride = (bounds[key] for key in ('start', 'end', 'stride'))
    if any(type(value) is not int for value in (lo, hi, stride)) or not (
            1 <= lo <= INPUT_MAX and 0 <= hi <= INPUT_MAX and 1 <= stride <= INPUT_MAX):
        raise ValueError('Invalid finite progression')
    return range(lo, hi + 1, stride)


def project(bounds, request, mode):
    choices = options(bounds)
    if not choices:
        return 0
    if mode == 'reject':
        return request if request in choices else 0
    start, stop, stride = choices[0], choices[-1], choices.step
    request = min(max(start, request), stop)
    index, remainder = divmod(request - start, stride)
    if (mode == 'ceil' and remainder) or (mode == 'nearest' and remainder * 2 > stride):
        index += 1
    return start + index * stride


def choose(condition, if_true, if_false):
    return if_true if condition else if_false


def filter_nonzero(rows, key):
    return [row for row in rows if row[key] != 0]


def require_items(items, value):
    if not items:
        raise ValueError('Der Warenkorb enthält keine bestellbare Menge.')
    return value


def install():
    primitives = {'grid_project': project, 'select_value': choose,
                  'filter_nonzero': filter_nonzero, 'require_nonempty': require_items}
    for name, operation in primitives.items():
        if ARITHMETIC.setdefault(name, operation) is not operation:
            raise ValueError(f'Conflicting opcode {name}')


def values_for(tree, product):
    return options(evaluate(tree, {'product': product}))


def line_record(expression):
    return expression[3][1][3][1]


class QuantityPredictor:
    def __init__(self, model, learned):
        self.model = model
        self.tags = model.tags
        self.quantity_domain = learned['domain']
        mode = learned['mode']
        if mode not in MODES:
            raise ValueError('A cart program needs an identified correction mode')
        self.normalize = ['grid_project', self.quantity_domain, R('request', 'quantity'), L(mode)]

    def __call__(self, context):
        program = self.model(context)
        if 'state.mixed.catalog' not in context[0]:
            return program
        revised = []
        for name, expression in program:
            if name == 'lines':
                previous = line_record(expression)['quantity']
                expression = substitute(expression, previous, self.normalize)
                expression = ['filter_nonzero', expression, L('quantity')]
            elif name == 'updated':
                op, source, variable, row = expression
                expression = [op, source, variable, record(
                    **{**row[1], **{field: R(variable, field) for field in FIELDS if field != 'stock'}})]
            elif name == 'order':
                expression = ['require_nonempty', R('quote_in', 'items'), expression]
            elif name == 'write:mixed.items':
                continue
            revised.append((name, expression))
        if any(name == 'lines' for name, _ in revised):
            revised.append(('write:mixed.items', ['map', R('lines'), 'line', record(
                id=R('line', 'id'), quantity=R('line', 'quantity'))]))
        return revised


def with_quantity(deck, checkpoint_path):
    install()
    raw = checkpoint_path.read_bytes()
    checkpoint = json.loads(raw)
    if checkpoint['supervision'] != 'cart' or checkpoint['exact_domains'] != 1:
        raise ValueError('The effect program requires one identified domain from cart supervision')
    identity = sha256(deck.checkpoint.encode() + raw).hexdigest()
    return ProgramDeck(QuantityPredictor(deck.predict, checkpoint), identity)
