"""Record commerce observations from an isolated Shopware Store API and Admin API."""
import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import random
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

BASE = os.environ.get('HOLODECK_TEACHER_URL', 'http://127.0.0.1:18765').rstrip('/')
if urlsplit(BASE).hostname != '127.0.0.1' or urlsplit(BASE).scheme != 'http':
    raise ValueError('The teacher must be a loopback HTTP instance.')


def cents(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


class Oracle:
    def authenticate(self):
        auth, _ = self.call('/api/oauth/token', {
            'grant_type': 'password', 'client_id': 'administration',
            'username': os.getenv('LAB_SW_USER', 'admin'),
            'password': os.getenv('LAB_SW_PASSWORD', 'shopware'),
        })
        return {'Authorization': 'Bearer ' + auth['access_token']}

    def __init__(self, shipping=None):
        self.headers = self.authenticate()
        self.states = {row['id']: row['technicalName'] for row in self.search('state-machine-state', {'limit': 200})}
        self.country = self.search('country', {'filter': [{'type':'equals','field':'iso','value':'DE'}]})[0]['id']
        self.salutation = self.search('salutation', {'limit': 1})[0]['id']
        named = self.search('sales-channel', {'filter': [{'type':'equals','field':'name','value':'ShopVM Kanne'}]})
        storefront = [{'type':'equals','field':'typeId','value':'8a243080f92e4c719546314b577cf82b'},
                      {'type':'equals','field':'active','value':True}]
        base = (named or self.search('sales-channel', {'filter':storefront,'limit':1}))[0]
        self.channel = {key: base[key] for key in (
            'typeId','languageId','currencyId','paymentMethodId','shippingMethodId','countryId',
            'navigationCategoryId','customerGroupId','taxCalculationType',
        )}
        if shipping is not None:
            original = self.search('shipping-method', {'ids':[base['shippingMethodId']]})[0]
            method_id = uuid4().hex
            self.sync('shipping_method',[{'id':method_id,'name':'Holodeck trace shipping','technicalName':'holodeck-'+method_id,'active':True,'taxType':'auto',
                'deliveryTimeId':original['deliveryTimeId'],'availabilityRuleId':original['availabilityRuleId'],
                'prices':[{'id':uuid4().hex,'calculation':1,'quantityStart':0,'currencyPrice':[{'currencyId':base['currencyId'],
                           'gross':shipping/100,'net':shipping/100,'linked':False}]}]}])
            self.channel['shippingMethodId'] = method_id
        self.channel.update(id=uuid4().hex, name='Holodeck neural trace ' + uuid4().hex[:8], active=True,
                            accessKey='SWSC'+uuid4().hex, languages=[{'id':base['languageId']}],
                            currencies=[{'id':base['currencyId']}], countries=[{'id':self.country}],
                            paymentMethods=[{'id':base['paymentMethodId']}], shippingMethods=[{'id':self.channel['shippingMethodId']}])
        self.sync('sales_channel', [self.channel])
        method = self.search('shipping-method', {'ids':[self.channel['shippingMethodId']], 'associations':{'prices':{}}})[0]
        self.shipping = cents(method['prices'][0]['currencyPrice'][0]['gross'])
        self.tax = {row['taxRate']: row['id'] for row in self.search('tax', {'limit': 100})}

    def call(self, path, payload=None, method=None, headers=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(BASE + path, body, {'Accept':'application/json','Content-Type':'application/json', **(headers or {})}, method=method)
        with urlopen(request, timeout=40) as response:
            raw = response.read()
            return (json.loads(raw) if raw else {}, response.headers)

    def admin(self, path, payload=None, method=None):
        return self.call('/api' + path, payload, method, self.headers)[0]

    def search(self, entity, criteria):
        rows = self.admin('/search/' + entity, criteria)['data']
        return [{**row.get('attributes',row), 'id':row['id']} for row in rows]

    def sync(self, entity, rows):
        self.admin('/_action/sync', {'trace':{'entity':entity,'action':'upsert','payload':rows}})

    def store(self, session, path, body=None, method=None):
        headers = {'sw-access-key':self.channel['accessKey'], **session}
        value, received = self.call('/store-api' + path, body, method, headers)
        if received.get('sw-context-token'):
            session['sw-context-token'] = received['sw-context-token']
        return value

    def products(self, items):
        return self.search('product', {'ids':[row['id'] for row in items]})

    def stocks(self, items):
        by_id = {row['id']:row['stock'] for row in self.products(items)}
        return [by_id[row['id']] for row in items]

    def order(self, oid):
        order = self.search('order', {'ids':[oid]})[0]
        criteria = {'filter':[{'type':'equals','field':'orderId','value':oid}]}
        tx = self.search('order-transaction', criteria)[0]
        delivery = self.search('order-delivery', criteria)[0]
        return {'stage':self.states[order['stateId']], 'payment':self.states[tx['stateId']],
                'delivery':self.states[delivery['stateId']], 'tracking':(delivery.get('trackingCodes') or [''])[0],
                'ids':{'order':oid,'order_transaction':tx['id'],'order_delivery':delivery['id']},
                'totals':[cents(order['positionPrice']), cents(order['shippingTotal']), cents(order['amountTotal']),
                          sum(cents(t['tax']) for t in order['price']['calculatedTaxes']), cents(order['amountNet'])]}

    def register(self, session):
        self.store(session, '/account/register', {
            'email':f'neural-{uuid4().hex}@example.test','firstName':'Alex','lastName':'Test','guest':True,
            'salutationId':self.salutation,'storefrontUrl':BASE,
            'billingAddress':{'firstName':'Alex','lastName':'Test','street':'Testweg 7','zipcode':'48624','city':'Schöppingen','countryId':self.country},
        })

    def journey(self, index, split):
        rng = random.Random(513 + index + (10000 if split=='test' else 0))
        rate = [7,19,20][index%3]
        items = [{'id':uuid4().hex, 'price':rng.randint(901, 12000 if split=='train' else 21000),
                  'stock':rng.randint(5, 17), 'rate':rate} for _ in range(2)]
        self.sync('product',[{'id':item['id'],'productNumber':'HDN-'+item['id'][:16], 'name':'Holodeck Probe',
            'active':True,'isCloseout':True,'stock':item['stock'],'taxId':self.tax[rate],
            'price':[{'currencyId':self.channel['currencyId'],'gross':item['price']/100,
                      'net':round(item['price']/(100+rate),2),'linked':True}],
            'visibilities':[{'salesChannelId':self.channel['id'],'visibility':30}]} for item in items])
        session = {}
        self.register(session)
        cart = self.store(session, '/checkout/cart')
        events = []

        def snapshot(value):
            by_id = {row['referencedId']:row for row in value['lineItems']}
            lines = []
            for item in items:
                row = by_id.get(item['id'])
                if row:
                    price = row['price']
                    lines.append([row['quantity'], cents(price['totalPrice']), sum(cents(t['tax']) for t in price['calculatedTaxes'])])
                else:
                    lines.append([0,0,0])
            price = value['price']
            shipping = value['deliveries'][0]['shippingCosts'] if value['deliveries'] else {'totalPrice':0,'calculatedTaxes':[]}
            totals = [cents(price['positionPrice']), cents(shipping['totalPrice']), cents(price['totalPrice']),
                      sum(cents(t['tax']) for t in price['calculatedTaxes']), cents(price['netPrice'])]
            return {'lines':lines,'totals':totals,'shipping_tax':sum(cents(t['tax']) for t in shipping['calculatedTaxes'])}

        def invoke(call):
            try:
                return 200, call()
            except HTTPError as error:
                if error.code not in (400,404,409):
                    raise
                return error.code, json.loads(error.read())

        def cart_event(name, slot, value):
            nonlocal cart
            pid = items[slot]['id']
            path = '/checkout/cart/line-item'
            method = {'add':'POST','set':'PATCH','remove':'DELETE'}[name]
            row = {'id':pid,'referencedId':pid,'type':'product','quantity':value,'stackable':True,'removable':True}
            body = {'ids':[pid]} if name=='remove' else {'items':[row]}
            before = snapshot(cart)
            status, response = invoke(lambda:self.store(session,path,body,method))
            cart = response if status==200 else cart
            events.append({'event':'cart.'+name,'slot':slot,'arg':value,'status':status,'before':before,'after':snapshot(cart),
                           'errors':sorted({e['messageKey'] for e in cart['errors'].values()}) if isinstance(cart.get('errors'),dict) else []})

        def checkout():
            nonlocal cart
            before = snapshot(cart)
            before_stock = self.stocks(items)
            status,response = invoke(lambda:self.store(session,'/checkout/order',{}))
            cart = self.store(session,'/checkout/cart')
            after_stock = self.stocks(items)
            order = self.order(response['id']) if status==200 else None
            events.append({'event':'checkout.place','status':status,'before':before,'after':snapshot(cart),
                           'stock_before':before_stock,'stock_after':after_stock,
                           'order':{key:value for key,value in order.items() if key!='ids'} if order else None})
            return order

        def track():
            current = self.order(oid)
            value = f"TRAIN-{rng.randrange(100000,999999)}"
            status,_ = invoke(lambda:self.admin("/order-delivery/"+current["ids"]["order_delivery"], {"trackingCodes":[value]}, "PATCH"))
            changed = self.order(oid)
            events.append({"event":"order_delivery.track","status":status,"arg":value,
                           "before":current["tracking"],"after":changed["tracking"]})

        def transition(entity, name):
            current = self.order(oid)
            status,_ = invoke(lambda:self.admin(f'/_action/{entity}/{current["ids"][entity]}/state/{name}',{}))
            changed = self.order(oid)
            select = lambda record: [record['stage'],record['payment'],record['delivery']]
            events.append({'event':entity+'.'+name,'status':status,'before':select(current),'after':select(changed)})

        checkout()
        cart_event('set',0,2)
        cart_event('remove',0,0)
        cart_event('add',0,rng.randint(1,3))
        cart_event('add',0,rng.randint(1,3))
        cart_event('add',0,items[0]['stock'] + rng.randint(1,4))
        cart_event('set',0,items[0]['stock'] + rng.randint(1,4))
        cart_event('set',0,rng.randint(1,4))
        cart_event('add',1,rng.randint(1,3))
        if index%2:
            cart_event('remove',1,0)
        order = checkout()
        if not order:
            raise RuntimeError(f'Nonempty checkout refused in {split} {index}: {events[-1]}')
        oid = order['ids']['order']
        track()
        transition('order','complete')
        transition('order_transaction','refund')
        transition('order_delivery','retour')
        if index%2:
            transition('order_delivery','ship')
        transition('order_transaction','paid')
        transition('order_transaction','paid')
        transition('order','process')
        if split=='test' and index%2==0:
            transition('order','complete')
        transition('order','process')
        if not index%2:
            transition('order_delivery','ship')
        transition('order_delivery','ship')
        transition('order','complete')
        transition('order','complete')
        transition('order','process')
        track()
        if index%3==0:
            transition('order_delivery','retour')
            transition('order_transaction','refund')
            transition('order_delivery','retour')
            transition('order_transaction','refund')
            transition('order_delivery','ship')
            transition('order_transaction','paid')
        return {'split':split,'index':index,'products':[{k:v for k,v in row.items() if k!='id'} for row in items],
                'shipping':self.shipping,'closeout':True,'events':events}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train',type=int,default=18)
    parser.add_argument('--test',type=int,default=6)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--shipping',type=int,help='Flat shipping price in cents')
    args = parser.parse_args()
    oracle = Oracle(args.shipping)
    jobs = [(i,split) for split,n in [('train',args.train),('test',args.test)] for i in range(args.offset,args.offset+n)]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool, args.output.open('w') as target:
        for job in pool.map(lambda pair:oracle.journey(*pair),jobs):
            target.write(json.dumps(job,separators=(',',':'))+'\n')
            target.flush()
            print(job['split'],job['index'],len(job['events']),flush=True)


if __name__=='__main__':
    main()
