"""Compile a recorded Shopware mask into component trees with live record slots."""
from argparse import ArgumentParser
from collections import Counter
import gzip
from hashlib import sha256
import json
from pathlib import Path


def trace_bytes(path):
    content = path.read_bytes()
    return gzip.decompress(content) if path.suffix == '.gz' else content


def field_key(tag, attrs):
    return (attrs.get('label') or attrs.get('id')) if tag in ('input', 'textarea', 'select') else None


def fields(node):
    if isinstance(node, str):
        return
    tag, _, attrs, children = node
    if (key := field_key(tag, attrs)) and 'value' in attrs:
        yield key, attrs['value']
    for child in children:
        yield from fields(child)


def infer_slots(rows, tab):
    observations = [dict(fields(next(item['root'] for item in row['tabs'] if item['key'] == tab))) for row in rows]
    result = {}
    for key in sorted(set.intersection(*(set(fields) for fields in observations))):
        values = [str(field[key]) for field in observations]
        if len(set(values)) < 2:
            continue
        candidates = [name for name in ('name', 'productNumber', 'stock')
                      if values == [str(row['record'][name]) for row in rows]]
        if len(candidates) == 1:
            result[key] = candidates[0]
    return result


def bind(node, record, slots):
    if isinstance(node, str):
        return node.replace(record['name'], '{{name}}').replace(record['productNumber'], '{{productNumber}}')
    tag, token, old_attrs, children = node
    attrs = {key: value.replace(record['id'], '{{id}}').replace(record['name'], '{{name}}')
             if isinstance(value, str) else value
             for key, value in old_attrs.items()}
    if key := slots.get(field_key(tag, old_attrs)):
        attrs['value'] = '{{' + key + '}}'
        if key == 'stock':
            attrs['data-stock'] = 'read' if attrs.get('disabled') else 'editable'
    return [tag, token, attrs, [bind(child, record, slots) for child in children]]


def counts(node):
    result = Counter()
    if isinstance(node, str):
        return result
    tag, _, attrs, children = node
    result['nodes'] += 1
    result['fields'] += tag in ('input', 'textarea', 'select')
    result['bound_fields'] += str(attrs.get('value', '')).startswith('{{')
    result['buttons'] += tag == 'button'
    result['live_stock'] += attrs.get('data-stock') == 'editable'
    for child in children:
        result.update(counts(child))
    return result


def product(rows):
    if len({row['record']['id'] for row in rows}) < 2:
        raise ValueError('Training needs independent product records')
    first = rows[0]
    keys = {tab['key'] for tab in first['tabs']}
    if len(keys) < 2 or any({tab['key'] for tab in row['tabs']} != keys for row in rows):
        raise ValueError('Training products disagree on their observable tabs')
    tabs = []
    for tab in first['tabs']:
        slots = infer_slots(rows, tab['key'])
        tree = bind(tab['root'], first['record'], slots)
        tabs.append({**tab, 'root': tree, 'counts': counts(tree), 'field_bindings': slots})
    if not any(tab['counts']['live_stock'] for tab in tabs):
        raise ValueError('The native stock field could not be bound')
    return {'training_ids': [row['record']['id'] for row in rows], 'tabs': tabs}


def areas(rows):
    return {'candidates': [{'name': row['name'], 'route': row['route'], 'counts': counts(row['tree']['root']),
                            'tokens': row['tree']['tokens'], 'root': row['tree']['root']}
                           for row in rows if 'tree' in row],
            'unmeasured': [row for row in rows if 'error' in row]}


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('mode', choices=('product', 'batch'))
    cli.add_argument('source', type=Path)
    cli.add_argument('output', type=Path)
    args = cli.parse_args()
    source = trace_bytes(args.source)
    rows = json.loads(source)
    result = (product if args.mode == 'product' else areas)(rows)
    result['trace_sha256'] = sha256(source).hexdigest()
    payload = (json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    args.output.write_bytes(gzip.compress(payload, mtime=0) if args.output.suffix == '.gz' else payload)
    print(json.dumps({k: [(x.get('key', x.get('name')), x.get('counts', {})) for x in v]
                      for k,v in result.items() if k in ('tabs','candidates','unmeasured')}))


if __name__ == '__main__':
    main()
