"""Small exact dataflow interpreter with bounded record collections."""
from collections import defaultdict
from fractions import Fraction
from operator import add, mul, sub


ARITHMETIC = {'add': add, 'sub': sub, 'mul': mul, 'min': min, 'max': max,
              'div': lambda a, b: Fraction(a) / Fraction(b)}


def rounded(value):
    q = Fraction(value)
    numerator, denominator = abs(q.numerator), q.denominator
    magnitude = (2 * numerator + denominator) // (2 * denominator)
    return -magnitude if q < 0 else magnitude


def bounded(values):
    if not isinstance(values, list) or len(values) > 128:
        raise ValueError('Expected a bounded row list')
    return values


def evaluate(expression, environment):
    op, *args = expression
    if op == 'ref':
        result = environment[args[0]]
        for key in args[1:]:
            result = result[key]
        return result
    if op == 'literal':
        return args[0]
    if op == 'record':
        return {key: evaluate(term, environment) for key, term in args[0].items()}
    if op == 'vector':
        return [evaluate(term, environment) for term in args]
    if op == 'map':
        source, name, body = args
        return [evaluate(body, {**environment, name: row}) for row in bounded(evaluate(source, environment))]
    if op == 'group':
        source, key = args
        groups = defaultdict(list)
        for row in bounded(evaluate(source, environment)):
            groups[row[key]].append(row)
        return [{'key': key, 'rows': groups[key]} for key in sorted(groups)]
    if op == 'index':
        source, key = args
        rows = bounded(evaluate(source, environment))
        index = {row[key]: row for row in rows}
        if len(index) != len(rows):
            raise ValueError('Duplicate key in a typed input collection')
        return index
    values = [evaluate(term, environment) for term in args]
    if op == 'get':
        return values[0][values[1]]
    if op == 'get_default':
        return values[0].get(values[1], values[2])
    if op == 'sum':
        return sum(bounded(values[0]))
    if op == 'concat':
        return [item for sequence in values for item in bounded(sequence)]
    if op == 'round':
        return rounded(values[0])
    if op in ARITHMETIC:
        return ARITHMETIC[op](*values)
    raise ValueError(f'Unknown collection operation {op}')


def run(bindings, inputs):
    environment = dict(inputs)
    for name, expression in bindings:
        if name in environment:
            raise ValueError(f'Duplicate dataflow producer for {name}')
        environment[name] = evaluate(expression, environment)
    return environment


def ref(*path):
    return ['ref', *path]


def literal(value):
    return ['literal', value]


def record(**values):
    return ['record', values]
