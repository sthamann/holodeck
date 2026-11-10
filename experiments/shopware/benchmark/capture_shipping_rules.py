"""Record Rule Builder availability and selected delivery quotes from Shopware."""
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from itertools import product
import json
from pathlib import Path
from uuid import uuid4

from capture_learning import api

from shipping_cases import CONTEXTS, COUNTRIES, GROUPS, HOLDOUT_CONTEXTS

D = ('country', '=', 'DE')
A = ('country', '=', 'AT')
T = ('group', '=', 'trade')
R = ('group', '=', 'retail')
P = ('amount', '>=', 6000)
H = ('amount', '>=', 9000)
W = ('weight', '>=', 3000)
B = ('weight', '>=', 5000)
L = ('weight', '<', 3000)
M = ('weight', '<', 5000)
TRAIN = (
    ('and', D, ('or', P, T)),
    ('and', A, ('or', H, R)),
    ('or', D, ('and', T, M)),
    ('and', A, ('or', T, B)),
    ('and', D, T, P),
    ('and', A, ('or', R, H), M),
    ('or', A, ('and', T, P)),
    ('and', D, ('or', R, H)),
    ('and', A, T, ('or', P, M)),
    ('or', ('and', D, T), ('and', H, L)),
    ('and', D, ('or', T, W)),
    ('and', A, ('or', R, L)),
    ('or', D, ('and', T, H, B)),
    ('and', D, ('or', R, P), W),
    ('and', A, R, P),
    ('and', A, ('or', T, P)),
    ('or', A, ('and', R, L)),
    ('and', D, T, ('or', H, B)),
    ('and', A, ('or', T, W), P),
    ('or', ('and', A, R), ('and', P, M)),
    ('and', A, ('or', R, W)),
    ('and', D, ('or', T, M)),
    ('or', A, ('and', R, H, W)),
    ('and', D, ('or', R, B), H),
)
TEST = (
    ('and', D, ('or', P, R), L),
    ('or', A, ('and', P, T, W)),
    ('and', A, ('or', H, M)),
    ('or', ('and', D, R), ('and', P, B)),
    ('and', D, P, ('or', R, W)),
    ('and', A, ('or', H, T)),
    ('or', D, ('and', H, R, M)),
    ('and', D, ('or', P, T), M),
    ('or', ('and', D, P), ('and', T, L)),
    ('and', A, ('or', W, R), H),
    ('and', D, ('or', H, W)),
    ('or', A, ('and', P, R, L)),
    ('and', A, T, ('or', H, L)),
    ('or', ('and', A, B), ('and', R, H)),
    ('and', D, ('or', H, M), T),
    ('or', D, ('and', T, W)),
)
XOR = ('or', ('and', D, R), ('and', A, T))
CHALLENGES = (XOR, ('or', ('and', A, R), ('and', D, T)),
              ('or', ('and', D, L), ('and', A, W)),
              ('or', ('and', R, P), ('and', T, ('amount', '<', 6000))))


def seed_rule(oracle, expression, countries, groups, name):
    conditions = []

    def visit(node, parent, position):
        identity = uuid4().hex
        item = {'id': identity, 'position': position}
        if parent is not None:
            item['parentId'] = parent
        if node[0] in ('and', 'or'):
            item['type'] = node[0] + 'Container'
            conditions.append(item)
            for index, child in enumerate(node[1:]):
                visit(child, identity, index)
        else:
            feature, operator, operand = node
            kind, field, value = {
                'country': lambda: ('customerShippingCountry', 'countryIds', [countries[operand]]),
                'group': lambda: ('customerCustomerGroup', 'customerGroupIds', [groups[operand]]),
                'amount': lambda: ('cartGoodsPrice', 'amount', operand / 100),
                'weight': lambda: ('cartWeight', 'weight', operand / 1000),
            }[feature]()
            conditions.append({**item, 'type': kind, 'value': {'operator': operator, field: value}})
    visit(expression, None, 0)
    return {'id': uuid4().hex, 'name': name, 'priority': 10, 'conditions': conditions}


