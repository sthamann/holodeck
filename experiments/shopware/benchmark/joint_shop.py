"""Serve the mixed shop through the joint transaction and projection checkpoint."""
from argparse import ArgumentParser
import json
from pathlib import Path
from uuid import uuid4

from adaptation import Activate, restore as restore_adapted
from admin_projection import AdminProjection, FULL
from composition import TextFeatures
from flow_learning import FIELDS
from graph_contract import Store, graph
from joint_deck import JointDeck
from learn_policy import json_lines, require_offline
from mixed_shop import App, serve
from program_deck_sparse import load
from program_lessons import canonical_order, canonical_quote
from service_intent import EXTRA


class JointShop(App):
    def __init__(self, args):
        self.store = Store(args.db)
        if getattr(args, 'forward_vm', False):
            from forward_vm import load_forward

            commerce = load_forward(args.checkpoint)
        else:
            commerce = load(args.checkpoint)
        if getattr(args, 'quantity_program', None):
            from quantity_program import with_quantity
            commerce = with_quantity(commerce, args.quantity_program)
        self.deck = JointDeck(commerce)
        self.shops = json.loads(args.catalog.read_text())
        self.projections = {}
        self.audit = args.audit
        self.showcase = args.showcase
        self.teacher_port = args.teacher_port
        if self.showcase:
            labels = ('Mokkakanne aus Stahl', 'Espressotasse mit Untertasse', 'French Press',
                      'Espresso Vorratspack', 'Dosierlöffel aus Messing')
            for shop in self.shops.values():
                shop['names'].update({item['id']: name for item, name in zip(shop['catalog'], labels)})
        self.strands = None
        self.strands_view = False
        if getattr(args, 'strands_model', None):
            from strands_readout import StrandsReadout

            self.strands = StrandsReadout(args.strands_model, args.strands_head)
            self.strands_view = args.strands_head is not None
        if not self.strands_view:
            features = TextFeatures(args.base, (), [(('admin',), '')])
            self.language = AdminProjection(features, args.language_weights, None)
        if args.intervention:
            # Suppress the learned shipped output. Checkout's initial open
            # value and all projection cells keep their original weights.
            model = self.deck.deck.predict
            field = 'write:order.delivery'
            cell = model.grammar.cells.index(field)
            shipped = model.grammar.atoms[field].index(['literal', 'shipped'])
            model.weights[(model.cells == cell) & (model.labels == shipped)] = 1
            self.deck.identity += '-delivery-output-control'
            self.deck.deck.checkpoint = self.deck.identity
        self.base_identity = self.deck.identity
        self.versions = {self.base_identity: self.deck}
        self.candidate = None
        if args.specialist:
            candidate = restore_adapted(args.checkpoint, args.specialist)
            if getattr(args, 'strands_program_head', None):
                from strands_programs import StrandsPrograms

                candidate = StrandsPrograms(self.strands, args.strands_program_head, args.specialist).attach(candidate)
            self.candidate = JointDeck(candidate)
            self.versions[self.candidate.identity] = self.candidate
            if not self.strands_view:
                self.service = AdminProjection(features, args.service_weights, None, extra=EXTRA)
            if args.active_specialist:
                self.deck = self.candidate

    def pinned(self, state):
        checkpoint = state['config'].get('joint.checkpoint')
        if checkpoint not in self.versions:
            raise ValueError('Die Modellversion dieses Vorgangs ist nicht geladen.')
        return self.versions[checkpoint]

    def activate(self, body):
        if self.candidate is None:
            raise ValueError('Keine neue Modellversion vorgemerkt.')
        session, revision = body['session'], body['revision']
        shop = self.shop_for(session)
        transition = {'kind': 'checkpoint.activate', 'root': session, 'id': body['request'], 'args': {}}
        before = self.store.read(shop['tenant'], session)
        receipt = self.store.apply(shop['tenant'], transition,
                                   Activate(self.base_identity, self.candidate.identity), revision)
        if before['config']['joint.checkpoint'] == self.base_identity:
            self.projections = {key: offer for key, offer in self.projections.items() if offer['root'] != session}
        self.log({'activation': session, 'receipt': receipt})
        return receipt

    def new_session(self, body):
        slug = body.get('shop') or next(iter(self.shops))
        shop = self.shops[slug]
        profile = {'name': shop['name'], 'shop': slug, 'demoCode': shop['code'], 'adminIntent': FULL,
                   'shops': [{'key': key, 'name': item['name']} for key, item in self.shops.items()]}
        if self.showcase:
            profile['showcase'] = True
        root = body.get('resume')
        if root:
            try:
                state = self.store.read(shop['tenant'], root)
                if state['config'].get('joint.checkpoint') in self.versions:
                    return {'session': root, **profile}
            except ValueError:
                pass
        root = uuid4().hex
        initial = graph(shop['tenant'], root, {'mixed.catalog': shop['catalog'], **dict.fromkeys(FIELDS, 0)},
                        {**shop['config'], 'joint.checkpoint': self.deck.identity})
        initial['workflow_effects'] = []
        self.store.seed(initial)
        return {'session': root, **profile}

    def view(self, session, scope, nodes, selected=None):
        response = super().view(session, scope, nodes, selected)
        # The core stores typed records with explicit identity. The existing
        # renderer reads the normalized public transport shape.
        data = response['data']
        if data.get('cart'):
            data['cart'] = canonical_quote(data['cart'])
        if data.get('receipt'):
            data['receipt'] = canonical_order(data['receipt'])
        if data.get('selection'):
            item = canonical_order(data['selection'])
            data['selection'] = {**item, 'items': item['lines']}
        return response

    def project(self, body):
        request = body['intent']
        if not isinstance(request, str) or not 0 < len(request) <= 450:
            raise ValueError('Bitte eine kurze Anfrage eingeben.')
        if self.strands_view:
            facets = self.strands.choose(request, include_service=self.candidate is not None)
        else:
            facets = self.language.choose(request)
        if body['scope'] != 'admin':
            facets &= {'order_taxes'}
        elif self.candidate is not None:
            service = (facets if self.strands_view else self.service.choose(request)) & EXTRA.keys()
            if service:
                facets.update({'details', *service})
        return self.render_offer(body['session'], body['scope'], body.get('selected'), facets, request)

    def render_offer(self, session, scope, selected, facets, request):
        if selected and scope != 'admin':
            raise ValueError('Diese Ansicht bindet keine Bestellauswahl.')
        tenant = self.shop_for(session)['tenant']
        state = self.store.read(tenant, selected or session)
        deck = self.pinned(state)
        nodes, offers = deck.view(state, scope, facets, bool(selected))
        result = self.view(session, scope, nodes, selected)
        if self.showcase:
            require_offline(self.teacher_port)
            active = self.candidate is not None and self.candidate.identity == deck.identity
            result['provenance'] = {'checkpoint': deck.identity, 'root': state['root'],
                                    'serviceActive': active, 'teacherOffline': True,
                                    'activationAvailable': bool(selected and self.candidate and not active)}
        projection = uuid4().hex
        self.projections[session, scope] = {'id': projection, 'nodes': nodes, 'offers': offers,
                                           'selected': selected, 'facets': facets, 'request': request,
                                           'checkpoint': deck.identity, 'root': state['root']}
        self.log({'projection': projection, 'scope': scope, 'intent': request, 'nodes': nodes,
                  'checkpoint': deck.identity, 'root': state['root'], 'offers': offers,
                  'readout': 'strands' if self.strands_view else 'joint',
                  'facets': sorted(facets)})
        return {**result, 'projection': projection}

    def action(self, body):
        session, scope, command = (body[key] for key in ('session', 'scope', 'action'))
        current = self.projections.get((session, scope))
        if not current or current['id'] != body['projection']:
            raise ValueError('Ansicht abgelaufen. Bitte neu erzeugen.')
        offer = current['offers'].get(command)
        if offer is None:
            raise ValueError('Aktion ist in dieser Ansicht nicht verfügbar.')
        shop = self.shop_for(session)
        action = next(item for node in current['nodes'] for item in node['actions'] if item['id'] == command)
        if body['revision'] != offer['revision']:
            raise ValueError('Der Datensatz wurde geändert. Bitte neu laden.')
        args = {}
        binding = action['binding']
        if binding == 'row.id' and body.get('row') != offer['root']:
            raise ValueError('Die Aktion gehört zu einer anderen Bestellung.')
        if binding == 'quantities':
            values = body['quantities']
            if not isinstance(values, dict) or not values or any(
                    key not in shop['names'] or type(value) is not int or not 1 <= value <= 999
                    for key, value in values.items()):
                raise ValueError('Bitte gültige Artikel und Mengen wählen.')
            args['items'] = [{'id': key, 'quantity': value} for key, value in values.items()]
        elif binding == 'code' and body.get('code', '').strip().upper() != shop['code']:
            raise ValueError('Dieser Gutscheincode ist ungültig.')
        event = {'kind': action['event'], 'root': offer['root'], 'id': body['request'],
                 'args': args, 'offer': offer['program']}
        receipt = self.store.apply(shop['tenant'], event, self.versions[current['checkpoint']], offer['revision'])
        self.projections.pop((session, scope), None)
        self.log({'action': action, 'root': event['root'], 'offer': offer, 'receipt': receipt})
        try:
            refreshed = self.render_offer(session, scope, current['selected'], current['facets'], current['request'])
        except (ValueError, TypeError, KeyError, ArithmeticError):
            safe_nodes = [{**node, 'actions': []} for node in current['nodes']]
            refreshed = {**self.view(session, scope, safe_nodes, current['selected']), 'projection': None}
        return {**refreshed, 'receipt': receipt}


