"""Induce bounded record programs from observed HTTP bodies and state deltas."""
from argparse import ArgumentParser
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re


def read(path):
    return json.loads(path.read_text())


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest()


def route_pattern(template):
    parts = re.split(r'(\{[A-Za-z][A-Za-z0-9_]*\})', template)
    return re.compile(''.join(f'(?P<{part[1:-1]}>[^/]+)' if part.startswith('{') else re.escape(part)
                              for part in parts))


def leaves(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, (*path, key))
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, (*path, index))


def same(left, right):
    return type(left) is type(right) and left == right


def request_context(body, identity, paths):
    values = dict(leaves(body))
    return {'record_id': identity, 'operands': [values[tuple(path)] for path in paths]}


def induce(examples, targets):
    """Fit structural templates whose varying leaves bind to live input slots."""
    sources = [dict(leaves(example)) for example in examples]
    first = targets[0]
    if all(same(first, target) for target in targets):
        return ['literal', first]
    candidates = sorted(set.intersection(*(set(row) for row in sources)),
                        key=lambda key: (len(key), tuple(map(str, key))))
    for key in candidates:
        if key and all(same(row[key], target) for row, target in zip(sources, targets, strict=True)):
            return ['ref', *key]
    if isinstance(first, dict) and all(isinstance(item, dict) and item.keys() == first.keys() for item in targets):
        return ['record', {key: induce(examples, [item[key] for item in targets]) for key in first}]
    if isinstance(first, list) and all(isinstance(item, list) and len(item) == len(first) for item in targets):
        return ['vector', *(induce(examples, list(values)) for values in zip(*targets, strict=True))]
    raise ValueError('No stable observed expression; collect an intervention or extend the primitives')


def fit(inventory, episodes):
    if not episodes or any(row['split'] != 'train' for row in episodes):
        raise ValueError('Fit accepts a nonempty training split only')
    operations = [(item, route_pattern(item['path'])) for item in inventory['operations']]
    groups = defaultdict(list)
    for episode in episodes:
        for step in episode['requests']:
            matches = [(item, match.groupdict()) for item, pattern in operations
                       if item['method'] == step['method'] and (match := pattern.fullmatch(step['path']))]
            if not matches:
                raise ValueError(f'Operation is missing from installed OpenAPI: {step["path"]}')
            item, params = min(matches, key=lambda pair: len(pair[1]))
            env = {'record': step['before'], 'params': params, 'body': step['body']}
            groups[(item['method'], item['path'])].append((item, env, step))
    programs = []
    for observations in groups.values():
        items, envs, steps = zip(*observations, strict=True)
        request = [{**env, 'record': None} for env in envs]
        id_program = induce(request, [step['before']['id'] for step in steps])
        before = [step['before'] for step in steps]
        after = [step['after'] for step in steps]
        changed = any(a != b for a, b in zip(before, after, strict=True))
        # A request can vary only at positions observed to read the record id
        # or to feed a learned write. All other request fields stay in scope.
        bodies = [dict(leaves(step['body'])) for step in steps]
        common = set.intersection(*(set(body) for body in bodies))
        slots = sorted((path for path in common if type(bodies[0][path]) in (int, str, bool)
                        and any(not same(bodies[0][path], body[path]) for body in bodies[1:])), key=str)
        inputs = [request_context(step['body'], step['before']['id'], slots) for step in steps]
        programs.append({**items[0], 'record_id': id_program,
                         'body_slots': slots,
                         'body_contract': induce(inputs, [step['body'] for step in steps]),
                         'writes': induce(envs, after) if changed else None,
                         'response': induce(envs, [step['response'] for step in steps]),
                         'status': induce(envs, [step['status'] for step in steps]), 'examples': len(steps)})
    return programs


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('inventory', type=Path)
    cli.add_argument('episodes', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--ui', type=Path, required=True)
    args = cli.parse_args()
    inventory, episodes, ui = read(args.inventory), read(args.episodes), read(args.ui)
    operations = fit(inventory, episodes)
    from project import fit_views

    model = {'training_channels': sorted({row['channel'] for row in episodes}),
             'training_sha256': fingerprint(args.episodes), 'ui_sha256': fingerprint(args.ui),
             'operations': operations, 'views': fit_views(ui, operations)}
    args.output.write_text(json.dumps(model, ensure_ascii=False, indent=2) + '\n')
    print(f'{len(operations)} acquired operation profiles, {len(model["views"])} view profiles')


if __name__ == '__main__':
    main()
