"""Paired offline comparison through the actual HTTP, ledger and projection path."""
import argparse
from contextlib import contextmanager
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from holodeck.neural_shop import AMOUNTS
from holodeck.runtime import Ledger, digest
from holodeck.server import Handler
from serve import Generator, QwenDeck


ADMIN_REQUEST = 'Ich brauche einen Versandtisch. Nur schon bezahlte Aufträge, bei denen der Versand offen ist. Den höchsten Gesamtbetrag nach oben. Direkt aus der Zeile als versendet markieren.'


class Client:
    def __init__(self,url):
        self.url,self.headers=url,{'Content-Type':'application/json'}

    def call(self,path,data=None,expected=200):
        req=Request(self.url+path,None if data is None else json.dumps(data).encode(),headers=self.headers)
        try:
            response=urlopen(req,timeout=180)
        except HTTPError as error:
            response=error
        with response:
            result=json.load(response)
            if response.status!=expected:
                raise AssertionError({'route':path.split('?')[0],'status':response.status,'response':result})
        for field,header in (('session','X-Deck-Session'),('admin_session','X-Deck-Admin')):
            if field in result:
                self.headers[header]=result[field]
        return result

    def view(self,task,**params):
        return self.call('/projection?'+urlencode({'task':task,**params}))

    def command(self,event,args,task,scope=None,request=None):
        data={'task':task,'event':event,'args':args,'id':request or uuid4().hex}
        if scope:
            data['workspace']=scope
        return self.call('/command',data)


@contextmanager
def server(deck,database,initial):
    ledger=Ledger(database,deck,initial)
    handler=partial(Handler,ledger=ledger,admin_token=uuid4().hex,admin_password='deck-demo')
    with ThreadingHTTPServer(('127.0.0.1',0),handler) as http:
        worker=Thread(target=http.serve_forever,daemon=True)
        worker.start()
        try:
            yield f'http://127.0.0.1:{http.server_port}',ledger
        finally:
            http.shutdown()
            worker.join(timeout=5)


def actions(view,event):
    for node in view['tree'].get('children',[]):
        yield from (a for a in node.get('actions',[]) if a['event']==event)
        for row in node.get('rows',[]):
            yield from (a for a in row['actions'] if a['event']==event)


def finish_checkout(client,ref,ledger):
    view=client.view('checkout')
    assert 'qwen_plan' in view
    quote=next(row['values'] for w in view['tree']['children'] if w.get('source')=='quote'
               for row in w['rows'] if 'gross' in row['values'])
    expected=ref['after']
    assert all(quote[k]==expected[k] for k in AMOUNTS if k in quote),(quote,expected)
    assert quote['discount']==ref['before']['goods']-expected['goods'],quote
    action=next(actions(view,'checkout.place'))
    args={**action['args'],'name':'Hafenlicht Test','email':'buyer@example.test',
          'address':'Testgasse 2','consent':True}
    request=uuid4().hex
    receipt=client.command(action['event'],args,'checkout',request=request)
    assert receipt==client.command(action['event'],args,'checkout',request=request)
    order=ledger.read()['orders'][receipt['result']['order_id']]
    assert {k:order['quote'][k] for k in AMOUNTS}=={k:expected[k] for k in AMOUNTS}
    assert order['payment']==ref['order']['payment']['after']
    return {'checkout':True,'gross':order['quote']['gross'],'discount':order['quote']['discount'],
            'order_id':order['id'],'payment':order['payment'],'qwen_plan':view['qwen_plan']}


def attempt_checkout(url,ledger,ref):
    client=Client(url)
    client.view('storefront')
    for sku,quantity in ref['items'].items():
        client.command('cart.add',{'sku':sku,'quantity':quantity},'storefront')
    first_projection_error=None
    try:
        view=client.view('checkout')
        promo=next(actions(view,'cart.promo'))
        client.command(promo['event'],{'code':ref['code']},'checkout')
    except (AssertionError,StopIteration) as exc:
        first_projection_error=str(exc) or 'No generated coupon control'
        # Isolate the post-coupon capability in the same arm, without credit
        # for a complete generated shopper flow.
        client.command('cart.promo',{'code':ref['code']},'checkout')
    try:
        result=finish_checkout(client,ref,ledger)
    except (AssertionError,StopIteration) as exc:
        result={'checkout':False,'error':str(exc) or 'Missing generated purchase binding'}
    return {**result,'coupon_control':first_projection_error is None,'initial_failure':first_projection_error}


