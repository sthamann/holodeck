"""Shared expression graph. Nodes can only depend on earlier nodes."""
import json

GRAPH = '$dataflow'
ARITY = {'add': 2, 'sub': 2, 'mul': 2, 'muldiv': 3, 'min': 2, 'max': 2,
         'eq': 2, 'le': 2}


def share(expressions):
    """Intern identical subexpressions in a topologically ordered graph."""
    nodes, lookup = [], {}

    def visit(expression):
        op, *args = expression
        key = json.dumps(expression, sort_keys=True)
        if key not in lookup:
            node = expression if op in ('const', 'read') else [op, *[visit(arg) for arg in args]]
            lookup[key] = len(nodes)
            nodes.append(node)
        return lookup[key]

    outputs = {field: visit(expressions[field]) for field in sorted(expressions)}
    return {'nodes': nodes, 'outputs': outputs}


def evaluate_graph(plan, evaluate):
    nodes = plan['nodes']
    if not isinstance(nodes, list) or len(nodes) > 80:
        raise ValueError('Graph exceeds node budget')
    values = []
    for node in nodes:
        op, *operands = node
        if op in ('const', 'read'):
            if len(operands) != (1 if op == 'const' else 2):
                raise ValueError('Malformed graph leaf')
            values.append(evaluate(node))
            continue
        if op not in ARITY or ARITY[op] != len(operands):
            raise ValueError('Unsupported graph node')
        if not all(type(i) is int and 0 <= i < len(values) for i in operands):
            raise ValueError('A graph operand must refer to an earlier node')
        values.append(evaluate([op, *[['const', values[i]] for i in operands]]))
    outputs = plan['outputs']
    if not all(type(i) is int and 0 <= i < len(values) for i in outputs.values()):
        raise ValueError('Invalid graph output')
    return {field: values[i] for field, i in outputs.items()}


def expand(plan):
    """Normalize a graph for offline comparison with tree supervision."""
    trees = []
    for op, *operands in plan['nodes']:
        trees.append([op, *operands] if op in ('const', 'read') else [op, *[trees[i] for i in operands]])
    return {field: trees[i] for field, i in plan['outputs'].items()}
