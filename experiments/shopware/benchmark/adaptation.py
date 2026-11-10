"""Train an additive after-sales specialist and load it with the frozen joint deck."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from collection_vm import literal as L, ref as R
from graph_contract import encode, retrieve
from learn_policy import fingerprint, json_lines, require_offline
from program_deck import Grammar, ProgramDeck
from program_deck_sparse import fit, load


ACTIONS = {
    'order_delivery.retour': ('return_full', 'returns', 'Retoure vollständig erfassen'),
    'order_delivery.retour_partially': ('return_partial', 'returns', 'Teilretoure erfassen'),
    'order_transaction.refund': ('refund_full', 'refunds', 'Erstattungsstatus setzen'),
    'order_transaction.refund_partially': ('refund_partial', 'refunds', 'Teilerstattungsstatus setzen'),
}
ENTITY_FIELDS = {'order_delivery': 'delivery', 'order_transaction': 'payment'}


def target(step):
    key = ENTITY_FIELDS[step['command'].split('.')[0]]
    accepted = step['http_status'] == 200
    before, after = step['before'], step['state']
    diff = {name for name in before if before[name] != after[name]}
    if (diff - {key}) or (diff and not accepted):
        raise ValueError('Observation exceeds the declared transaction relation')
    cells = {'write:result.accepted': L(accepted)}
    if diff:
        cells['write:order.' + key] = L(after[key])
    return key, before[key], cells


def compile_examples(rows, old_events):
    routes = {}
    teaching = {}
    events = {}
    observed = 0
    for row in rows:
        for step in row['events']:
            name = step['command']
            if name not in ACTIONS:
                continue
            observed += 1
            if step['stock'] != row['order']['stock']:
                raise ValueError('Captured command changed inventory outside the trained scope')
            field, before, cells = target(step)
            tag = f'state.order.{field}={before}'
            routes.setdefault(name, set()).add(tag)
            key = tuple(sorted((f'event={name}', tag)))
            if teaching.setdefault(key, cells) != cells:
                raise ValueError('Observed effects disagree for the same input')
            child = step['event']
            if child and child not in old_events:
                if step['before_markers'] != step['markers']:
                    raise ValueError('New workflow effect needs its own supervision')
                events.setdefault(child, set()).add(step['markers']['both'])
    if routes.keys() != ACTIONS.keys():
        raise ValueError('One or more capabilities were not observed')
    for child in events:
        teaching[(f'event={child}',)] = {'write:effect.ready': R('state', 'effect.ready'),
                                         'write:effect.emitted': L(0)}
    facets = ('returns', 'refunds')
    for features in ((), ('returns',), ('refunds',), facets):
        ctx = tuple(sorted(('event=project.admin', *(f'event.{key}=True' for key in features))))
        cells = {}
        for i, (event, (name, facet, caption)) in enumerate(ACTIONS.items(), 1):
            if facet in features:
                cells['view:actions:' + name] = L({
                    'node': 'details', 'slot': 'actions', 'rank': 10 + i,
                    'value': {'id': name, 'event': event, 'binding': 'row.id', 'label': caption}})
        teaching[ctx] = cells
    return [((ctx, ''), cells) for ctx, cells in sorted(teaching.items())], {
        'routes': {name: sorted(states) for name, states in sorted(routes.items())},
        'notifications': {name: sorted(values) for name, values in sorted(events.items())},
        'observations': observed,
    }


def train(base, source, destination):
    rows = json_lines(source)
    if not rows or any(row['split'] != 'train' for row in rows) or len({row['channel'] for row in rows}) < 2:
        raise ValueError('Need two isolated training channels and no evaluation rows')
    old = load(base)
    tags = {tag[6:] for tag in old.predict.tags if tag.startswith('event=')}
    samples, info = compile_examples(rows, tags)
    grammar = Grammar.fit(samples)
    with TemporaryDirectory() as temporary:
        path = Path(temporary)
        (path / 'grammar.json').write_text(json.dumps(grammar.atoms) + '\n')
        (path / 'retrieval.json').write_text(json.dumps(samples) + '\n')
        fit(path, destination, ordered=True)
    info.update({'base': old.checkpoint, 'source': fingerprint(source),
                 'training_channels': sorted({row['channel'] for row in rows}),
                 'signatures': len(samples)})
    (destination / 'adaptation.json').write_text(json.dumps(info, indent=2) + '\n')
    print(json.dumps(info), flush=True)


class Composite:
    def __init__(self, base, specialist, metadata):
        self.base, self.specialist = base, specialist
        self.routes = {name: set(tags) for name, tags in metadata['routes'].items()}
        self.notifications = set(metadata['notifications'])
        self.tags = sorted(set(base.tags) | set(specialist.tags))

    def __call__(self, context):
        tags, _ = context
        event = next(tag[6:] for tag in tags if tag.startswith('event='))
        if event in self.routes:
            observed = self.routes[event] & set(tags)
            if len(observed) != 1:
                # An unobserved category is a refusal, never an extrapolated
                # money or delivery mutation.
                return [('write:result.accepted', L(False))]
            return self.specialist((tuple(sorted({f'event={event}', *observed})), ''))
        if event in self.notifications:
            return self.specialist(((f'event={event}',), ''))
        original = self.base(context)
        if event != 'project.admin':
            return original
        inputs = tuple(tag for tag in tags if tag in ('event=project.admin', 'event.returns=True', 'event.refunds=True'))
        return original + self.specialist((inputs, ''))


def restore(base, specialist):
    prior, addition = load(base), load(specialist)
    details = specialist / 'adaptation.json'
    metadata = json.loads(details.read_text())
    if metadata['base'] != prior.checkpoint:
        raise ValueError('Specialist requires another base checkpoint')
    combined = sha256(encode((prior.checkpoint, addition.checkpoint, fingerprint(details))).encode()).hexdigest()
    return ProgramDeck(Composite(prior.predict, addition.predict, metadata), combined)


class Activate:
    """One recorded version switch. Domain fields and continuation stay intact."""
    def __init__(self, previous, candidate):
        self.previous, self.candidate = previous, candidate

    def forward(self, state, event, tenant):
        retrieve(state, event, tenant)
        if state['config'].get('joint.checkpoint') != self.previous:
            raise ValueError('The order no longer uses the expected checkpoint')
        state['config']['joint.checkpoint'] = self.candidate
        return {'graph': state, 'checkpoint_change': {'from': self.previous, 'to': self.candidate}}


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--teacher-port', type=int, default=55724)
    args = parser.parse_args()
    require_offline(args.teacher_port)
    train(args.base, args.source, args.output)