def admin_state(deck,references):
    records={}
    for i,ref in enumerate(references):
        oid=digest(['reference-order',i])[:32]
        catalog=deck.initial['catalog']
        quote={k:ref['after'][k] for k in AMOUNTS}
        quote['lines']=[{'sku':sku,'quantity':qty,'name':catalog[sku]['name']} for sku,qty in ref['items'].items()]
        records[oid]={'id':oid,'buyer':'fixture-buyer','name':f'Referenz {i+1}','address':'Testweg 1',
            'stage':'open','payment':ref['order']['payment']['after'],'delivery':ref['order']['shipping']['before'],
            'tracking':'','quote':quote,'sequence':i+1}
    decoy={**next(iter(records.values())),'id':'f'*32,'payment':'open','sequence':len(records)+1}
    return {**deck.initial,'orders':{**records,decoy['id']:decoy}}


def attempt_admin(url,ledger):
    client=Client(url)
    client.call('/admin-login',{'password':'deck-demo'})
    key=client.call('/workspace',{'prompt':ADMIN_REQUEST})['workspace']
    try:
        view=client.view('admin',workspace=key)
        assert 'qwen_plan' in view
        table=next(w for w in view['tree']['children'] if w['component']=='table')
        before=ledger.read()['orders']
        expected=sorted((o for o in before.values() if o['payment']=='paid' and o['delivery']=='open'),
                        key=lambda o:o['quote']['gross'],reverse=True)
        assert [r['values']['id'] for r in table['rows']]==[o['id'] for o in expected]
        assert 'sort' in table['tools'] and 'gross' in table['sorts']
        clicked=next(a for a in table['rows'][0]['actions'] if a['event']=='order_delivery.ship')
        client.command(clicked['event'],clicked['args'],'admin',scope=key)
        after=ledger.read()['orders']
        assert after[expected[0]['id']]['delivery']=='shipped'
        assert all(after[o['id']]==o for o in before.values() if o['id']!=expected[0]['id'])
        assert expected[0]['id'] not in [r['values']['id'] for r in
             next(w for w in client.view('admin',workspace=key)['tree']['children'] if w['component']=='table')['rows']]
        return {'admin':True,'sorted':[o['quote']['gross'] for o in expected],
                'shipped':expected[0]['id'],'qwen_plan':view['qwen_plan']}
    except (AssertionError,StopIteration,KeyError) as exc:
        return {'admin':False,'error':str(exc) or 'Missing table or row action'}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--model',type=Path,required=True)
    cli.add_argument('--adapter',type=Path)
    cli.add_argument('--shop',type=Path,required=True)
    cli.add_argument('--output',type=Path,required=True)
    cli.add_argument('--separate-pricing',action='store_true')
    args=cli.parse_args()
    generator=Generator(args.model,args.adapter)
    deck=QwenDeck(args.shop/'merchant.json',generator,args.separate_pricing)
    refs=json.loads((args.shop/'reference.json').read_text())
    report={'mode':'adapter' if args.adapter else 'base','model_revision':args.model.name,
            'merchant':deck.brand['name'],'checkout':[]}
    with TemporaryDirectory() as temp:
        with server(deck,Path(temp)/'checkout.sqlite',deck.initial) as (url,ledger):
            for ref in refs:
                report['checkout'].append(attempt_checkout(url,ledger,ref))
            completed={case['order_id'] for case in report['checkout'] if case['checkout']}
            if completed:
                linked=attempt_admin(url,ledger)
                report['same_order_admin']={**linked,'from_checkout':linked.get('shipped') in completed}
        with server(deck,Path(temp)/'admin.sqlite',admin_state(deck,refs)) as (url,ledger):
            report.update(attempt_admin(url,ledger))
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
