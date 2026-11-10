"""Source-informed arithmetic hypotheses, used only during compilation."""
from collection_vm import literal as L, record, ref as R
from mixed_programs import collect, product_of, summing, term, display_bands


def fdiv(a, b):
    return term('f64_div', a, b)


def fmul(a, b):
    return term('f64_mul', a, b)


def euros(cents):
    return fdiv(cents, L(100))


def normalized(value, digits):
    return term('f64_significant', value, L(digits))


def cents(value):
    return term('php82_integral', value, L(2))


def cash(value, scope):
    interval = term('get_default', R('config'), L(scope + '_increment'), L(1))
    return term('php82_quantize', value, interval)


def band(price, rate, percentage, qty, digits):
    base = fmul(fdiv(price, L(100)), percentage)
    raw_tax = fmul(fdiv(base, fdiv(term('add', L(100), rate), L(100))), fdiv(rate, L(100)))
    return record(rate=rate, gross=cents(fmul(normalized(base, digits), qty)),
                  tax=cents(fmul(normalized(raw_tax, digits), qty)))


def allocated(source, amount, total, digits):
    rows = term('get', R('allocation'), L('source'))
    weight = R('r', 'gross')
    divisor = term('get', R('allocation'), L('total'))
    # A per-call denominator is injected into this reusable collection cell.
    ratio = fmul(fdiv(euros(weight), euros(divisor)), L(100))
    share = normalized(ratio, digits)
    shares = collect(rows, 'r', band(euros(amount), R('r', 'rate'), share, L(1), digits))
    context = term('vector', record(source=source, total=total))
    return term('get', collect(context, 'allocation', shares), L(0))


def lines(digits):
    qty = term('min', R('request', 'quantity'), R('product', 'stock'))
    unit = cash(euros(R('product', 'unit')), 'item')
    price = band(euros(unit), R('product', 'rate'), L(100), qty, digits)[1]
    line = record(id=R('request', 'id'), quantity=qty, gross=product_of(unit, qty),
                  rate=price['rate'], tax=price['tax'])
    link = term('get', R('catalog_index'), R('request', 'id'))
    return collect(R('items'), 'request', term('get', collect(term('vector', link), 'product', line), L(0)))


def variant(cells, digits, denominator, stage):
    if 'shipping_bands' not in cells:
        return cells
    result = dict(cells)
    result['lines'] = lines(digits)
    subtotal, goods = R('subtotal'), R('goods')
    previous = cells['discount_value']
    if previous != L(0):
        factor = R('config', 'discount_value')
        if cells['quote'][1]['discount'] == L(None):
            raise ValueError('Discount amount and view have inconsistent atoms')
        # The original learned amount atom contains either a cap or a
        # percentage expression. Its operands are kept in the new atom.
        amount = previous[2]
        if amount == term('min', subtotal, factor):
            value = euros(amount)
        elif amount == term('round', term('div', term('mul', subtotal, factor), L(100))):
            value = fmul(fdiv(euros(subtotal), L(100)), factor)
        else:
            raise ValueError('Unrecognized discount amount atom')
        result['discount_value'] = term('sub', L(0), cash(value, 'item'))
        result['discount_bands'] = allocated(R('base'), R('discount_value'), subtotal, digits)
    shipping = cash(euros(R('config', 'shipping')), 'item')
    old_shipping = cells['shipping_bands']
    if old_shipping[0] == 'vector':
        rate = old_shipping[1][1]['rate']
        result['shipping_bands'] = term('vector', band(euros(shipping), rate, L(100), L(1), digits))
    else:
        source = 'base' if stage == 'base' else old_shipping[1][1]
        shipping_basis = R(source)
        total = summing(shipping_basis, 'b', R('b', 'gross')) if denominator == 'bands' else (
            goods if source == 'adjusted' else subtotal)
        result['shipping_bands'] = allocated(shipping_basis, shipping, total, digits)
    gross = term('add', goods, shipping)
    result['gross'] = cash(euros(gross), 'total')
    result['net'] = term('sub', gross, summing(R('total_bands'), 'b', R('b', 'tax')))
    result['quote'] = record(**{**cells['quote'][1],
                               'shipping': record(gross=shipping, taxes=display_bands(R('shipping_bands')))})
    return result