def import_catalog(source, target, cases):
    rows = json_lines(source)
    shops = {}
    for pair in cases:
        label, raw_case = pair.split(':', 1)
        row = next(row for row in rows if row['shop'] == label and row['case'] == int(raw_case))
        shops[label.lower()] = {'name': label, 'tenant': row['channel'], 'config': row['config'],
                                'catalog': row['catalog'], 'code': label[:4].upper() + str(raw_case),
                                'names': {item['id']: f'Artikel {i:02d}' for i, item in enumerate(row['catalog'], 1)}}
    target.write_text(json.dumps(shops, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('import', 'serve'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--case', action='append')
    parser.add_argument('--base', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--language-weights', type=Path)
    parser.add_argument('--strands-model', help='Strands checkpoint path or Hugging Face model ID')
    parser.add_argument('--strands-head', type=Path, help='Locally fitted Strands pointer weights')
    parser.add_argument('--strands-program-head', type=Path, help='Fitted after-sales program selection weights')
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--db', type=Path, default=Path(':memory:'))
    parser.add_argument('--audit', type=Path)
    parser.add_argument('--port', type=int, default=55720)
    parser.add_argument('--teacher-port', type=int, default=55719)
    parser.add_argument('--intervention', action='store_true')
    parser.add_argument('--specialist', type=Path)
    parser.add_argument('--service-weights', type=Path)
    parser.add_argument('--active-specialist', action='store_true')
    parser.add_argument('--quantity-program', type=Path)
    parser.add_argument('--showcase', action='store_true')
    parser.add_argument('--forward-vm', action='store_true', help='Run compiled expert graphs in model forward passes')
    args = parser.parse_args()
    require_offline(args.teacher_port)
    if args.mode == 'import':
        if not args.source or not args.case:
            parser.error('import requires --source and one or more --case Shop:case arguments')
        import_catalog(args.source, args.catalog, args.case)
    else:
        if not args.checkpoint or not (args.strands_head or (args.base and args.language_weights)):
            parser.error('serve requires --checkpoint and either --strands-head or --base with --language-weights')
        if args.strands_head and not args.strands_model:
            parser.error('--strands-head requires --strands-model')
        if args.strands_program_head and not (args.strands_model and args.specialist):
            parser.error('--strands-program-head requires --strands-model and --specialist')
        if args.strands_model and not (args.strands_head or args.strands_program_head):
            parser.error('--strands-model requires at least one fitted head')
        if ((not args.strands_head and bool(args.specialist) != bool(args.service_weights))
                or (args.active_specialist and not args.specialist) or (args.intervention and args.specialist)):
            parser.error('provide matching specialist and language weights without an intervention')
        if args.quantity_program and (args.specialist or args.intervention):
            parser.error('the quantity experiment uses the standalone joint checkpoint')
        if args.forward_vm and (args.specialist or args.quantity_program or args.intervention):
            parser.error('the forward-pass experiment uses one frozen joint checkpoint')
        serve(JointShop(args), args.port)
