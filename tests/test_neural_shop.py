"""Closed-loop Shopware replay and HTTP acceptance for the neural shop."""
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4

from holodeck.neural_shop import AMOUNTS, NeuralDeck
from holodeck.projection import materialize
from holodeck.runtime import Ledger, Refusal, digest
from holodeck.server import HERE, Handler, test_payment
from test_standalone_shop import Client


BUYER = {'role':'buyer','id':'test-buyer'}
ADMIN = {'role':'admin','id':'test-admin'}
CONTACT = {'name':'Alex Test','email':'alex@example.test','address':'Testweg 7','consent':True}
TRACE = HERE.parents[1] / 'experiments/shopware/neural/shopware.jsonl'


def walk(node):
    yield node
    for child in node.get('children', ()):
        yield from walk(child)


def widget(projection, component):
    return next(node for node in walk(projection['tree']) if node['component'] == component)


class NeuralShop(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temp = Path(directory.name)
        self.deck = NeuralDeck(HERE/'deck.npz',HERE/'merchant.json')

    def test_held_out_oracle_journeys_without_resets_or_gold_injection(self):
        cases = [row for line in TRACE.read_text().splitlines() if (row:=json.loads(line))['split']=='test']
        cases = [case for case in cases if case['products'][0]['rate'] in (7,19)]
        self.assertGreaterEqual(len(cases),8)
        self.assertIn(625,{c['shipping'] for c in cases})
        for case in cases:
            with self.subTest(index=case['index'],shipping=case['shipping']):
                fixture = self.temp/'merchant.json'
                catalog = {str(i):{**p,'sku':str(i),'active':True,'name':f'Probe {i}'}
                           for i,p in enumerate(case['products'])}
                fixture.write_text(json.dumps({'catalog':catalog,'rate':case['products'][0]['rate'],'shipping':case['shipping']}))
                deck = NeuralDeck(HERE/'weights.npz',fixture)
                ledger = Ledger(self.temp/f"{case['index']}.db",deck,deck.initial)
                oid = None
                for row in case['events']:
                    event = row['event']
                    state = ledger.read()
                    expected_ok = row['status']==200
                    if event.startswith('cart.'):
                        actor,args = BUYER,{'sku':str(row['slot']),'quantity':row['arg']}
                    elif event=='checkout.place':
                        cart = state['carts'].get(BUYER['id'],{'items':{}})
                        quote = deck.quote(state['catalog'],cart['items'])
                        args = {**CONTACT,'quote_key':digest(quote)}
                        actor = BUYER
                    else:
                        actor,args = ADMIN,{'order_id':oid}
                        if event=='order_delivery.track':
                            args['tracking'] = row['arg']
                    if expected_ok:
                        result = ledger.apply(event,actor,args,uuid4().hex)
                        self.assertEqual(result['trace']['weights'],deck.weights.identity)
                        if event=='checkout.place':
                            oid = result['result']['order_id']
                    else:
                        with self.assertRaises(Refusal,msg=event):
                            ledger.apply(event,actor,args,uuid4().hex)
                        self.assertEqual(ledger.read(),state)
                    state = ledger.read()
                    if isinstance(row['after'],dict):
                        items = state['carts'].get(BUYER['id'],{'items':{}})['items']
                        quote = deck.quote(state['catalog'],items)
                        self.assertEqual([quote[k] for k in AMOUNTS],row['after']['totals'],event)
                        priced_lines = {x['sku']:[x['quantity'],x['gross'],x['tax']] for x in quote['lines']}
                        self.assertEqual([priced_lines.get(key,[0,0,0]) for key in catalog],row['after']['lines'],event)
                        if event=='checkout.place':
                            self.assertEqual([state['catalog'][key]['stock'] for key in catalog],row['stock_after'])
                            if expected_ok:
                                order = state['orders'][oid]
                                self.assertEqual([order['quote'][k] for k in AMOUNTS],row['order']['totals'])
                                self.assertEqual([order[k] for k in ('stage','payment','delivery')],
                                                 [row['order'][k] for k in ('stage','payment','delivery')])
                    elif event=='order_delivery.track':
                        self.assertEqual(state['orders'][oid]['tracking'],row['after'])
                    else:
                        self.assertEqual([state['orders'][oid][key] for key in ('stage','payment','delivery')],row['after'],event)

    def test_http_shop_restart_idempotency_and_stock_integrity(self):
        database = self.temp/'shop.sqlite'
        ledger = Ledger(database,self.deck,self.deck.initial)
        def start(active):
            handler = partial(Handler,ledger=active,admin_token=uuid4().hex,admin_password='fixture-password')
            server = ThreadingHTTPServer(('127.0.0.1',0),handler)
            runner = threading.Thread(target=server.serve_forever,daemon=True)
            runner.start()
            return server,runner,f'http://127.0.0.1:{server.server_port}'

        server,runner,base = start(ledger)
        def stop(server,runner):
            server.shutdown()
            server.server_close()
            runner.join(timeout=5)
        self.addCleanup(stop,server,runner)
        buyer,admin,other = Client(base),Client(base),Client(base)
        admin.login()
        self.assertEqual(widget(buyer.view('checkout'),'message')['title'],'Dein Warenkorb ist leer')
        buyer.call('/projection?task=admin',expected=403)
        for client in (buyer,other):
            client.command('cart.add',{'sku':'moka-3','quantity':25})
            self.assertEqual(widget(client.view('checkout'),'list')['rows'][0]['values']['quantity'],8)
        fields = lambda client: {**widget(client.view('checkout'),'form')['actions'][0]['args'],**CONTACT}
        stale_args = fields(other)
        request = uuid4().hex
        checkout = fields(buyer)
        received = buyer.command('checkout.place',checkout,task='checkout',event_id=request)
        oid = received['result']['order_id']
        self.assertEqual(buyer.command('checkout.place',checkout,task='checkout',event_id=request),received)
        self.assertEqual(widget(buyer.view('order'),'detail')['rows'][0]['values']['payment'],'paid')
        self.assertEqual(widget(other.view('order'),'message')['title'],'Hier erscheint deine Bestellung')
        self.assertEqual(ledger.read()['orders'][oid]['consent']['quote_key'],checkout['quote_key'])
        self.assertEqual(ledger.read()['catalog']['moka-3']['stock'],0)
        self.assertEqual(widget(other.view('checkout'),'message')['title'],'Der Bestand hat sich geändert')
        other.command('checkout.place',stale_args,task='checkout',expected=409)
        other.command('cart.remove',{'sku':'moka-3'},task='checkout')
        self.assertEqual(widget(other.view('checkout'),'message')['title'],'Dein Warenkorb ist leer')
        self.assertEqual(len(ledger.read()['orders']),1)
        admin.command('order.complete',{'order_id':oid},task='admin',expected=409)
        admin.command('order.process',{'order_id':oid},task='admin')
        admin.command('order_delivery.track',{'order_id':oid,'tracking':'TEST-NEURAL-17'},task='admin')
        admin.command('order_delivery.ship',{'order_id':oid},task='admin')
        stop(server,runner)
        reloaded = NeuralDeck(HERE/'deck.npz',HERE/'merchant.json')
        restarted = Ledger(database,reloaded,reloaded.initial)
        restarted.settle(test_payment)
        server,runner,base = start(restarted)
        self.addCleanup(stop,server,runner)
        buyer.base = admin.base = base
        admin.login()
        reopened = widget(buyer.view('order'),'detail')['rows'][0]['values']
        self.assertEqual(reopened['delivery'],'shipped')
        self.assertEqual(reopened['tracking'],'TEST-NEURAL-17')
        admin.command('order.complete',{'order_id':oid},task='admin')
        row = widget(buyer.view('order'),'detail')['rows'][0]['values']
        self.assertEqual((row['stage'],row['payment'],row['delivery']),('completed','paid','shipped'))
        self.assertNotIn('buyer',widget(admin.view('admin'),'table')['rows'][0]['values'])

    def test_ablating_the_weights_changes_runtime_outputs(self):
        import numpy as np
        weights = self.deck.weights
        paid = {'id':'test','name':'Test','address':'Test','stage':'in_progress','payment':'paid','delivery':'open',
                'quote':{'gross':1000,'lines':[]},'tracking':'','sequence':1}
        before = {a['event'] for a in widget(self.deck.project('admin',{**self.deck.initial,'orders':{'test':paid}},ADMIN,{}),'table')['rows'][0]['actions']}
        self.assertIn('order_delivery.ship',before)
        for key,value in weights.values.items():
            if key.startswith('net.'):
                value.fill(0)
        after = {a['event'] for a in widget(self.deck.project('admin',{**self.deck.initial,'orders':{'test':paid}},ADMIN,{}),'table')['rows'][0]['actions']}
        self.assertNotIn('order_delivery.ship',after)
        with self.assertRaises(Refusal):
            self.deck.action('order_delivery.ship',{**self.deck.initial,'orders':{'test':paid}},ADMIN,{'order_id':'test'},'')
        weights.values['int.line_tax'] = np.zeros_like(weights.values['int.line_tax'])
        with self.assertRaises(Refusal):
            self.deck.quote(self.deck.initial['catalog'],{'moka-3':1})


    def test_model_builds_and_binds_both_shops(self):
        other = NeuralDeck(HERE/'deck.npz',HERE/'werkraum.json')
        self.assertEqual(self.deck.weights.identity,other.weights.identity)
        for deck, expected in [(self.deck,('grid','split',4289)),(other,('table','stack',5475))]:
            with self.subTest(shop=deck.brand['name']):
                layout, checkout_layout, amount = expected
                ledger = Ledger(self.temp/(deck.brand['theme']+'.sqlite'),deck,deck.initial)
                storefront = deck.project('storefront',ledger.read(),BUYER,{})
                card = widget(storefront,layout)
                chosen = card['rows'][0]['actions'][0]
                sku = chosen['args']['sku']
                ledger.apply(chosen['event'],BUYER,chosen['args'],uuid4().hex)
                # Model emits an additional bound cart and sorts a different storefront context.
                compare = deck.project('storefront',ledger.read(),BUYER,{'intent':'compare'})
                self.assertIn('price',widget(compare,'table')['sorts'])
                self.assertEqual(widget(compare,'list')['rows'][0]['values']['quantity'],1)
                checkout = deck.project('checkout',ledger.read(),BUYER,{})
                self.assertEqual(checkout['tree']['component'],checkout_layout)
                self.assertEqual(widget(checkout,'stats')['rows'][0]['values']['gross'],amount)
                buy = widget(checkout,'form')['actions'][0]
                self.assertEqual(buy['navigate'],{'task':'order','intent':'status'})
                oid = ledger.apply(buy['event'],BUYER,{**buy['args'],**CONTACT},uuid4().hex)['result']['order_id']
                ledger.settle(test_payment)
                for event in ('order.process','order_delivery.track','order_delivery.ship','order.complete'):
                    admin = deck.project('admin',ledger.read(),ADMIN,{'intent':'orders'})
                    grid = widget(admin,'table')
                    self.assertEqual(grid['source'],'orders')
                    self.assertIn('address',grid['details'])
                    action = next(a for a in grid['rows'][0]['actions'] if a['event']==event)
                    self.assertEqual(action['args']['order_id'],oid)
                    args = {**action['args'], 'tracking':'MODEL-BOUND'} if action['fields'] else action['args']
                    ledger.apply(action['event'],ADMIN,args,uuid4().hex)
                receipt = widget(deck.project('order',ledger.read(),BUYER,{}),'detail')['rows'][0]['values']
                self.assertEqual((receipt['id'],receipt['gross'],receipt['tracking'],receipt['stage']), (oid,amount,'MODEL-BOUND','completed'))
                inventory = widget(deck.project('admin',ledger.read(),ADMIN,{'intent':'catalog'}),'table')
                self.assertEqual(inventory['rows'][0]['values']['stock'],deck.initial['catalog'][sku]['stock']-1)

    def test_projected_program_rejects_wrong_scope_and_hidden_total(self):
        state = {**self.deck.initial,**self.deck.action('cart.add',self.deck.initial,BUYER,{'sku':'moka-3','quantity':1},'')['writes']}
        checkout = self.deck.project('checkout',state,BUYER,{})
        sources, actions, quote_key, _, _ = self.deck.read_model(state,BUYER)
        program = [node for node in checkout['program'] if node['source']!='quote']
        with self.assertRaisesRegex(Refusal,'sichtbaren Gesamtpreis'):
            materialize(program,'buyer',sources,actions,quote_key,self.deck.brand)
        hostile = self.deck.projector.decode(('admin','orders','visual','empty','absent'))
        with self.assertRaisesRegex(Refusal,'Datenquelle|Navigationsziel'):
            materialize(hostile,'buyer',sources,actions,quote_key,self.deck.brand)

    def test_no_layout_or_binding_fallback_without_projection_weights(self):
        state = {**self.deck.initial,**self.deck.action('cart.add',self.deck.initial,BUYER,{'sku':'moka-3','quantity':1},'')['writes']}
        decoder = self.deck.projector
        self.deck.project('checkout',state,BUYER,{})
        for name, value in decoder.values.items():
            if name.startswith('ui.argument.'):
                value.fill(0)
        decoder.decode.cache_clear()
        with self.assertRaisesRegex(Refusal,'keine eindeutige Oberfläche'):
            self.deck.project('checkout',state,BUYER,{})
        self.assertEqual(self.deck.quote(state['catalog'],{'moka-3':1})['gross'],4289)

    def test_admin_searches_and_pages_all_orders(self):
        row = {'id':'', 'name':'Alex Test', 'address':'Testweg', 'payment':'paid', 'delivery':'open',
               'stage':'open', 'tracking':'', 'quote':{'gross':4289,'lines':[]}}
        orders = {f'order-{i}':{**row,'id':f'order-{i}','sequence':i} for i in range(502)}
        orders['order-0']['name'] = 'Ältester Händlerkunde'
        state = {**self.deck.initial,'orders':orders}
        view = lambda **query: widget(self.deck.project('admin',state,ADMIN,{'intent':'orders','ops':{'orders':query}}),'table')
        self.assertEqual(view()['paging']['total'],502)
        self.assertEqual(len(view()['rows']),20)
        self.assertEqual(view(page=25)['rows'][-1]['values']['id'],'order-0')
        selected = view(q='Ältester')['rows'][0]
        self.assertEqual(selected['values']['id'],'order-0')
        self.assertEqual(next(a for a in selected['actions'] if a['event']=='order.process')['args']['order_id'],'order-0')
