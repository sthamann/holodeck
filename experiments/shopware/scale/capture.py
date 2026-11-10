"""Record complete Admin and Store API journeys on an isolated Shopware teacher."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
from threading import Lock
import importlib.util
import json
import random
import sys
from pathlib import Path
from urllib.error import HTTPError
from uuid import uuid4

oracle_path = Path(__file__).resolve().parents[1]/'neural/capture.py'
oracle_spec = importlib.util.spec_from_file_location('scale_oracle',oracle_path)
oracle_module = importlib.util.module_from_spec(oracle_spec)
oracle_spec.loader.exec_module(oracle_module)
cents = oracle_module.cents

RATES = (7, 19)
POLICIES = {
    'train': {'percentage':(5,10,17,25),'absolute':(250,700,1900,3000)},
    'test': {'percentage':(7,13,19,31),'absolute':(175,625,1350,3250)},
}


class Oracle(oracle_module.Oracle):
    def __init__(self, shipping):
        self.auth_lock = Lock()
        super().__init__(shipping)

    def admin(self, path, payload=None, method=None):
        headers = self.headers
        try:
            return self.call('/api'+path,payload,method,headers)[0]
        except HTTPError as error:
            if error.code!=401:
                raise
            error.close()
        with self.auth_lock:
            if self.headers is headers:
                self.headers = self.authenticate()
        return super().admin(path,payload,method)


def prices(oracle,rate,unit):
    return [{'currencyId':oracle.channel['currencyId'],'gross':unit/100,
             'net':round(unit/(100+rate),2),'linked':True}]


def promotion(oracle, code, kind, value, active=True):
    oracle.sync('promotion',[{'id':uuid4().hex,'name':code,'active':active,'priority':1,
        'useCodes':True,'useIndividualCodes':False,'useSetGroups':False,'exclusive':False,
        'preventCombination':False,'code':code,
        'salesChannels':[{'id':uuid4().hex,'salesChannelId':oracle.channel['id'],'priority':1}],
        'discounts':[{'id':uuid4().hex,'scope':'cart','type':kind,
                      'value':value if kind=='percentage' else value/100,'considerAdvancedRules':False}]}])


def cart_snapshot(cart):
    positions = []
    for item in cart['lineItems']:
        price = item['price']
        positions.append({'type':item['type'],'quantity':item['quantity'],'unit':cents(price['unitPrice']),
                          'total':cents(price['totalPrice']),
                          'tax':sum(cents(t['tax']) for t in price['calculatedTaxes'])})
    return {'goods':cents(cart['price']['positionPrice']),'gross':cents(cart['price']['totalPrice']),
            'net':cents(cart['price']['netPrice']),'tax':sum(cents(t['tax']) for t in cart['price']['calculatedTaxes']),
            'shipping':sum(cents(x['shippingCosts']['totalPrice']) for x in cart['deliveries']),
            'lines':positions,'errors':sorted({v['messageKey'] for v in (cart['errors'] or {}).values()})
            if isinstance(cart['errors'],dict) else []}


def seed_product(oracle,rate,worker):
    pid = uuid4().hex
    oracle.sync('product',[{'id':pid,'productNumber':'SCALE-'+pid[:10].upper(),'name':f'Scale Probe {worker}',
        'active':True,'isCloseout':True,'stock':30,'taxId':oracle.tax[rate],
        'price':prices(oracle,rate,2975),
        'visibilities':[{'salesChannelId':oracle.channel['id'],'visibility':30}]}])
    return pid


def get_stock(oracle, pid):
    return oracle.search('product',{'ids':[pid],'includes':{'product':['id','stock']}})[0]['stock']


def observed_product(oracle,pid):
    p = oracle.search('product',{'ids':[pid],'includes':{'product':['id','name','price','stock']}})[0]
    return {'name':p['name'],'price':cents(p['price'][0]['gross']),'stock':p['stock']}


def relation(oracle,order,entity):
    items = order.get({'order_transaction':'transactions','order_delivery':'deliveries'}[entity])
    if not items:
        criteria = {'filter':[{'type':'equals','field':'orderId','value':order['id']}]}
        items = oracle.search(entity.replace('_','-'),criteria)
    return items[0]


def state_id(result):
    data = result.get('data',result)
    if isinstance(data,list):
        data = data[0] if data else {}
    return data.get('stateId') or data.get('attributes',{}).get('stateId')


def step(oracle, entity, oid, before, verb):
    status = 200
    try:
        response = oracle.admin(f'/_action/{entity}/{oid}/state/{verb}',{})
    except HTTPError as error:
        if error.code not in (400,409):
            raise
        status = error.code
        error.read()
        response = {}
    after = state_id(response)
    if after is None:
        after = oracle.search(entity.replace('_','-'),{'ids':[oid]})[0]['stateId']
    return {'event':entity+'.'+verb,'before':oracle.states[before],
            'after':oracle.states[after],'status':status},after


def setup(oracle,prefix):
    codes = {}
    for split,policies in POLICIES.items():
        for kind,amounts in policies.items():
            for value in amounts:
                code = f'{prefix}-{split[0].upper()}{kind[0].upper()}{value}'
                promotion(oracle,code,kind,value)
                codes[(split,kind,value)] = code
    promotion(oracle,prefix+'-OFF','percentage',10,active=False)
    return codes


def journey(oracle,pid,rate,session,index,codes,prefix):
    rng = random.Random(721837+index)
    split = 'test' if index%5==0 else 'train'
    kind = 'percentage' if index%2 else 'absolute'
    value = rng.choice(POLICIES[split][kind])
    condition = ('foreign' if index%13==0 else 'inactive' if index%17==0 else 'active')
    coupon = codes[(split,kind,value)] if condition=='active' else prefix+('-OFF' if condition=='inactive' else '-UNKNOWN')
    if condition=='inactive':
        kind,value='percentage',10
    qty = rng.randint(1,5)
    unit = rng.randint(450,14950)
    stock = rng.randint(qty,42)
    change = {'name':f'Scale Artikel {index:05d}','price':unit,'stock':stock}
    oracle.admin('/product/'+pid,{'name':change['name'],'price':prices(oracle,rate,unit),'stock':stock},'PATCH')
    observed = observed_product(oracle,pid)
    cart = oracle.store(session,'/checkout/cart/line-item',{'items':[
        {'id':pid,'referencedId':pid,'type':'product','quantity':qty,'stackable':True,'removable':True}]})
    before = cart_snapshot(cart)
    cart = oracle.store(session,'/checkout/cart/line-item',{'items':[{'type':'promotion','referencedId':coupon}]})
    after = cart_snapshot(cart)
    order = oracle.store(session,'/checkout/order',{})
    delivery = relation(oracle,order,'order_delivery')
    transaction = relation(oracle,order,'order_transaction')
    purchased_stock = get_stock(oracle,pid)
    payment,pay_id = step(oracle,'order_transaction',transaction['id'],transaction['stateId'],'paid')
    ship,ship_id = step(oracle,'order_delivery',delivery['id'],delivery['stateId'],'ship')
    partial = index%3==0 and qty>1
    returned,_ = step(oracle,'order_delivery',delivery['id'],ship_id,'retour_partially' if partial else 'retour')
    refund,_ = step(oracle,'order_transaction',transaction['id'],pay_id,'refund_partially' if partial else 'refund')
    final_stock = get_stock(oracle,pid)
    return {'case':index,'split':split,'rate':rate,'shipping_config':oracle.shipping,
            'catalog':{'request':change,'observed':observed},'quantity':qty,
            'coupon':{'condition':condition,'mode':kind,'value':value,
                      'accepted':any(x['type']=='promotion' for x in after['lines'])},
            'before':before,'after':after,'order':{'gross':cents(order['amountTotal']),'net':cents(order['amountNet']),
            'stock':purchased_stock,'returned_stock':final_stock,'events':[payment,ship,returned,refund]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count',type=int,default=2048)
    parser.add_argument('--workers',type=int,default=12)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume',action='store_true',help='Capture only missing case IDs')
    args = parser.parse_args()
    completed = set()
    if args.resume and args.output.exists():
        with args.output.open() as stream:
            for line in stream:
                index = json.loads(line)['case']
                if type(index) is not int or not 0<=index<args.count or index in completed:
                    raise ValueError('Duplicate or unexpected case ID in the existing capture')
                completed.add(index)
    pending = Queue()
    for index in range(args.count):
        if index not in completed:
            pending.put(index)
    if pending.empty():
        return
    oracle = Oracle(shipping=625)
    prefix = 'HDS'+uuid4().hex[:7].upper()
    codes = setup(oracle,prefix)
    lock = Lock()
    progress = len(completed)
    with args.output.open('a' if args.resume else 'w') as out:
        def worker(number):
            nonlocal progress
            products = {rate:seed_product(oracle,rate,f'{number}-{rate}') for rate in RATES}
            session = {}
            oracle.register(session)
            while True:
                try:
                    index = pending.get_nowait()
                except Empty:
                    break
                try:
                    rate = RATES[index//2%len(RATES)]
                    record = journey(oracle,products[rate],rate,session,index,codes,prefix)
                except HTTPError as error:
                    print(f'case {index}: {error.code} {error.url.split("?")[0]} {error.read().decode()[:450]}',file=sys.stderr,flush=True)
                    raise
                with lock:
                    out.write(json.dumps(record,separators=(',',':'))+'\n')
                    out.flush()
                    progress += 1
                    if progress%50==0 or args.count<=10:
                        print(f'{progress}/{args.count}',flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(worker,range(args.workers)))


if __name__=='__main__':
    main()
