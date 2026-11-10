"""Replay the expanded Shopware model and exercise the new admin boundary."""
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from uuid import uuid4

from holodeck.neural_shop import NeuralDeck
from holodeck.runtime import Ledger, Refusal, digest
from holodeck.server import HERE, Handler, test_payment
from test_standalone_shop import Client

ROOT = HERE.parents[1]
ADMIN = {'id':'test-admin','role':'admin'}
SPEC = {
    'title':'Versand und knappe Ware','unsupported':'',
    'views':[
        {'source':'orders','title':'Noch zu versenden','sort':'gross','direction':'desc','focus':'dispatch',
         'filters':[{'field':'payment','op':'eq','value':'paid'},{'field':'delivery','op':'eq','value':'open'}]},
        {'source':'catalog','title':'Geringer Bestand','sort':'stock','direction':'asc','focus':'read',
         'filters':[{'field':'stock','op':'lt','value':5}]},
    ],
}


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.deck = NeuralDeck(HERE/'workspace.npz',HERE/'nordlager.json','local-fixture')
        self.ledger = Ledger(Path(folder.name)/'db.sqlite',self.deck,self.deck.initial)

    def buy(self, index, sku='NL-D12', pay=False):
        buyer = {'id':'buyer-'+str(index),'role':'buyer'}
        self.ledger.apply('cart.add',buyer,{'sku':sku,'quantity':1},uuid4().hex)
        state = self.ledger.read()
        quote = self.deck.quote(state['catalog'],state['carts'][buyer['id']]['items'])
        args = {'name':f'Test {index}','email':f'test{index}@example.test','address':'Testweg 1',
                'quote_key':digest(quote),'consent':True}
        oid = self.ledger.apply('checkout.place',buyer,args,uuid4().hex)['result']['order_id']
        if pay:
            self.ledger.settle(test_payment)
        return oid

    def test_checkpoint_matches_second_native_transition_matrix(self):
        trace = ROOT/'experiments/shopware/workspace/transitions.jsonl'
        rows = [row for line in trace.read_text().splitlines() if (row:=json.loads(line))['split']=='test']
        self.assertEqual(len(rows),196)
        for row in rows:
            ok = row['status']==200
            result = (ok,row['after'] if ok else row['before'])
            self.assertEqual(self.deck.weights.decide(row['event'],row['before']),result,row)

    def test_closed_loop_replay_of_32_native_steps(self):
        rows = (ROOT/'experiments/shopware/workspace/chains.jsonl').read_text().splitlines()
        self.assertEqual(len(rows),4)
        for index,line in enumerate(rows):
            order = self.buy(index)
            def snapshot():
                record = self.ledger.read()['orders'][order]
                return [record[field] for field in ('stage','payment','delivery')]
            for step in json.loads(line)['events']:
                self.assertEqual(snapshot(),step['before'])
                call = lambda: self.ledger.apply(step['event'],ADMIN,{'order_id':order},uuid4().hex)
                if step['status']==200:
                    call()
                else:
                    with self.assertRaises(Refusal):
                        call()
                self.assertEqual(snapshot(),step['after'],step['event'])

    def test_free_text_entrypoint_and_scope_with_live_records(self):
        eligible = self.buy(1,'NL-B04',True)
        other = self.buy(2,'NL-A01',True)
        self.ledger.apply('order_delivery.ship',ADMIN,{'order_id':other},uuid4().hex)
        handler = partial(Handler,ledger=self.ledger,admin_token='token',admin_password='fixture-password')
        server = ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(close)
        base = f'http://127.0.0.1:{server.server_port}'
        stranger, admin = Client(base), Client(base)
        with patch('holodeck.workspace.interpret',return_value=SPEC) as planner:
            stranger.call('/workspace',{'prompt':'Bitte Versandübersicht bauen'},403)
            planner.assert_not_called()
            admin.login()
            key = admin.call('/workspace',{'prompt':'Bitte Versandübersicht bauen'})['workspace']
            planner.assert_called_once()
        url = '/projection?task=admin&workspace='+key
        stranger.call(url,expected=403)
        tree = admin.call(url)['tree']
        self.assertEqual(tree['component'],'split')
        orders,catalog = tree['children']
        self.assertEqual([row['values']['id'] for row in orders['rows']],[eligible])
        self.assertEqual([row['values']['stock'] for row in catalog['rows']],[2,4])
        ship = next(a for a in orders['rows'][0]['actions'] if a['event']=='order_delivery.ship')
        self.assertEqual(ship['args'],{'order_id':eligible})
        event = {'task':'admin','event':ship['event'],'args':ship['args'],'id':uuid4().hex,'workspace':key}
        # The order is still shippable by Shopware's delivery machine but outside the active workspace.
        self.ledger.apply('order_transaction.refund',ADMIN,{'order_id':eligible},uuid4().hex)
        admin.call('/command',event,expected=409)
        self.assertEqual(self.ledger.read()['orders'][eligible]['delivery'],'open')

    def test_bad_plan_and_missing_readout_fail_closed(self):
        invalid = json.loads(json.dumps(SPEC))
        invalid['views'][0]['sort'] = 'items'
        with self.assertRaises(Refusal):
            self.deck.workspace.remember(invalid)
        for key,value in self.deck.workspace.arrays.items():
            if key.startswith('ws.readout.'):
                value.fill(0)
        with self.assertRaises(Refusal):
            self.deck.workspace.remember(SPEC)
