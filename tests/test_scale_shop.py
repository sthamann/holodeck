"""Closed-loop differential replay and a live HTTP commerce test."""
from functools import partial
from collections import Counter
import gzip
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from holodeck.neural import arithmetic_pool
from holodeck.neural_shop import AMOUNTS, NeuralDeck, ORDER_EVENTS
from holodeck.runtime import Ledger, Refusal, digest
from holodeck.server import HERE, Handler
from test_neural_shop import walk, widget
from test_standalone_shop import Client

TRACE = HERE.parents[1]/'experiments/shopware/scale/journeys.jsonl.gz'
ADMIN = {'id':'merchant','role':'admin'}
BUYER = {'id':'buyer','role':'buyer'}
CONTACT = {'name':'Holodeck Test','email':'scale@example.test','address':'Testweg 4','consent':True}


def action_in(view,event):
    for node in walk(view['tree']):
        actions = node.get('actions',[]) + [a for r in node.get('rows',[]) for a in r['actions']]
        for action in actions:
            if action['event']==event:
                return action
    raise AssertionError(f'Missing model-emitted action {event}')


def observed_quote(quote):
    return ({k:quote[k] for k in AMOUNTS},
            [(line['quantity'],line['unit'],line['gross'],line['tax']) for line in quote['lines']],
            quote['discount'])


def teacher_quote(snapshot):
    return ({k:snapshot[k] for k in AMOUNTS},
            [(line['quantity'],line['unit'],line['total'],line['tax'])
             for line in snapshot['lines'] if line['type']=='product'],
            -sum(line['total'] for line in snapshot['lines'] if line['type']=='promotion'))


