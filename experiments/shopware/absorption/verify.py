"""Run a frozen candidate against held-out native API traces on its normal HTTP path."""
from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path
import socket
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from learn import fingerprint, read, route_pattern


def call(port, step):
    admin = step['path'].startswith('/api/')
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    headers.update({'Authorization': 'Bearer absorption-admin'} if admin else {'sw-access-key': 'absorption-store'})
    request = Request(f'http://127.0.0.1:{port}' + step['path'], data=json.dumps(step['body']).encode(),
                      method=step['method'], headers=headers)
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.code, json.loads(raw) if raw else None


def offline(port):
    try:
        conn = socket.create_connection(('127.0.0.1', port), timeout=2)
    except ConnectionRefusedError:
        return
    conn.close()
    raise RuntimeError('Native teacher is still reachable')


def main():
    cli = ArgumentParser(description=__doc__)
    for field in ('inventory', 'model', 'episodes', 'output'):
        cli.add_argument(field, type=Path)
    cli.add_argument('--port', type=int, default=55746)
    cli.add_argument('--teacher-port', type=int, default=55745)
    args = cli.parse_args()
    offline(args.teacher_port)
    model, rows = read(args.model), read(args.episodes)
    if not rows or any(row['split'] != 'holdout' or row['channel'] in model['training_channels'] for row in rows):
        raise ValueError('Verification needs a disjoint native cohort')
    inventory = read(args.inventory)
    profiles = {(op['method'], op['path']): route_pattern(op['path']) for op in model['operations']}
    counts, exact = Counter(), Counter()
    failures = []
    for row in rows:
        for step in row['requests']:
            result = call(args.port, step)
            expected = step['status'], step['response']
            op = next(key for key, pattern in profiles.items()
                      if key[0] == step['method'] and pattern.fullmatch(step['path']))
            counts[op] += 1
            if json.dumps(result, sort_keys=True) == json.dumps(expected, sort_keys=True):
                exact[op] += 1
            else:
                failures.append({'path': step['path'], 'expected': expected, 'actual': result})
    gap = next((op['path'] for op in inventory['operations'] if op['method'] == 'POST'
                and op['path'].startswith('/api/search/') and '{' not in op['path']
                and not any(key[0] == 'POST' and pattern.fullmatch(op['path']) for key, pattern in profiles.items())),
               '/api/absorption-negative-probe')
    unsupported, _ = call(args.port, {'method': 'POST', 'path': gap, 'body': {}})
    offline(args.teacher_port)
    summary = {'model_sha256': fingerprint(args.model), 'native_episodes_sha256': fingerprint(args.episodes),
               'teacher_port_refused': args.teacher_port, 'operations_in_inventory': len(inventory['operations']),
               'entities_in_inventory': len(inventory['entities']), 'exact': sum(exact.values()),
               'requests': sum(counts.values()), 'uninstalled_route_status': unsupported,
               'profiles': [{'method': key[0], 'path': key[1], 'exact': exact[key], 'total': counts[key]}
                            for key in counts], 'failures': failures}
    args.output.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))
    if failures or unsupported != 501 or set(counts) != profiles.keys():
        raise SystemExit('The candidate did not pass the observed profiles')


if __name__ == '__main__':
    main()