def merchant(index, phase, formulas, contexts):
    oracle = api.Oracle(575)
    lookup = oracle.search('country', {'filter': [{'type': 'equalsAny', 'field': 'iso', 'value': '|'.join(COUNTRIES)}]})
    countries = {row['iso']: row['id'] for row in lookup}
    oracle.sync('country', [{'id': identity, 'active': True, 'shippingAvailable': True} for identity in countries.values()])
    groups = {'retail': oracle.channel['customerGroupId'], 'trade': uuid4().hex}
    oracle.sync('customer_group', [{'id': groups['trade'], 'name': 'Holodeck Freight Trade', 'displayGross': True}])
    shop = f'Freight {phase} {index + 1}'
    oracle.sync('sales_channel', [{'id': oracle.channel['id'], 'name': shop,
                                  'countries': [{'id': identity} for identity in countries.values()]}])
    default = oracle.search('shipping-method', {'ids': [oracle.channel['shippingMethodId']]})[0]
    identities = [uuid4().hex for _ in formulas]
    rules = [seed_rule(oracle, expr, countries, groups, shop + f' route {i+1}') for i, expr in enumerate(formulas)]
    oracle.sync('rule', rules)
    currency = oracle.channel['currencyId']
    light, heavy = (590 + index * 65, 1170 + index * 125) if phase != 'test' else (
        645 + index * 80, 1295 + index * 145)
    tiers = (light, heavy)
    calculation, cutoff, gap = ((2, 60, 0.01), (3, 5, 0.001), (2, 90, 0.01), (3, 3, 0.001))[index] if phase == 'test' else (3, 3, 0.001)
    oracle.sync('shipping_method', [
        {'id': identity, 'name': f'{shop} method {i+1}', 'technicalName': 'freight-' + identity,
         'active': True, 'taxType': 'auto', 'deliveryTimeId': default['deliveryTimeId'],
         'availabilityRuleId': rule['id'],
         'prices': [{'id': uuid4().hex, 'calculation': calculation, 'quantityStart': start,
                     **({'quantityEnd': round(cutoff - gap, 3)} if start == 0 else {}),
                     'currencyPrice': [{'currencyId': currency, 'gross': amount / 100,
                                        'net': amount / 100, 'linked': False}]}
                    for start, amount in zip((0, cutoff), tiers)]}
        for i, (identity, rule) in enumerate(zip(identities, rules, strict=True))])
    oracle.sync('sales_channel', [{'id': oracle.channel['id'],
                                  'shippingMethods': [{'id': identity} for identity in identities]}])
    tax = uuid4().hex
    oracle.sync('tax', [{'id': tax, 'name': shop + ' test tax', 'taxRate': 19}])
    catalog = {pair: uuid4().hex for pair in sorted({case[2:] for case in contexts})}
    oracle.sync('product', [
        {'id': identity, 'productNumber': 'FREIGHT-' + identity, 'name': f'{shop} article',
         'active': True, 'stock': 40, 'taxId': tax, 'weight': grams / 1000,
         'price': [{'currencyId': currency, 'gross': amount / 100, 'net': amount / 119, 'linked': False}],
         'visibilities': [{'salesChannelId': oracle.channel['id'], 'visibility': 30}]}
        for (amount, grams), identity in catalog.items()])

    for country, group in sorted({case[:2] for case in contexts}):
        address = {'firstName': 'Sample', 'lastName': 'Freight', 'street': 'Testweg 3',
                   'zipcode': '1010' if country == 'AT' else '48624', 'city': 'Teststadt',
                   'countryId': countries[country]}
        mail = 'freight-' + uuid4().hex + '@example.test'
        password = uuid4().hex + '!9aA'
        account = oracle.store({}, '/account/register', {
            'email': mail, 'firstName': address['firstName'], 'lastName': address['lastName'],
            'password': password, 'guest': False, 'salutationId': oracle.salutation,
            'storefrontUrl': api.oracle_module.BASE, 'billingAddress': address})
        oracle.admin('/customer/' + account['id'], {'groupId': groups[group]}, 'PATCH')
        placed = False
        for _, _, amount, grams in (case for case in contexts if case[:2] == (country, group)):
            session = {}
            oracle.store(session, '/account/login', {'username': mail, 'password': password})
            oracle.store(session, '/context', {'shippingMethodId': default['id']}, 'PATCH')
            oracle.store(session, '/checkout/cart', method='DELETE')
            identity = catalog[(amount, grams)]
            cart = oracle.store(session, '/checkout/cart/line-item', {'items': [
                {'id': identity, 'referencedId': identity, 'type': 'product', 'quantity': 1, 'stackable': True, 'removable': True}]})
            if api.cents(cart['price']['positionPrice']) != amount or len(cart['lineItems']) != 1:
                raise ValueError((country, group, amount, grams, api.cart_snapshot(cart)))
            if float(cart['lineItems'][0]['deliveryInformation']['weight']) != grams / 1000:
                raise ValueError('Wrong native basket weight')
            if cart['deliveries'][0]['location']['country']['iso'] != country:
                raise ValueError('Wrong native delivery country')
            available = oracle.store(session, '/shipping-method', {'onlyAvailable': True})
            active = {element['id'] for element in available['elements']}
            if default['id'] not in active:
                raise ValueError('Missing default shipping method')
            choices = [key for key in identities if key in active]
            quote = None
            if choices:
                method = choices[(amount + grams + index) % len(choices)]
                oracle.store(session, '/context', {'shippingMethodId': method}, 'PATCH')
                cart = oracle.store(session, '/checkout/cart')
                delivery = cart['deliveries'][0]
                if delivery['shippingMethod']['id'] != method:
                    raise ValueError('Native checkout did not use the chosen method')
                quote = {'method': method, 'shipping': api.cents(delivery['shippingCosts']['totalPrice']),
                         'total': api.cents(cart['price']['totalPrice'])}
            observed_order = None
            if phase == 'test' and not placed and quote is not None and (country, group, amount, grams) in HOLDOUT_CONTEXTS:
                order_id = oracle.store(session, '/checkout/order', {})['id']
                order = oracle.search('order', {'ids': [order_id], 'associations': {'deliveries': {}}})[0]
                observed_order = {'method': order['deliveries'][0]['shippingMethodId'],
                                  'shipping': api.cents(order['shippingTotal']), 'total': api.cents(order['amountTotal'])}
                if observed_order != quote:
                    raise ValueError('Native order and checkout quote disagree')
                placed = True
            yield {'shop': shop, 'channel': oracle.channel['id'], 'case': [country, group, amount, grams],
                   'methods': identities, 'available': choices, 'quote': quote,
                   **({'order': observed_order} if observed_order is not None else {})}


