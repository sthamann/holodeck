"""Record real Admin API requests and independent readbacks on isolated Shopware."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'neural'))
from capture import BASE, Oracle

SYNC = '/api/_action/sync'


def sync(entity, row):
    return [{'key': 'write', 'action': 'upsert', 'entity': entity, 'payload': [row]}]


def execute(oracle, request, image):
    path = request['path']
    body = request['body']
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', **oracle.headers}
    if request.get('query'):
        path += '?' + urlencode(request['query'])
        headers['Content-Type'] = 'image/jpeg'
        body = image
    else:
        body = json.dumps(body).encode()
    try:
        with urlopen(Request(BASE + path, body, headers, method=request['method']), timeout=45) as response:
            return response.status
    except HTTPError as error:
        raise RuntimeError(f'{path}: {error.code}: {error.read()[:700]!r}') from error


def select(row, *fields):
    return {key: row[key] for key in fields}


def capture(oracle, split, i):
    uid = lambda: uuid4().hex
    product, group, option, setting, variant, media, relation = (uid() for _ in range(7))
    currency = oracle.channel['currencyId']
    label = ('Papierwerft' if split == 'holdout' else 'Kornufer') + f' Katalog {i + 1}'
    record = {'id': product, 'name': label, 'stock': 20 + i, 'productNumber': 'HD-' + product[:12]}
    base_price = {'currencyId': currency, 'gross': 25.81 + i, 'net': 21.68 + i, 'linked': False}
    oracle.sync('property_group', [{'id': group, 'name': 'Finish ' + group[:6], 'displayType': 'text',
                                    'sortingType': 'alphanumeric'}])
    option_name = ['Ivory', 'Linen', 'Slate', 'Indigo'][i % 4]
    oracle.sync('property_group_option', [{'id': option, 'groupId': group, 'name': option_name}])
    oracle.sync('product', [{**record, 'active': True, 'taxId': oracle.tax[19],
                             'price': [base_price], 'visibilities': [
                                 {'salesChannelId': oracle.channel['id'], 'visibility': 30}]}])
    initial = oracle.search('product', {'ids': [product]})[0]
    version = initial['versionId']
    image_path = HERE.parent / 'native' / 'public' / 'assets' / ('moka.jpg', 'moka-pair.jpg', 'press.jpg')[i % 3]
    image = image_path.read_bytes()
    filename = 'product-' + uid()[:10]
    steps = []

    def observe(name, inputs, path, body, readback, query=None):
        request = {'method': 'POST', 'path': path, 'query': query or {}, 'body': body}
        # Keep the binary fixture separately and use its digest in the request trace.
        if query:
            request['body'] = sha256(image).hexdigest()
        status = execute(oracle, request, image)
        change = readback()
        steps.append({'action': name, 'inputs': inputs, 'request': request, 'status': status,
                      'observed': change})

    def product_fields(*fields):
        return select(oracle.search('product', {'ids': [product]})[0], *fields)

    gross = round(43 + i * 7.15 + (10 if split == 'holdout' else 0), 2)
    net = round(35 + i * 5.12 + (7 if split == 'holdout' else 0), 2)
    price = {'currencyId': currency, 'gross': gross, 'net': net, 'linked': bool(i % 2)}
    price = {key: int(value) if type(value) is float and value.is_integer() else value
             for key, value in price.items()}
    observe('price', {'product': product, 'version': version, **price}, SYNC,
            sync('product', {'id': product, 'versionId': version, 'price': [price]}),
            lambda: {'collection': 'product', 'id': product, 'values': {
                'price': [select(p, *price) for p in product_fields('price')['price']]}})
    observe('configure', {'product': product, 'setting': setting, 'option': option}, SYNC,
            sync('product', {'id': product, 'configuratorSettings': [{'id': setting, 'optionId': option}]}),
            lambda: {'collection': 'configurator', 'id': setting, 'values': select(
                oracle.search('product-configurator-setting', {'ids': [setting]})[0], 'productId', 'optionId')})
    stock = 5 + i * 3
    number = record['productNumber'] + '-' + option_name.upper()
    observe('variant', {'product': product, 'variant': variant, 'option': option, 'number': number, 'stock': stock},
            SYNC, sync('product', {'id': variant, 'parentId': product, 'productNumber': number,
                                  'stock': stock, 'options': [{'id': option}]}),
            lambda: {'collection': 'variant', 'id': variant, 'values': {
                **select(oracle.search('product', {'ids': [variant]})[0], 'parentId', 'productNumber', 'stock'),
                'optionIds': oracle.search('product', {'ids': [variant]})[0]['optionIds']}})
    observe('media_create', {'media': media}, '/api/media', {'id': media},
            lambda: {'collection': 'media', 'id': oracle.search('media', {'ids': [media]})[0]['id'],
                     'values': {}})
    blob = sha256(image).hexdigest()
    observe('media_upload', {'media': media, 'name': filename, 'blob': blob, 'size': len(image)}, f'/api/_action/media/{media}/upload', None,
            lambda: {'collection': 'media', 'id': media, 'values': select(
                oracle.search('media', {'ids': [media]})[0], 'fileName', 'fileExtension', 'mimeType', 'fileSize')},
            {'extension': 'jpg', 'fileName': filename})
    observe('media_link', {'product': product, 'relation': relation, 'media': media, 'position': i}, SYNC,
            sync('product_media', {'id': relation, 'productId': product, 'mediaId': media, 'position': i}),
            lambda: {'collection': 'product_media', 'id': relation, 'values': select(
                oracle.search('product-media', {'ids': [relation]})[0], 'productId', 'mediaId', 'position')})
    observe('cover', {'product': product, 'relation': relation}, SYNC,
            sync('product', {'id': product, 'coverId': relation}),
            lambda: {'collection': 'product', 'id': product, 'values': product_fields('coverId')})
    return {'split': split, 'channel': oracle.channel['id'], 'record': record, 'version': version,
            'options': [{'id': option, 'name': option_name}], 'price': initial['price'],
            'image_sha256': blob, 'image_fixture': image_path.name, 'steps': steps}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('split', choices=('train', 'holdout'))
    parser.add_argument('output', type=Path)
    parser.add_argument('--count', type=int, default=3)
    args = parser.parse_args()
    oracle = Oracle()
    rows = [capture(oracle, args.split, index + (3 if args.split == 'holdout' else 0)) for index in range(args.count)]
    args.output.write_text(json.dumps(rows, ensure_ascii=False, separators=(',', ':')) + '\n')
    print(f'{args.split}: {len(rows)} products, {sum(len(row["steps"]) for row in rows)} native writes', flush=True)


if __name__ == '__main__':
    main()
