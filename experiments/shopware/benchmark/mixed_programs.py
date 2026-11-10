"""Candidate collection programs for Shopware trace induction."""
from itertools import product

from collection_vm import literal as L, record, ref as R, run


def term(op, *values):
    return [op, *values]


def collect(source, name, expression):
    return ['map', source, name, expression]


def summing(source, name, expression):
    return term('sum', collect(source, name, expression))


def product_of(a, b):
    return term('mul', a, b)


def rounded(value):
    return term('round', value)


def fraction(a, b):
    return term('div', a, b)


def tax_raw(amount, rate):
    return fraction(product_of(amount, rate), term('add', L(100), rate))


def group_bands(source):
    return collect(['group', source, 'rate'], 'g', record(
        rate=R('g', 'key'),
        gross=summing(R('g', 'rows'), 'r', R('r', 'gross')),
        tax=summing(R('g', 'rows'), 'r', R('r', 'tax'))))


def display_bands(source):
    return collect(source, 'b', term('vector', R('b', 'rate'), R('b', 'gross'), R('b', 'tax')))


def line_program(quantity, rounding):
    qty = R('request', 'quantity')
    if quantity == 'cap':
        qty = term('min', qty, R('product', 'stock'))
    gross = product_of(R('product', 'unit'), qty)
    rate = R('product', 'rate')
    amount = (product_of(rounded(tax_raw(R('product', 'unit'), rate)), qty)
              if rounding == 'unit' else rounded(tax_raw(gross, rate)))
    row = record(id=R('request', 'id'), quantity=qty, gross=gross, rate=rate, tax=amount)
    # Bind the linked product in a one-row map, preserving the request key.
    linked = ['map', term('vector', term('get', R('catalog_index'), R('request', 'id'))), 'product', row]
    lines = collect(R('items'), 'request', term('get', linked, L(0)))
    return [('catalog_index', ['index', R('catalog'), 'id']),
            ('lines', lines), ('base', group_bands(R('lines')))]


def allocated(source, amount, basis, rounding):
    def weight(var):
        if basis == 'equal':
            return L(1)
        if basis == 'net':
            return term('sub', R(var, 'gross'), R(var, 'tax'))
        return R(var, basis)

    raw = fraction(product_of(amount, weight('r')), summing(source, 'b', weight('b')))
    cost = rounded(raw)
    vat = rounded(tax_raw(cost if rounding == 'share-first' else raw, R('r', 'rate')))
    return collect(source, 'r', record(rate=R('r', 'rate'), gross=cost, tax=vat))


def discount_amount(mode, strategy):
    subtotal = R('subtotal')
    value = R('config', 'discount_value')
    if mode == 'percentage':
        if strategy == 'per-line':
            magnitude = summing(R('lines'), 'line', rounded(
                fraction(product_of(R('line', 'gross'), value), L(100))))
        else:
            magnitude = rounded(fraction(product_of(subtotal, value), L(100)))
    else:
        magnitude = term('min', subtotal, value) if strategy == 'cap' else value
    return term('sub', L(0), magnitude)


def quote_program(mode, opts):
    program = line_program(opts['quantity'], opts['item_round'])
    program += [('subtotal', summing(R('base'), 'b', R('b', 'gross')))]
    if mode == 'none':
        program += [('discount_value', L(0)), ('discount_bands', L([]))]
        shown_discount = L(None)
    else:
        program += [('discount_value', discount_amount(mode, opts['discount_amount'])),
                    ('discount_bands', allocated(R('base'), R('discount_value'),
                                                  opts['discount_basis'], opts['discount_round']))]
        shown_discount = record(gross=R('discount_value'), taxes=display_bands(R('discount_bands')))
    program += [('adjusted', group_bands(term('concat', R('base'), R('discount_bands'))))]
    shipping_base = R('adjusted') if opts['shipping_stage'] == 'adjusted' else R('base')
    program += [('shipping_bands', allocated(shipping_base, R('config', 'shipping'),
                                             opts['shipping_basis'], opts['shipping_round'])),
                ('total_bands', group_bands(term('concat', R('adjusted'), R('shipping_bands')))),
                ('goods', term('add', R('subtotal'), R('discount_value'))),
                ('gross', term('add', R('goods'), R('config', 'shipping'))),
                ('net', term('sub', R('gross'), summing(R('total_bands'), 'b', R('b', 'tax'))))]
    line = record(id=R('line', 'id'), quantity=R('line', 'quantity'),
                  gross=R('line', 'gross'), taxes=term('vector', term('vector',
                      R('line', 'rate'), R('line', 'gross'), R('line', 'tax'))))
    program += [('quote', record(
        items=['index', collect(R('lines'), 'line', line), 'id'],
        discount=shown_discount,
        shipping=record(gross=R('config', 'shipping'), taxes=display_bands(R('shipping_bands'))),
        goods=R('goods'), gross=R('gross'), net=R('net'), taxes=display_bands(R('total_bands'))))]
    return program


def predict_quote(program, row):
    result = run(program, {'catalog': row['catalog'], 'items': row['cart_input'], 'config': row['config']})['quote']
    result['items'] = {identity: {key: value for key, value in line.items() if key != 'id'}
                       for identity, line in result['items'].items()}
    return result


def base_candidates():
    for qty, line, basis, rounding in product(
            ('cap', 'copy'), ('line', 'unit'), ('gross', 'net', 'tax', 'equal'), ('exact', 'share-first')):
        yield {'quantity': qty, 'item_round': line, 'shipping_basis': basis,
               'shipping_round': rounding, 'shipping_stage': 'base'}


def promo_candidates():
    for amount, basis, stage in product(('total', 'per-line'),
                                          ('gross', 'net', 'tax', 'equal'), ('base', 'adjusted')):
        yield {'percentage_amount': amount, 'discount_basis': basis, 'shipping_stage': stage}


def promo_options(mode, base, promo, absolute):
    return {**base, 'discount_amount': promo['percentage_amount'] if mode == 'percentage' else absolute,
            'discount_round': base['shipping_round'], 'discount_basis': promo['discount_basis'],
            'shipping_stage': promo['shipping_stage']}


def checkout_program(options, initial_state):
    quantity = term('get', term('get_default', R('quote', 'items'), R('product', 'id'), L({'quantity': 0})),
                    L('quantity'))
    stock = R('product', 'stock')
    if options['stock'] == 'subtract':
        stock = term('sub', stock, quantity)
    updated = collect(R('catalog'), 'product', record(
        id=R('product', 'id'), unit=R('product', 'unit'), rate=R('product', 'rate'), stock=stock))
    stock_rows = collect(R('updated'), 'p', record(id=R('p', 'id'), stock=R('p', 'stock')))
    return [('updated', updated), ('stock_rows', ['index', stock_rows, 'id']),
            ('order', record(gross=R('quote', 'gross'), net=R('quote', 'net'),
                taxes=R('quote', 'taxes'), lines=R('quote', 'items'), stock=R('stock_rows'),
                **{key: L(value) for key, value in initial_state.items()}))]


def predict_order(program, catalog, quote):
    result = run(program, {'catalog': catalog, 'quote': quote})
    order = result['order']
    order['stock'] = {identity: value['stock'] for identity, value in order['stock'].items()}
    return order, result['updated']
