"""Install a candidate only after independent native HTTP and browser checks."""
from argparse import ArgumentParser
import json
from pathlib import Path
from shutil import copyfile

from learn import fingerprint, read


def main():
    parser = ArgumentParser(description=__doc__)
    for name in ('inventory', 'candidate', 'api_report', 'native_ui', 'deck_ui', 'output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    api, native, deck = read(args.api_report), read(args.native_ui), read(args.deck_ui)
    if (api['model_sha256'] != fingerprint(args.candidate) or not api['requests'] or
            api['exact'] != api['requests'] or api['uninstalled_route_status'] != 501):
        raise ValueError('Candidate does not have an exact HTTP replay')
    if not native or native != deck:
        raise ValueError('Candidate does not match native browser bindings and persisted effects')
    inventory = read(args.inventory)
    passed = {(item['method'], item['path']): item['total'] for item in api['profiles']
              if item['total'] and item['exact'] == item['total']}
    if len(passed) != len(read(args.candidate)['operations']):
        raise ValueError('Installed operation profiles require independent observations')
    coverage = [{**op, 'state': 'scoped-pass' if (op['method'], op['path']) in passed else 'unmeasured',
                 'cases': passed.get((op['method'], op['path']), 0)} for op in inventory['operations']]
    args.output.mkdir(parents=True, exist_ok=False)
    copyfile(args.candidate, args.output / 'model.json')
    summary = {'candidate_sha256': api['model_sha256'], 'api_sha256': fingerprint(args.api_report),
               'native_ui_sha256': fingerprint(args.native_ui), 'deck_ui_sha256': fingerprint(args.deck_ui),
               'inventory_operations': len(coverage), 'scoped_pass': len(passed),
               'unmeasured': len(coverage) - len(passed), 'exact_http': api['exact'],
               'browser_observations': len(native), 'browser_writes': sum('action' in row for row in native)}
    (args.output / 'manifest.json').write_text(json.dumps(summary, indent=2) + '\n')
    (args.output / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
