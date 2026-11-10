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
from holodeck.runtime import Ledger, Refusal, digest
from holodeck.server import HERE, Handler, test_payment
from test_standalone_shop import Client


BUYER = {'role':'buyer','id':'test-buyer'}
ADMIN = {'role':'admin','id':'test-admin'}
CONTACT = {'name':'Alex Test','email':'alex@example.test','address':'Testweg 7','consent':True}
TRACE = HERE.parents[1] / 'experiments/shopware/neural/shopware.jsonl'


class NeuralShop(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temp = Path(directory.name)
        self.deck = NeuralDeck(HERE/'weights.npz',HERE/'merchant.json')

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
        self.assertEqual(buyer.view('checkout')['nodes'][0]['component'],'empty')
        buyer.call('/projection?task=admin',expected=403)
        for client in (buyer,other):
            client.command('cart.add',{'sku':'moka-3','quantity':25})
            self.assertEqual(client.view('checkout')['nodes'][0]['data'][0]['quantity'],8)
        fields = lambda client: {**client.view('checkout')['nodes'][-1]['actions'][0]['args'],**CONTACT}
        stale_args = fields(other)
        request = uuid4().hex
        checkout = fields(buyer)
        received = buyer.command('checkout.place',checkout,task='checkout',event_id=request)
        oid = received['result']['order_id']
        self.assertEqual(buyer.command('checkout.place',checkout,task='checkout',event_id=request),received)
        self.assertEqual(buyer.view('order')['nodes'][0]['data']['payment'],'paid')
        self.assertEqual(other.view('order')['nodes'][0]['component'],'empty')
        self.assertEqual(ledger.read()['orders'][oid]['consent']['quote_key'],checkout['quote_key'])
        self.assertEqual(ledger.read()['catalog']['moka-3']['stock'],0)
        self.assertEqual(other.view('checkout')['nodes'][-1]['title'],'Der Bestand hat sich geändert')
        other.command('checkout.place',stale_args,task='checkout',expected=409)
        other.command('cart.remove',{'sku':'moka-3'},task='checkout')
        self.assertEqual(other.view('checkout')['nodes'][0]['component'],'empty')
        self.assertEqual(len(ledger.read()['orders']),1)
        admin.command('order.complete',{'order_id':oid},task='admin',expected=409)
        admin.command('order.process',{'order_id':oid},task='admin')
        admin.command('order_delivery.track',{'order_id':oid,'tracking':'TEST-NEURAL-17'},task='admin')
        admin.command('order_delivery.ship',{'order_id':oid},task='admin')
        stop(server,runner)
        reloaded = NeuralDeck(HERE/'weights.npz',HERE/'merchant.json')
        restarted = Ledger(database,reloaded,reloaded.initial)
        restarted.settle(test_payment)
        server,runner,base = start(restarted)
        self.addCleanup(stop,server,runner)
        buyer.base = admin.base = base
        admin.login()
        reopened = buyer.view('order')['nodes'][0]['data']
        self.assertEqual(reopened['delivery'],'shipped')
        self.assertEqual(reopened['tracking'],'TEST-NEURAL-17')
        admin.command('order.complete',{'order_id':oid},task='admin')
        row = buyer.view('order')['nodes'][0]['data']
        self.assertEqual((row['stage'],row['payment'],row['delivery']),('completed','paid','shipped'))
        self.assertNotIn('buyer',admin.view('admin')['nodes'][0]['data'][0])

    def test_ablating_the_weights_changes_runtime_outputs(self):
        import numpy as np
        weights = self.deck.weights
        paid = {'id':'test','name':'Test','address':'Test','stage':'in_progress','payment':'paid','delivery':'open',
                'quote':{},'tracking':''}
        before = {a['event'] for a in self.deck.order_view(paid,admin=True)['actions']}
        self.assertIn('order_delivery.ship',before)
        for key,value in weights.values.items():
            if key.startswith('net.'):
                value.fill(0)
        after = {a['event'] for a in self.deck.order_view(paid,admin=True)['actions']}
        self.assertNotIn('order_delivery.ship',after)
        with self.assertRaises(Refusal):
            self.deck.action('order_delivery.ship',{**self.deck.initial,'orders':{'test':paid}},ADMIN,{'order_id':'test'},'')
        weights.values['int.line_tax'] = np.zeros_like(weights.values['int.line_tax'])
        with self.assertRaises(Refusal):
            self.deck.quote(self.deck.initial['catalog'],{'moka-3':1})
