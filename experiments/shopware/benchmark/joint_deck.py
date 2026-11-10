"""Compose views and transaction offers from one learned cell checkpoint."""
from hashlib import sha256

from collection_vm import run
from graph_contract import encode, root_of
from program_deck import signature


# A finalized document and its source draft are append-only. Mutable
# lifecycle state stays in separate fields.
SEALED_FIELDS = {'mixed.order': {'mixed.order', 'mixed.items', 'mixed.quote'}}


def program_hash(program):
    return sha256(encode(program).encode()).hexdigest()


class JointDeck:
    def __init__(self, deck):
        self.deck = deck
        self.identity = deck.checkpoint
        self.events = {tag[6:] for tag in deck.predict.tags if tag.startswith('event=')}

    def plan(self, state, event, tenant, *, input_witness=False):
        if state['config'].get('joint.checkpoint') != self.identity:
            raise ValueError('Dieser Vorgang gehört zu einer anderen Modellversion.')
        if event['kind'] not in self.events or event['kind'].startswith('project.'):
            raise ValueError('Dieses Ereignis wird vom Deck nicht unterstützt.')
        result = self.deck.forward(state, event, tenant)
        before = root_of(state)['fields']
        writes = result['writes']
        if any(anchor in before and fields & writes.keys() for anchor, fields in SEALED_FIELDS.items()):
            raise ValueError('Der Vorgang würde einen abgeschlossenen Beleg überschreiben.')
        changed = any(before.get(key) != value for key, value in writes.items() if key != 'result.accepted')
        if writes.get('result.accepted') is False or (not changed and not input_witness):
            raise ValueError('Für den aktuellen Zustand ergibt die Aktion keine Änderung.')
        return result

    def forward(self, state, event, tenant):
        result = self.plan(state, event, tenant)
        if program_hash(result['program']) != event.get('offer'):
            raise ValueError('Das Aktionsprogramm hat sich geändert. Bitte die Ansicht neu laden.')
        return result

    def view(self, state, scope, facets, selected):
        event = {'kind': f'project.{scope}', 'args': dict.fromkeys(facets, True)}
        context = signature(state, event)
        cells = self.deck.predict(context)
        if not cells or any(not name.startswith('view:') for name, _ in cells):
            raise ValueError('Das Deck hat keine reine Leseprojektion erzeugt.')
        data = run(cells, {})
        nodes = {}
        for cell, item in sorted(data.items(), key=lambda pair: pair[1]['rank']):
            if cell.startswith('view:node:'):
                nodes[item['id']] = {'id': item['id'], 'source': item['source'],
                                     'component': item['component'], 'actions': []}
                if item['component'] == 'grid':
                    nodes[item['id']].update(columns=[], controls=[], filters=[])
        offers = {}
        for cell, item in sorted(data.items(), key=lambda pair: pair[1]['rank']):
            if cell.startswith('view:node:') or item['node'] not in nodes:
                continue
            node = nodes[item['node']]
            if item['slot'] != 'actions':
                node[item['slot']].append(item['value'])
                continue
            action = item['value']
            if action['binding'] == 'row.id' and not selected:
                continue
            args = {}
            if action['binding'] == 'quantities':
                # A typed witness checks the program shape. The actual quantities
                # are supplied by the browser and validated at commit.
                catalog = root_of(state)['fields'].get('mixed.catalog', [])
                witness = next((item for item in catalog if item['stock'] > 0), None)
                if witness is None:
                    continue
                args = {'items': [{'id': witness['id'], 'quantity': 1}]}
            probe = {'kind': action['event'], 'id': 'projection-probe', 'args': args, 'root': state['root']}
            try:
                result = self.plan(state, probe, state['tenant'],
                                   input_witness=action['binding'] == 'quantities')
            except (ValueError, TypeError, KeyError, ArithmeticError):
                continue
            proof = program_hash(result['program'])
            node['actions'].append(action)
            offers[action['id']] = {'program': proof, 'root': state['root'], 'revision': state['revision']}
        return list(nodes.values()), offers
