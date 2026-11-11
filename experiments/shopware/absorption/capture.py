"""Capture Shopware's versioned API inventory and a correlated catalog lesson."""
from argparse import ArgumentParser
import json
from pathlib import Path
from random import Random
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'neural'))
from capture import BASE, Oracle

METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options'}
FIELDS = ['id', 'name', 'stock', 'productNumber']
ADMIN = {'includes': {'product': FIELDS}}
STORE = {'includes': {'product_detail': ['product', 'apiAlias'], 'product': FIELDS}}


def request(path, headers, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(BASE + path, data=data, method=method,
                  headers={'Accept': 'application/json', 'Content-Type': 'application/json', **headers})
    try:
        with urlopen(req, timeout=40) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as error:
        with error:
            raw = error.read()
            raise RuntimeError(f'{method} {path} returned {error.code}: {raw[:240]!r}') from error


def inventory(oracle):
    result = {'operations': [], 'entities': sorted(oracle.admin('/_info/entity-schema.json'))}
    for scope, schema in (('admin', oracle.admin('/_info/openapi3.json')),
                          ('store', oracle.store({}, '/_info/openapi3.json'))):
        for path, methods in sorted(schema['paths'].items()):
            prefix = '/api' if scope == 'admin' else '/store-api'
            route = path if path.startswith(prefix + '/') else prefix + path
            for method, definition in sorted(methods.items()):
                if method in METHODS:
                    result['operations'].append({'scope': scope, 'method': method.upper(), 'path': route,
                                                 'operation': definition.get('operationId', '')})
    result['transitions'] = sorted((row['stateMachineId'], row['fromStateId'], row['actionName'], row['toStateId'])
                                   for row in oracle.search('state-machine-transition', {'limit': 500}))
    return result


def fixture(oracle, rng, native_channel, split, index):
    identity = uuid4().hex
    label = 'Kornufer' if split == 'train' else 'Papierwerft'
    record = {'id': identity, 'name': f'{label} Artikel {index + 1}',
              'stock': rng.randrange(3, 17), 'productNumber': 'HD-' + identity[:12]}
    oracle.sync('product', [{**record, 'active': True, 'taxId': oracle.tax[19],
                             'price': [{'currencyId': oracle.channel['currencyId'], 'gross': 23.40,
                                        'net': 19.66, 'linked': False}],
                             'visibilities': [{'salesChannelId': key, 'visibility': 30}
                                              for key in {oracle.channel['id'], native_channel}]}])
    return record


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('split', choices=('train', 'holdout'))
    parser.add_argument('output', type=Path)
    parser.add_argument('--count', type=int, default=8)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    oracle = Oracle()
    channel = oracle.search('sales-channel', {'filter': [
        {'type': 'equals', 'field': 'domains.url', 'value': BASE},
    ], 'limit': 1})
    if not channel:
        channel = oracle.search('sales-channel', {'filter': [
            {'type': 'equals', 'field': 'typeId', 'value': oracle.channel['typeId']},
        ], 'associations': {'domains': {}}, 'limit': 100})
        channel = [row for row in channel if row.get('domains')]
        if channel:
            domain = channel[0]['domains'][0]
            keys = ('salesChannelId', 'languageId', 'currencyId', 'snippetSetId')
            oracle.sync('sales_channel_domain', [{**{key: domain[key] for key in keys},
                                                  'url': BASE, 'id': uuid4().hex}])
    if not channel:
        raise RuntimeError('The fixture teacher needs a native storefront channel')
    native = channel[0]['id']
    if args.split == 'train':
        (args.output / 'inventory.json').write_text(json.dumps(inventory(oracle), indent=2) + '\n')
    rng = Random(610 if args.split == 'train' else 785)
    rows = []
    for index in range(args.count):
        before = fixture(oracle, rng, native, args.split, index)
        identity = before['id']
        headers = {'sw-access-key': oracle.channel['accessKey']}
        admin = {**ADMIN, 'ids': [identity]}
        change = {'stock': rng.randrange(20, 55)}
        responses = []

        def observe(method, path, request_body, credentials, state, observed):
            status, body = request(path, credentials, request_body, method)
            responses.append({'method': method, 'path': path, 'body': request_body,
                              'status': status, 'response': body, 'before': state, 'after': observed})

        observe('POST', '/api/search/product', admin, oracle.headers, before, before)
        observe('POST', '/store-api/product/' + identity, STORE, headers, before, before)
        observe('PATCH', '/api/product/' + identity, change, oracle.headers, before, None)
        native_record = oracle.search('product', {'ids': [identity], **ADMIN})[0]
        after = {key: native_record[key] for key in FIELDS}
        responses[-1]['after'] = after
        observe('POST', '/api/search/product', admin, oracle.headers, after, after)
        observe('POST', '/store-api/product/' + identity, STORE, headers, after, after)
        if after != {**before, **change}:
            raise RuntimeError('Native persisted state disagrees with the recorded patch')
        version_record = oracle.search('product', {'ids': [identity], 'includes': {'product': ['id', 'versionId']}})[0]
        stock = rng.randrange(56, 64)
        sync = [{'key': 'write', 'action': 'upsert', 'entity': 'product', 'payload': [
            {'id': identity, 'versionId': version_record['versionId'], 'stock': stock},
        ]}]
        observe('POST', '/api/_action/sync', sync, oracle.headers, after, None)
        saved = oracle.search('product', {'ids': [identity], **ADMIN})[0]
        synced = {key: saved[key] for key in FIELDS}
        responses[-1]['after'] = synced
        if synced != {**after, 'stock': stock}:
            raise RuntimeError('Native persisted state disagrees with the recorded sync')
        observe('POST', '/api/search/product', admin, oracle.headers, synced, synced)
        observe('POST', '/store-api/product/' + identity, STORE, headers, synced, synced)
        rows.append({'split': args.split, 'channel': oracle.channel['id'], 'record': before,
                     'requests': responses})
    (args.output / 'episodes.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    (args.output / 'browser.json').write_text(json.dumps({'record': rows[0]['record'],
                                                          'stock': 64 if args.split == 'train' else 72}) + '\n')
    print(f'{args.split}: {len(rows)} Shopware episodes')


if __name__ == '__main__':
    main()
