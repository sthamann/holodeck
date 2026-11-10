"""Shopware-shaped protocol facade over merchant data and compiled model programs.

Protocol serialization, identity and transactions are explicit adapters. Pricing
and learned field transitions execute the native model. Unsupported semantics fail.
"""
from __future__ import annotations
import copy,json,threading,time,uuid
from decimal import Decimal,ROUND_HALF_UP
from common import ROOT,BASE
from packages import Repository,now_ms,uid
from native import NativeModel
from vm import root_of,digest

class APIError(Exception):
    def __init__(self,status,code,detail):self.status=status;self.code=code;self.detail=detail;super().__init__(detail)

def cents(value):
    if isinstance(value,bool):raise ValueError('invalid money')
    return int((Decimal(str(value))*100).quantize(Decimal(1),rounding=ROUND_HALF_UP))

class Commerce:
    def __init__(self,repo=None):
        self.repo=repo or Repository();self.compiled={};self.cache={};self.lock=threading.RLock()
        self.metrics={'native_calls':0,'view_cache_hits':0,'view_cache_misses':0,'large_model_calls':0,'compilations':0}
        for tenant in self.repo.tenants():self.model(self.repo.package(tenant))

    def model(self,package):
        key=digest(package['config'])
        with self.lock:
            if key not in self.compiled:
                self.compiled[key]=NativeModel(package['config'],checkpoint=BASE/'models/base')
                self.metrics['compilations']+=int(self.compiled[key].compiled_now)
            return self.compiled[key]

    def graph(self,tenant,token,items,products,package):
        graph={'tenant':tenant,'revision':0,'root':token,'config':package['config'],'objects':[
            {'id':token,'kind':'root','parent':None,'fields':{'gross':0,'tax':0,'net':0,'goods':0,'order_total':0,'order_net':0,'order_tax':0,'credit':0}}]}
        indexed={p['id']:p for p in products}
        for identifier,quantity in items.items():
            if identifier not in indexed:raise APIError(404,'CONTENT__PRODUCT_NOT_FOUND','Unbekanntes Produkt.')
            p=indexed[identifier]
            if type(quantity) is not int or not 1<=quantity<=10000:raise APIError(400,'CHECKOUT__INVALID_QUANTITY','Die Menge muss zwischen 1 und 10000 liegen.')
            if not p['active']:raise APIError(409,'CHECKOUT__PRODUCT_NOT_ACTIVE','Produkt ist nicht aktiv.')
            if quantity>p['_offer']['stock']:raise APIError(409,'CHECKOUT__INSUFFICIENT_STOCK','Bestand reicht nicht aus.')
            graph['objects'].append({'id':identifier,'kind':'item','parent':token,'fields':{'unit':p['_offer']['unit_cents'],
                'quantity':quantity,'rate':p['taxRate'],'gross':0,'tax':0,'net':0}})
        graph['objects'] += [{'id':token+'-transaction','kind':'transaction','parent':token,'fields':{'state':-1,'amount':0}},
            {'id':token+'-continuation','kind':'continuation','parent':token,'fields':{'active':0,'due':0}}]
        return graph

    def calculate(self,tenant,token,items,products,package):
        graph=self.graph(tenant,token,items,products,package)
        self.metrics['native_calls']+=1
        return self.model(package).forward(graph,{'kind':'price','root':token,'now':int(time.time()),'id':uuid.uuid4().hex},tenant)['graph']

    def price(self,fields,rate):
        return {'unitPrice':fields['unit']/100,'quantity':fields['quantity'],'totalPrice':fields['gross']/100,
                'calculatedTaxes':[{'tax':fields['tax']/100,'taxRate':float(rate),'price':fields['gross']/100,'apiAlias':'cart_tax_calculated'}],
                'taxRules':[{'taxRate':float(rate),'percentage':100.0,'apiAlias':'cart_tax_rule'}],'referencePrice':None,'listPrice':None,'regulationPrice':None,'apiAlias':'calculated_price'}

    def product_rows(self,tenant,criteria=None,db=None):
        criteria=criteria or {};allowed={'ids','filter','sort','limit','page','total-count-mode','associations','includes'}
        if set(criteria)-allowed:raise APIError(501,'SHOPVM__CRITERIA_UNSUPPORTED','Diese Suchoption ist noch nicht abgedeckt.')
        if criteria.get('associations') or criteria.get('includes'):raise APIError(501,'SHOPVM__ASSOCIATIONS_UNSUPPORTED','Diese Projektion ist noch nicht abgedeckt.')
        products=self.repo.products(tenant,db);package=self.repo.package(tenant,db)
        graph=self.calculate(tenant,'catalog',{p['id']:1 for p in products if p['_offer']['stock']>0},[p for p in products if p['_offer']['stock']>0],package)
        calculated={o['id']:o['fields'] for o in graph['objects'] if o['kind']=='item'}
        output=[]
        for p in products:
            fields=calculated.get(p['id'])
            if fields is None:
                # Listing unavailable products still calculates the unit price; stock is an admission invariant for a cart only.
                temporary=copy.deepcopy(p);temporary['_offer']['stock']=1
                one=self.calculate(tenant,'catalog-one',{p['id']:1},[temporary],package)
                fields=next(o['fields'] for o in one['objects'] if o['kind']=='item')
            output.append({k:v for k,v in p.items() if k!='_offer'} | {
                'translated':{'name':p['name'],'description':p['description']},'stock':p['_offer']['stock'],'availableStock':p['_offer']['stock'],
                'calculatedPrice':self.price(fields,p['taxRate']),'calculatedPrices':[],'apiAlias':'product',
                'extensions':{'shopvm':{'priceVersion':p['_offer']['version'],'observedAt':p['_offer']['observed_at_ms']}}})
        if criteria.get('ids'):output=[p for p in output if p['id'] in criteria['ids']]
        for condition in criteria.get('filter',[]):
            field=condition.get('field');op=condition.get('type');value=condition.get('value')
            if field not in ('id','name','productNumber','stock','active'):raise APIError(501,'SHOPVM__FILTER_UNSUPPORTED','Filterfeld noch nicht abgedeckt.')
            if op=='equals':output=[p for p in output if p.get(field)==value]
            elif op=='contains':output=[p for p in output if str(value).casefold() in str(p.get(field,'')).casefold()]
            else:raise APIError(501,'SHOPVM__FILTER_UNSUPPORTED','Filtertyp noch nicht abgedeckt.')
        total=len(output)
        for spec in reversed(criteria.get('sort',[])):
            field=spec.get('field')
            if field not in ('id','name','productNumber','stock'):raise APIError(501,'SHOPVM__SORT_UNSUPPORTED','Sortierung noch nicht abgedeckt.')
            if spec.get('order','ASC').upper() not in ('ASC','DESC'):raise APIError(400,'SHOPVM__SORT_INVALID','Ungültige Sortierrichtung.')
            output.sort(key=lambda p:p[field],reverse=spec.get('order','ASC').upper()=='DESC')
        limit=criteria.get('limit',25);page=criteria.get('page',1)
        if type(limit) is not int or type(page) is not int or not 1<=limit<=500 or page<1:raise APIError(400,'SHOPVM__PAGINATION_INVALID','Ungültige Seiteneinstellung.')
        return output[(page-1)*limit:page*limit],total

    def cart_response(self,graph,products,token,quote):
        if graph['config']['shipping_gross'] or graph['config']['shipping_tax']:
            raise APIError(501,'SHOPVM__SHIPPING_PROJECTION_UNSUPPORTED','Versandpositionen sind in dieser API Fassung noch nicht abgedeckt.')
        root=root_of(graph)['fields'];indexed={p['id']:p for p in products};lines=[];taxes={}
        for obj in graph['objects']:
            if obj['kind']!='item':continue
            p=indexed[obj['id']];f=obj['fields'];rate=f['rate']
            taxes.setdefault(rate,{'tax':0,'price':0});taxes[rate]['tax']+=f['tax'];taxes[rate]['price']+=f['gross']
            lines.append({'id':obj['id'],'referencedId':obj['id'],'type':'product','quantity':f['quantity'],'label':p['name'],
                'good':True,'stackable':True,'removable':True,'payload':{'productNumber':p['productNumber']},'price':self.price(f,rate),'apiAlias':'cart_line_item'})
        price={'netPrice':root['net']/100,'totalPrice':root['gross']/100,'positionPrice':root['goods']/100,'rawTotal':root['gross']/100,
            'calculatedTaxes':[{'tax':v['tax']/100,'taxRate':float(rate),'price':v['price']/100} for rate,v in sorted(taxes.items())],
            'taxRules':[{'taxRate':float(rate),'percentage':((taxes[rate]['price']/100)/(root['gross']/100)*100) if root['gross'] else 100.0/len(taxes)} for rate in sorted(taxes)],'taxStatus':'gross','apiAlias':'cart_price'}
        return {'token':token,'name':'shopvm','lineItems':lines,'price':price,'errors':{},'deliveries':[],
                'extensions':{'shopvm':{'quote':quote,'nativeKey':self.model({'config':graph['config']}).key}},'apiAlias':'cart'}

    def quote(self,items,products,package):
        indexed={p['id']:p for p in products}
        return digest({'offers':[(key,quantity,indexed[key]['_offer']['version']) for key,quantity in sorted(items.items())],
                       'merchant_revision':package['revision'],'config':package['config']})

    def cart(self,tenant,token,change=None,operation='get'):
        with self.repo.connect() as db:
            db.execute('BEGIN IMMEDIATE');ctx=self.repo.ensure_context(tenant,token,db);cart=json.loads(ctx['cart'])
            generation=cart.get('generation',ctx['token'])
            if operation!='get':generation=uuid.uuid4().hex
            items=cart['items']
            if operation=='clear':items={}
            if operation in ('add','update'):
                for item in (change or {}).get('items',[]):
                    identifier=item.get('referencedId',item.get('id'));quantity=item.get('quantity',1)
                    if item.get('type','product')!='product':raise APIError(501,'SHOPVM__LINE_TYPE_UNSUPPORTED','Nur Produktpositionen sind in diesem Versuch unterstützt.')
                    if type(quantity) is not int or quantity<=0:raise APIError(400,'CHECKOUT__INVALID_QUANTITY','Ungültige Menge.')
                    items[identifier]=(items.get(identifier,0)+quantity) if operation=='add' else quantity
            if operation=='remove':
                for identifier in (change or {}).get('ids',[]):items.pop(identifier,None)
            package=self.repo.package(tenant,db);products=self.repo.products(tenant,db)
            if package['config']['shipping_gross'] or package['config']['shipping_tax']:
                raise APIError(501,'SHOPVM__SHIPPING_PROJECTION_UNSUPPORTED','Versandpositionen sind in dieser API Fassung noch nicht abgedeckt.')
            graph=self.calculate(tenant,ctx['token'],items,products,package);quote=self.quote(items,products,package)
            db.execute('UPDATE contexts SET cart=?,revision=revision+1 WHERE token=?',(json.dumps({'items':items,'quote':quote,'generation':generation}),ctx['token']))
            db.commit();return self.cart_response(graph,products,ctx['token'],quote),ctx['token']

    def register(self,tenant,token,data):
        if not data.get('email') or not data.get('firstName') or not data.get('lastName') or not data.get('billingAddress'):raise APIError(400,'FRAMEWORK__WRITE_CONSTRAINT_VIOLATION','Die Kundendaten sind unvollständig.')
        with self.repo.connect() as db:
            db.execute('BEGIN IMMEDIATE');ctx=self.repo.ensure_context(tenant,token,db)
            customer={'id':uuid.uuid4().hex,'email':data['email'],'firstName':data['firstName'],'lastName':data['lastName'],'guest':bool(data.get('guest',True)),'apiAlias':'customer'}
            db.execute('UPDATE contexts SET customer=? WHERE token=?',(json.dumps(customer),ctx['token']));db.commit()
            return customer,ctx['token']

    def checkout(self,tenant,token,body):
        with self.repo.connect() as db:
            db.execute('BEGIN IMMEDIATE');ctx=self.repo.ensure_context(tenant,token,db)
            cart=json.loads(ctx['cart']);items=cart['items']
            receipt_key=ctx['token']+'/'+cart.get('generation',ctx['token'])
            previous=db.execute('SELECT value FROM receipts WHERE tenant=? AND key=?',(tenant,receipt_key)).fetchone()
            if previous:return json.loads(previous[0]),ctx['token']
            if not ctx['customer']:raise APIError(403,'CHECKOUT__CUSTOMER_NOT_LOGGED_IN','Für die Testbestellung bitte Kundendaten angeben.')
            if not items:raise APIError(400,'CHECKOUT__CART_IS_EMPTY','Der Warenkorb ist leer.')
            package=self.repo.package(tenant,db);products=self.repo.products(tenant,db);quote=self.quote(items,products,package)
            graph=self.calculate(tenant,ctx['token'],items,products,package)
            if cart['quote']!=quote:raise APIError(409,'SHOPVM__REQUOTE_REQUIRED','Preis oder Bestand wurde geändert. Warenkorb neu laden und bestätigen.')
            result=self.model(package).forward(graph,{'kind':'order','root':graph['root'],'id':uuid.uuid4().hex,'now':int(time.time())},tenant);self.metrics['native_calls']+=1
            graph=result['graph'];root=root_of(graph)['fields'];identifier=uuid.uuid4().hex
            number=str(10000+db.execute('SELECT count(*) FROM orders WHERE tenant=?',(tenant,)).fetchone()[0])
            cart_view=self.cart_response(graph,products,ctx['token'],quote)
            bound=[{'id':p['id'],**p['_offer']} for p in products if p['id'] in items]
            order={'id':identifier,'orderNumber':number,'amountTotal':root['order_total']/100,'amountNet':root['order_net']/100,
                'price':cart_view['price'],'lineItems':cart_view['lineItems'],'orderCustomer':json.loads(ctx['customer']),
                'stateMachineState':{'technicalName':'open'},'transactions':[{'id':uid(identifier+'/transaction'),'amount':{'totalPrice':root['order_total']/100},'stateMachineState':{'technicalName':'open'}}],
                'extensions':{'shopvm':{'boundOffers':bound,'boundAt':now_ms(),'quote':quote,'model':result['model'],'nativeKey':result['native_key']}},'apiAlias':'order'}
            # Fixed ledger admission: debit available units and create the order atomically.
            # This is an explicit transaction invariant, not claimed as learned inventory behavior.
            for product in bound:
                changed=db.execute('UPDATE products SET stock=stock-?,version=version+1,observed=? WHERE tenant=? AND id=? AND version=? AND stock>=?',
                    (items[product['id']],now_ms(),tenant,product['id'],product['version'],items[product['id']])).rowcount
                if changed!=1:raise APIError(409,'CHECKOUT__STOCK_CONFLICT','Bestand wurde gleichzeitig verändert.')
                db.execute('INSERT INTO changes(tenant,entity,id,version,created) VALUES (?,?,?,?,?)',(tenant,'product',product['id'],product['version']+1,now_ms()))
            db.execute('INSERT INTO orders VALUES (?,?,?,?,?)',(tenant,identifier,ctx['token'],json.dumps(order),json.dumps(graph)))
            db.execute('INSERT INTO receipts VALUES (?,?,?,?)',(tenant,receipt_key,digest({'quote':quote,'items':items}),json.dumps(order)))
            db.execute('UPDATE contexts SET cart=? WHERE token=?',(json.dumps({'items':{},'quote':None,'generation':cart.get('generation',ctx['token'])}),ctx['token']))
            db.commit();return order,ctx['token']

    def orders(self,tenant,ids=None):
        with self.repo.connect() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT value FROM orders WHERE tenant=? ORDER BY rowid DESC',(tenant,))]
        return [row for row in rows if not ids or row['id'] in ids]

    def bound_view(self,tenant,role,search='',sort='name',direction='ASC'):
        version=self.repo.catalog_version(tenant);key=(tenant,role,search,sort,direction,version)
        with self.lock:
            if key in self.cache:self.metrics['view_cache_hits']+=1;return copy.deepcopy(self.cache[key])
        package=self.repo.package(tenant);criteria={'sort':[{'field':sort,'order':direction}]}
        if search:criteria['filter']=[{'type':'contains','field':'name','value':search}]
        rows,_=self.product_rows(tenant,criteria)
        path=ROOT/'views'/f'{role}.json'
        if not path.exists():raise APIError(503,'SHOPVM__VIEW_NOT_READY','Ansicht wurde noch nicht erzeugt.')
        program=json.loads(path.read_text())
        result={'merchant':{'id':tenant,'name':package['name'],'style':package['style']},'view':program,'bindings':{'products':rows,'orders':self.orders(tenant) if role=='merchant' else []},
                'version':version,'runtime':{'mode':'stored-view-with-fresh-bindings','largeModelCalls':0,'nativeKey':self.model(package).key}}
        with self.lock:
            self.cache={k:v for k,v in self.cache.items() if k[0]!=tenant or k[-1]==version}
            self.cache[key]=copy.deepcopy(result);self.metrics['view_cache_misses']+=1
        return result
