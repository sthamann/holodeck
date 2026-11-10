"""Run the trained transition network and exact integer operand heads."""
from functools import lru_cache
from hashlib import sha256
from itertools import product
import json
from pathlib import Path

import numpy as np

from .runtime import Refusal, rounded_product


@lru_cache(maxsize=None)
def arithmetic_pool(size):
    """The same typed arithmetic vocabulary is available to every output field."""
    registers = tuple(range(size + 3))  # input registers, zero, one, hundred
    pool = [(0, i) for i in registers]
    for op in (1, 2, 3):
        pool.extend((op, a, b) for a,b in product(registers, repeat=2))
    for a,b,c in product(range(size), repeat=3):
        pool.extend(((4, a,b,c), (5, a,b,c)))
    for op in (6, 7):
        pool.extend((op,a,b) for a,b in product(registers, repeat=2))
    for op in (8, 9):
        pool.extend((op, inner,a,b,c) for inner,a,b,c in product((1,2,3),range(size),range(size),range(size)))
    return tuple(pool)


def evaluate(program, inputs):
    op,a,*args = program
    values = (*inputs,0,1,100)
    x = values[a]
    if op == 0:
        return x
    if op in (8,9):
        inner,b,c,d = (a,*args)
        return (min if op==8 else max)(evaluate((inner,b,c),inputs), values[d])
    y = values[args[0]]
    if op == 1:
        return x+y
    if op == 2:
        return x-y
    if op == 3:
        return x*y
    if op in (6,7):
        return (min if op==6 else max)(x,y)
    divisor = values[args[1]] + (100 if op == 5 else 0)
    return rounded_product(x,y,divisor) if divisor > 0 else None


def features(meta, event, observed):
    event_index = meta['events'].index(event)
    value_index = meta['observations'].index(observed)
    result = np.zeros(len(meta['events'])+len(meta['observations']),dtype=np.float32)
    result[event_index] = 1
    result[len(meta['events'])+value_index] = 1
    return result


class Weights:
    def __init__(self, path):
        path = Path(path)
        self.identity = sha256(path.read_bytes()).hexdigest()
        with np.load(path, allow_pickle=False) as archive:
            self.meta = json.loads(str(archive['schema']))
            self.values = {key:archive[key].copy() for key in archive.files if key != 'schema'}
        if self.meta['format'] != 2:
            raise ValueError('Unsupported neural checkpoint')

    def decide(self, event, observed):
        if event not in self.meta['events'] or observed not in self.meta['observations']:
            return False, observed
        v = features(self.meta,event,observed)
        for layer in (0, 2, 4):
            v = self.values[f'net.{layer}.weight'] @ v + self.values[f'net.{layer}.bias']
            if layer != 4:
                v = np.maximum(v,0)
        index = int(np.argmax(v))
        confidence = 1 / np.exp(v-v[index]).sum()
        if confidence < 0.95:
            return False, observed
        output = self.meta['outputs'][index]
        return output != '<refused>', observed if output in ('<refused>','<same>') else output

    def integer(self, head, *inputs):
        scores = self.values['int.'+head]
        pool = arithmetic_pool(len(inputs))
        if self.meta['arities'][head] != len(inputs) or scores.shape != (len(pool),):
            raise ValueError('Incompatible integer head shape')
        index = int(np.argmax(scores))
        if np.count_nonzero(scores == scores[index]) != 1:
            raise Refusal('Der Modellstand liefert keine eindeutige Berechnung.')
        program = pool[index]
        result = evaluate(program, inputs)
        if result is None:
            raise Refusal('Die Berechnung hat keinen definierten Wert.')
        return result
