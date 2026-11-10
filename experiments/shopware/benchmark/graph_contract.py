"""Common graph and executor for all comparison arms. No commerce dispatch."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from vm import Store, evaluate, retrieve, root_of
from graph_ir import GRAPH, evaluate_graph


FIELD_TYPES = {
    'product.name': str, 'product.price': int, 'product.stock': int,
    'cart.quantity': int, 'cart.goods': int, 'cart.gross': int,
    'cart.shipping': int, 'cart.net': int, 'cart.tax': int,
    'cart.discount': int, 'cart.accepted': bool,
    'order.gross': int, 'order.net': int,
    'order.payment': str, 'order.delivery': str, 'order.stage': str,
    'result.accepted': bool, 'holo.view': dict,
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def initial(row):
    return {f'product.{key}': value for key, value in row['initial'].items()}


def config(row):
    return {'rate': row['rate'], 'shipping': row['shipping'],
            **{f'coupon.{key}': value for key, value in row['policy'].items()}}


def graph(tenant, root, fields, settings):
    return {'tenant': tenant, 'root': root, 'revision': 0, 'config': settings,
            'objects': [{'id': root, 'kind': 'document', 'parent': None, 'fields': fields}]}


def context(fields, settings, request):
    # Exact scalar operands remain available to the VM. The controller sees
    # presence, categorical values, event, and optional natural language.
    groups = {'record': fields, 'setting': settings, 'argument': request['args']}
    tokens = [f"event={request['kind']}"]
    for scope, values in groups.items():
        for key, value in sorted(values.items()):
            if key == 'text' or key.startswith(('result.', 'holo.')):
                continue
            tokens.append(f'{scope}.{key}')
            if key in ('order.stage', 'order.payment', 'order.delivery', 'coupon.mode', 'coupon.active'):
                tokens.append(f'{scope}.{key}={value}')
    return tuple(tokens), request['args'].get('text', '')


def read(scope, field):
    return ['read', scope, field]


def literal(value):
    return ['const', value]


def value(expression, obj, state, request):
    if expression[0] == 'const':
        return copy.deepcopy(expression[1])
    if expression[0] == 'read':
        scopes = {'self': obj['fields'], 'root': root_of(state)['fields'],
                  'config': state['config'], 'event': request}
        return scopes[expression[1]][expression[2]]
    return evaluate(expression, obj, state, request)


def execute(program, state, event, tenant):
    retrieve(state, event, tenant)
    current = root_of(state)
    request = {'id': event['id'], 'root': event['root'], **event['args']}
    def compute(expr):
        return value(expr, current, state, request)

    writes = evaluate_graph(program[GRAPH], compute) if GRAPH in program else {}
    for field, expr in program.items():
        if field == GRAPH:
            continue
        if field in writes:
            raise ValueError('Duplicate writer')
        writes[field] = compute(expr)
    for field, result in writes.items():
        if type(result) is not FIELD_TYPES[field]:
            raise ValueError(f'Wrong output type for {field}')
        if type(result) is int and abs(result) >= 2**63:
            raise ValueError('Integer range')
    after = copy.deepcopy(state)
    root_of(after)['fields'].update(writes)
    return {'graph': after, 'writes': writes, 'program': program}


class Deck:
    def __init__(self, predict, name):
        self.predict = predict
        self.name = name

    def forward(self, state, event, tenant):
        fields = root_of(state)['fields']
        candidates = self.predict(context(fields, state['config'], event))
        errors = []
        for program in candidates:
            try:
                return {**execute(program, state, event, tenant), 'model': self.name,
                        'candidate_attempts': len(errors) + 1}
            except (KeyError, ValueError, TypeError) as error:
                errors.append(str(error))
        raise ValueError(f'No executable candidate: {errors}')
