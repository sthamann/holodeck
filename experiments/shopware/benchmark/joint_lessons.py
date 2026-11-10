"""Fit one sparse checkpoint on commerce cells and annotated Shopware views."""
from argparse import ArgumentParser
from itertools import combinations
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from admin_projection import FACETS, FILTERS, examples
from graph_contract import encode
from learn_policy import fingerprint, require_offline
from mixed_projection import ATOMS, COLUMNS, COMMANDS, CONTROLS, Projector, SCOPES, lessons
from program_deck import Grammar
from program_deck_sparse import fit


def labels(view):
    result = {}
    orders = {'columns': COLUMNS, 'controls': (*CONTROLS, 'search'),
              'filters': ('payment', 'delivery'), 'actions': tuple(COMMANDS)}
    for node in view:
        rank = tuple(ATOMS).index(node['id'])
        core = {key: node[key] for key in ('id', 'source', 'component')}
        result[f'view:node:{node["id"]}'] = ['literal', {**core, 'rank': rank}]
        for slot in ('columns', 'controls', 'filters', 'actions'):
            for value in node.get(slot, []):
                key = value['id'] if slot == 'actions' else value
                pos = orders[slot].index(key)
                result[f'view:{slot}:{key}'] = ['literal', {
                    'node': node['id'], 'slot': slot, 'value': value, 'rank': pos}]
    return result


def projection_rows():
    # The existing annotation readers verify that the supported elements and
    # actions are grounded in native Storefront/Admin captures.
    list(lessons())
    observations = Path(__file__).resolve().parent.parent / 'workspace/ui.jsonl'
    witnessed = {frozenset(items) for items in examples(observations).values()}
    wanted = witnessed | {frozenset()} | {frozenset((name,)) for name in FACETS}
    wanted |= {frozenset(pair) for pair in combinations(FACETS, 2)}
    wanted.add(frozenset(FACETS))
    for facets in sorted(wanted, key=lambda items: (len(items), sorted(items))):
        selected = {'orders', 'number', *facets}
        if facets & {'pay', 'reopen', 'ship'}:
            selected.add('details')
        if facets & {'details', 'pay', 'reopen', 'ship', 'workflow', 'order_taxes'}:
            selected.add('select')
        nodes = Projector.compose(selected, {value for key, value in FILTERS.items() if key in facets})
        yield 'admin', facets, nodes
    for scope in ('storefront', 'checkout', 'receipt'):
        for requested in ((), ('order_taxes',)):
            selected = set(SCOPES[scope])
            if requested and scope != 'receipt':
                selected.add('taxes')
            yield scope, requested, Projector.compose(selected)


def projection_samples(commerce):
    # Cross UI labels with a neutral config and a representative input schema
    # from the teaching population. No reference values or held-out IDs enter.
    contexts = []
    for (tags, _), cells in commerce:
        context = {tag for tag in tags if tag.startswith(('state.', 'config.'))}
        contexts.append(context)
    core = set.intersection(*contexts)
    scope_rows = []
    for scope, facets, nodes in projection_rows():
        tags = {*core, f'event=project.{scope}', *(f'event.{key}' for key in facets),
                *(f'event.{key}=True' for key in facets)}
        scope_rows.append(((tuple(sorted(tags)), ''), labels(nodes)))
    return scope_rows


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('commerce', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--teacher-port', type=int, default=55719)
    args = parser.parse_args()
    require_offline(args.teacher_port)
    source = args.commerce / 'retrieval.json'
    commerce = json.loads(source.read_text())
    scope_rows = projection_samples(commerce)
    samples = commerce + scope_rows
    grammar = Grammar.fit(samples)
    with TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'grammar.json').write_text(json.dumps(grammar.atoms) + '\n')
        (path / 'retrieval.json').write_text(json.dumps(samples) + '\n')
        fit(path, args.output, ordered=True)
    info = {'commerce_signatures': len(commerce), 'projection_signatures': len(scope_rows),
            'source_sha256': fingerprint(source), 'total_cells': len(grammar.cells),
            'replay_archive_in_runtime': False}
    (args.output / 'training.json').write_text(encode(info) + '\n')
    print(info)


if __name__ == '__main__':
    main()
