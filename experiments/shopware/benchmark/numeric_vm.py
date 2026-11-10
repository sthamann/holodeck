"""Versioned scalar primitives. Intermediate binary64 values never leave the VM."""
from math import floor, isfinite, log10

from collection_vm import ARITHMETIC


def finite(value):
    value = float(value)
    if not isfinite(value) or abs(value) >= 1e10:
        raise ArithmeticError('Value outside binary64 profile bounds')
    return value


def divide(a, b):
    return finite(finite(a) / finite(b))


def multiply(a, b):
    return finite(finite(a) * finite(b))


def significant(value, digits):
    if type(digits) is not int or not 1 <= digits <= 17:
        raise ValueError('Invalid binary64 precision')
    return float(format(finite(value), f'.{digits}g'))


def integral(value, places):
    """Bounded PHP 8.2 half-away rounding, returned as a scaled integer."""
    value = finite(value)
    if type(places) is not int or not 0 <= places <= 4:
        raise ValueError('Unsupported scale')
    if not value:
        return 0
    magnitude = abs(value)
    precision = 14 - floor(log10(magnitude))
    shifted = (floor(magnitude * 10.0 ** precision + 0.5) / 10.0 ** (precision - places)
               if places < precision < places + 15 else magnitude * 10.0 ** places)
    result = floor(shifted + 0.5)
    return -result if value < 0 else result


def quantize(value, increment):
    if type(increment) is not int or not 1 <= increment <= 100:
        raise ValueError('Invalid minor-unit increment')
    cents = integral(value, 2)
    multiplier = 100.0 / increment
    return integral((cents / 100) * multiplier, 0) * increment


def install():
    primitives = {'f64_div': divide, 'f64_mul': multiply, 'f64_significant': significant,
                  'php82_integral': integral, 'php82_quantize': quantize}
    for name, operation in primitives.items():
        if ARITHMETIC.setdefault(name, operation) is not operation:
            raise ValueError(f'Numeric opcode conflict: {name}')