@contextmanager
def cent_rounding(oracle):
    """Temporarily fix the rounding profile of the disposable teacher."""
    currency = oracle.channel['currencyId']
    profile = {key: {'decimals': 2, 'interval': 0.01, 'roundForNet': True}
               for key in ('itemRounding', 'totalRounding')}
    saved = [('currency', oracle.search('currency', {'ids': [currency]})[0])]
    saved += [('currency-country-rounding', row) for row in oracle.search('currency-country-rounding', {
        'filter': [{'type': 'equals', 'field': 'currencyId', 'value': currency}], 'limit': 500})]
    changed = []
    try:
        for entity, row in saved:
            oracle.admin('/' + entity + '/' + row['id'], profile, 'PATCH')
            changed.append((entity, row))
        yield
    finally:
        for entity, row in reversed(changed):
            oracle.admin('/' + entity + '/' + row['id'], {key: {field: row[key][field] for field in profile[key]} for key in profile}, 'PATCH')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('pilot', 'train', 'test'))
    parser.add_argument('output', type=Path)
    parser.add_argument('--holdout', type=Path)
    args = parser.parse_args()
    if args.phase == 'pilot':
        tasks = [(0, 'pilot', (TRAIN[0], TRAIN[9], XOR), tuple(product(COUNTRIES, GROUPS, (5999, 9000), (2999, 5000))))]
    elif args.phase == 'train':
        tasks = [(i, 'train', TRAIN[i*6:(i+1)*6], CONTEXTS) for i in range(4)]
    else:
        tasks = [(i, 'test', (*TEST[i*4:(i+1)*4], CHALLENGES[i]), CONTEXTS + HOLDOUT_CONTEXTS) for i in range(4)]
    if (args.phase == 'test') != bool(args.holdout):
        parser.error('The test phase requires a separate --holdout path')
    def capture(tasks, output, holdout=None):
        for batch in pool.map(lambda task: list(merchant(*task)), tasks):
            independent, support = [], []
            for row in batch:
                target = independent if holdout and tuple(row['case']) in HOLDOUT_CONTEXTS else support
                target.append(row)
            output.writelines(json.dumps(row, separators=(',', ':')) + '\n' for row in support)
            output.flush()
            if holdout:
                holdout.writelines(json.dumps(row, separators=(',', ':')) + '\n' for row in independent)
                holdout.flush()
            print(batch[0]['shop'], len(batch), 'native contexts', flush=True)

    with cent_rounding(api.Oracle(575)), ThreadPoolExecutor(max_workers=4) as pool, args.output.open('x') as output:
        if args.holdout:
            with args.holdout.open('x') as holdout:
                capture(tasks, output, holdout)
        else:
            capture(tasks, output)


if __name__ == '__main__':
    main()
