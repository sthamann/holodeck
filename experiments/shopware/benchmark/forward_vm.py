"""Lower a bounded expert bank to exact PyTorch FX forward graphs.

Lowering consumes the expression grammar at load time. A request dispatches
registered model subgraphs and passes typed values between them. Exact custom
kernels retain the reference integer, rational and bounded binary64 semantics.
"""
from collections import defaultdict
from fractions import Fraction
from hashlib import sha256
from operator import getitem
from pathlib import Path

import torch
from torch import fx, nn

from collection_vm import ARITHMETIC, bounded, rounded
from program_deck import ProgramDeck
from program_deck_sparse import load


def record(values):
    return values


def vector(*values):
    return list(values)


def divide(left, right):
    return Fraction(left) / Fraction(right)


def indexed(values, key):
    rows = bounded(values)
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError('Duplicate key in a typed input collection')
    return result


def grouped(values, key):
    groups = defaultdict(list)
    for row in bounded(values):
        groups[row[key]].append(row)
    return [{'key': value, 'rows': groups[value]} for value in sorted(groups)]


def sum_rows(values):
    return sum(bounded(values))


def concat(*values):
    return [row for value in values for row in bounded(value)]


def get_default(values, key, default):
    return values.get(key, default)


class ExactLinear(nn.Module):
    """Two coefficient weights with exact scalar evaluation for ints and fractions."""
    def __init__(self, coefficients):
        super().__init__()
        self.weight = nn.Parameter(torch.tensor(coefficients, dtype=torch.int64), requires_grad=False)

    def forward(self, left, right):
        a, b = self.weight.tolist()
        # This range also bounds both products and their sum in int64.
        if type(left) is int and type(right) is int and max(abs(left), abs(right)) < 2**61 and max(abs(a), abs(b)) <= 1:
            values = torch.tensor((left, right), dtype=torch.int64)
            return torch.dot(self.weight, values).item()
        return a * left + b * right


class MapRows(nn.Module):
    def __init__(self, name, body):
        super().__init__()
        self.name, self.body = name, body

    def forward(self, values, env):
        return [self.body.forward({**env, self.name: row}) for row in bounded(values)]


class Lowering:
    def __init__(self):
        self.module, self.graph = nn.Module(), fx.Graph()
        self.env = self.graph.placeholder('env')
        self.children = 0

    def call(self, module, *args):
        name = f'cell_{self.children}'
        self.children += 1
        self.module.add_module(name, module)
        return self.graph.call_module(name, args)

    def expression(self, expression):
        op, *args = expression
        match op:
            case 'literal':
                return args[0]
            case 'ref':
                value = self.env
                for key in args:
                    value = self.graph.call_function(getitem, (value, key))
                return value
            case 'record':
                return self.graph.call_function(record, ({key: self.expression(term)
                                                          for key, term in args[0].items()},))
            case 'map':
                source, name, body = args
                return self.call(MapRows(name, Lowering().compile(body)), self.expression(source), self.env)
            case 'index' | 'group':
                source, key = args
                kernel = indexed if op == 'index' else grouped
                return self.graph.call_function(kernel, (self.expression(source), key))
            case 'add' | 'sub':
                coefficients = (1, 1 if op == 'add' else -1)
                return self.call(ExactLinear(coefficients), *(self.expression(term) for term in args))
        kernels = {'get': getitem, 'get_default': get_default, 'sum': sum_rows,
                   'concat': concat, 'round': rounded, 'div': divide, 'vector': vector}
        kernel = kernels.get(op) or ARITHMETIC.get(op)
        if kernel is None:
            raise ValueError(f'No forward kernel for {op}')
        return self.graph.call_function(kernel, tuple(self.expression(term) for term in args))

    def compile(self, expression):
        self.graph.output(self.expression(expression))
        return fx.GraphModule(self.module, self.graph)


class ForwardVM(nn.Module):
    """Use a learned routing result to select already compiled cell forwards."""
    def __init__(self, grammar):
        super().__init__()
        self.experts = nn.ModuleList()
        self.addresses = {}
        # Keep the selected expressions alive so identity-based dispatch is
        # stable and an unregistered expression cannot enter the model.
        self.grammar = grammar
        for name, alternatives in grammar.atoms.items():
            for expression in alternatives:
                if expression is None:
                    continue
                self.addresses[name, id(expression)] = len(self.experts)
                self.experts.append(Lowering().compile(expression))
        self.calls = 0

    def forward(self, bindings, inputs):
        self.calls += 1
        environment = dict(inputs)
        for name, selected in bindings:
            if name in environment:
                raise ValueError(f'Duplicate dataflow producer for {name}')
            address = self.addresses.get((name, id(selected)))
            if address is None:
                raise ValueError('Program cell is outside the compiled expert bank')
            # A projection can intentionally probe a cell with missing inputs.
            # Call the generated forward directly so FX does not print that refusal.
            environment[name] = self.experts[address].forward(environment)
        return environment


class TensorRouter(nn.Module):
    """Execute the published sparse gate weights in the model runtime."""
    def __init__(self, decoder):
        super().__init__()
        self.decoder = decoder
        self.tags = decoder.tags
        self.weight = nn.Parameter(torch.from_numpy(decoder.weights.copy()), requires_grad=False)

    def forward(self, context):
        values = set(context[0])
        missing = torch.tensor([tag not in values for tag in self.tags], dtype=self.weight.dtype)
        return self.decoder.decode_enabled((self.weight @ missing == 0).numpy())


def load_forward(directory):
    source = load(directory)
    model = ForwardVM(source.predict.grammar).eval()
    compiler = sha256(Path(__file__).read_bytes()).hexdigest()
    return ProgramDeck(TensorRouter(source.predict).eval(), f'{source.checkpoint}-forward-{compiler}', model)