class ScaleShop(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.deck = NeuralDeck(HERE/'scale.npz',HERE/'marktlabor.json')

    def test_held_out_complete_live_shopware_journeys(self):
        with gzip.open(TRACE,'rt') as stream:
            data = [json.loads(line) for line in stream]
        self.assertEqual([r['case'] for r in data],list(range(2048)))
        self.assertEqual(Counter(r['split'] for r in data),{'train':1638,'test':410})
        tests = [r for r in data if r['split']=='test']
        policy = {
            split:{(r['coupon']['mode'],r['coupon']['value']) for r in data
                   if r['split']==split and r['coupon']['accepted']}
            for split in ('train','test')
        }
        self.assertTrue(policy['train'].isdisjoint(policy['test']))
        self.assertEqual({r['shipping_config'] for r in data},{625})
        decks = {}
        for rate in (7,19):
            package = self.temp/f'merchant-{rate}.json'
            product = {'sku':'p','name':'Initial','price':1000,'stock':4,'active':True,'rate':rate}
            package.write_text(json.dumps({'catalog':{'p':product},'rate':rate,'shipping':625}))
            decks[rate] = NeuralDeck(HERE/'scale.npz',package)
        for row in tests:
            with self.subTest(case=row['case']):
                deck = decks[row['rate']]
                state = deck.initial

                def apply(event, actor, args):
                    nonlocal state
                    result = deck.action(event,state,actor,args,'order')
                    state = {**state,**result['writes']}
                    return result

                def quote():
                    cart = state['carts'][BUYER['id']]
                    return deck.quote(state['catalog'],cart['items'],cart.get('coupon'))

                request = row['catalog']['request']
                apply('catalog.edit',ADMIN,{'sku':'p','version':digest(state['catalog']['p']),**request})
                self.assertEqual({key:state['catalog']['p'][key] for key in request},row['catalog']['observed'])
                apply('cart.add',BUYER,{'sku':'p','quantity':row['quantity']})
                self.assertEqual(observed_quote(quote()),teacher_quote(row['before']))
                coupon = row['coupon']
                deck.promotions = {'CODE':{'active':coupon['condition']!='inactive',
                                           'mode':coupon['mode'],'value':coupon['value']}}
                code = 'FOREIGN' if coupon['condition']=='foreign' else 'CODE'
                if coupon['accepted']:
                    apply('cart.promo',BUYER,{'code':code})
                else:
                    with self.assertRaises(Refusal):
                        apply('cart.promo',BUYER,{'code':code})
                self.assertEqual(observed_quote(quote()),teacher_quote(row['after']))
                apply('checkout.place',BUYER,{**CONTACT,'quote_key':digest(quote())})
                self.assertEqual(state['orders']['order']['quote']['gross'],row['order']['gross'])
                self.assertEqual(state['orders']['order']['quote']['net'],row['order']['net'])
                self.assertEqual(state['catalog']['p']['stock'],row['order']['stock'])
                for step in row['order']['events']:
                    field = ORDER_EVENTS[step['event']]
                    self.assertEqual(state['orders']['order'][field],step['before'])
                    if step['status']==200:
                        apply(step['event'],ADMIN,{'order_id':'order'})
                    else:
                        with self.assertRaises(Refusal):
                            apply(step['event'],ADMIN,{'order_id':'order'})
                    self.assertEqual(state['orders']['order'][field],step['after'])
                self.assertEqual(state['catalog']['p']['stock'],row['order']['returned_stock'])

    def test_two_line_shopware_baskets(self):
        with TRACE.with_name('baskets.jsonl').open() as stream:
            cases = [json.loads(line) for line in stream]
        self.assertEqual(len(cases),64)
        for index, row in enumerate(cases):
            with self.subTest(case=index):
                catalog = {str(i):{'sku':str(i),'name':'Test','price':unit,
                                  'stock':10,'active':True,'rate':row['rate']}
                           for i,unit in enumerate(row['units'])}
                package = self.temp/'basket.json'
                package.write_text(json.dumps({'catalog':catalog,'rate':row['rate'],
                    'shipping':row['shipping'],'promotions':{
                        'BASKET':{'active':True,'mode':row['mode'],'value':row['value']}}}))
                deck = NeuralDeck(HERE/'scale.npz',package)
                quantities = {str(i):q for i,q in enumerate(row['quantities'])}
                for coupon,key in ((None,'before'),('BASKET','after')):
                    expected = teacher_quote(row[key])
                    actual = observed_quote(deck.quote(catalog,quantities,coupon))
                    self.assertEqual((actual[0],sorted(actual[1]),actual[2]),
                                     (expected[0],sorted(expected[1]),expected[2]))

    def test_held_out_quantity_changes(self):
        with TRACE.with_name('quantities.jsonl').open() as stream:
            rows = [json.loads(line) for line in stream]
        self.assertEqual(Counter(r['split'] for r in rows),{'train':18,'test':6})
        sku = next(iter(self.deck.initial['catalog']))
        for row in rows:
            if row['split']!='test':
                continue
            catalog = self.deck.initial['catalog']
            state = {**self.deck.initial,'catalog':{**catalog,sku:{**catalog[sku],'stock':row['stock']}}}
            cart = self.deck.action('cart.add',state,BUYER,{'sku':sku,'quantity':row['before']},'test')
            self.assertEqual(cart['writes']['carts'][BUYER['id']]['items'][sku],row['before'])
            changed = self.deck.action('cart.set',{**state,**cart['writes']},BUYER,
                                       {'sku':sku,'quantity':row['requested']},'test')
            self.assertEqual(changed['writes']['carts'][BUYER['id']]['items'][sku],row['after'])

    def test_discount_changes_with_only_one_learned_head(self):
        catalog = self.deck.initial['catalog']
        sku = next(iter(catalog))
        quantities = {sku:2}
        no_code = self.deck.quote(catalog,quantities)
        normal = self.deck.quote(catalog,quantities,'LAB17')
        self.assertEqual(normal['discount'],1649)
        # Select the input goods amount instead of the fitted percentage program.
        scores = self.deck.weights.values['int.scale.discount.percentage']
        scores.fill(0)
        scores[arithmetic_pool(3).index((0,0))] = 1
        changed = self.deck.quote(catalog,quantities,'LAB17')
        self.assertEqual(changed['discount'],no_code['goods'])
        self.assertEqual(changed['gross'],no_code['shipping'])

    def test_live_http_catalog_coupon_and_return_bindings(self):
        ledger = Ledger(self.temp/'live.sqlite',self.deck,self.deck.initial)
        handler = partial(Handler,ledger=ledger,admin_token='test-token',admin_password='fixture-password')
        http = ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread = Thread(target=http.serve_forever,daemon=True)
        thread.start()
        def close():
            http.shutdown()
            http.server_close()
            thread.join(timeout=5)
        self.addCleanup(close)
        url = f'http://127.0.0.1:{http.server_port}'
        buyer, admin = Client(url), Client(url)
        admin.login()
        editor = action_in(admin.call('/projection?task=admin&intent=catalog'),'catalog.edit')
        sku = editor['args']['sku']
        args = {**editor['args'],'name':'Geprüfter Filter','price':5000,'stock':12}
        admin.command(editor['event'],args,task='admin')
        admin.command(editor['event'],{**args,'stock':9},task='admin',expected=409)
        buyer.command(editor['event'],args,expected=409)
        buyer.command('cart.add',{'sku':sku,'quantity':1})
        quantity = action_in(buyer.view('storefront'),'cart.set')
        buyer.command(quantity['event'],{**quantity['args'],'quantity':2})
        projection = buyer.view('checkout')
        prior = action_in(projection,'checkout.place')['args']
        promo = action_in(projection,'cart.promo')
        buyer.command(promo['event'],{'code':'INVALID'},task='checkout',expected=409)
        buyer.command(promo['event'],{'code':'LAB17'},task='checkout')
        buyer.command('checkout.place',{**prior,**CONTACT},task='checkout',expected=409)
        checkout = buyer.view('checkout')
        quote = widget(checkout,'stats')['rows'][0]['values']
        self.assertEqual((quote['discount'],quote['gross']),(1700,8925))
        buy = action_in(checkout,'checkout.place')
        oid = buyer.command(buy['event'],{**buy['args'],**CONTACT},task='checkout')['result']['order_id']
        admin.command('order_delivery.ship',{'order_id':oid},task='admin')
        for event in ('order_delivery.retour_partially','order_transaction.refund_partially'):
            act = action_in(admin.call('/projection?task=admin&intent=orders'),event)
            self.assertEqual(act['args']['order_id'],oid)
            admin.command(event,act['args'],task='admin')
        view = widget(buyer.view('order'),'detail')['rows'][0]['values']
        self.assertEqual((view['delivery'],view['payment']),('returned_partially','refunded_partially'))
        self.assertEqual(ledger.read()['catalog'][sku]['stock'],10)
