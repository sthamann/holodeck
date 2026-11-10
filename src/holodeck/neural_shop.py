"""Bind trained state transitions to local records and renderable components."""
import json
from pathlib import Path
from uuid import uuid4

from .neural import Weights
from .runtime import Refusal, digest


TASKS = {'storefront':'Sortiment','checkout':'Kasse','order':'Bestellstatus','admin':'Shop verwalten'}
ORDER_EVENTS = {
    'order.process':('stage','In Bearbeitung nehmen'),
    'order.complete':('stage','Bestellung abschließen'),
    'order_transaction.paid':('payment','Testzahlung bestätigen'),
    'order_transaction.refund':('payment','Testzahlung erstatten'),
    'order_delivery.ship':('delivery','Als versendet markieren'),
    'order_delivery.retour':('delivery','Retoure erfassen'),
}
CART_EVENTS = {'cart.add','cart.set','cart.remove'}
EMPTY_CART = {'items':{},'last':''}
AMOUNTS = ('goods','shipping','gross','tax','net')


def field(name, label, kind='text', value=''):
    return {'name':name,'label':label,'type':kind,'value':value}


def control(event, title, args, fields=(), navigate=None):
    return {'event':event,'title':title,'args':args,'fields':list(fields),'navigate':navigate}


def panel(component, title, **data):
    return {'component':component,'title':title,**data}


def presence(value):
    return 'present' if value else 'empty'


def refuse_unless(condition, message='Das Modell lässt diese Aktion im aktuellen Zustand nicht zu.'):
    if not condition:
        raise Refusal(message)


class NeuralDeck:
    def __init__(self, checkpoint, merchant):
        self.weights = Weights(checkpoint)
        fixture = json.loads(Path(merchant).read_text())
        self.shipping = fixture['shipping']
        self.rate = fixture['rate']
        if self.rate not in (7,19) or any(p['rate']!=self.rate for p in fixture['catalog'].values()):
            raise ValueError('The measured profile is a uniform 7% or 19% gross shop')
        self.initial = {'catalog':fixture['catalog'],'orders':{},'carts':{}}
        self.identity = digest([self.weights.identity,fixture])

    def roles(self, task):
        return ('admin',) if task=='admin' else ('buyer',) if task in TASKS else ()

    def quote(self, catalog, quantities):
        integer = self.weights.integer
        lines = []
        for sku,qty in quantities.items():
            product = catalog[sku]
            unit,rate = product['price'],product['rate']
            gross = integer('line_gross',unit,qty,rate)
            tax = integer('line_tax',gross,rate)
            lines.append({'sku':sku,'quantity':qty,'unit':unit,'gross':gross,'tax':tax,'name':product['name']})
        count = sum(line['quantity'] for line in lines)
        shipping = integer('shipping.'+presence(count),self.shipping,count)
        shipping_tax = integer('line_tax',shipping,self.rate)
        registers = (sum(row['gross'] for row in lines),sum(row['tax'] for row in lines),shipping,shipping_tax)
        goods,gross,tax = [integer(name,*registers) for name in ('goods','gross','tax')]
        return {'goods':goods,'shipping':shipping,'gross':gross,'tax':tax,
                'net':integer('net',gross,tax),'lines':lines}

    def remaining_stock(self, product, count):
        if not product['active']:
            return None
        stock = self.weights.integer('checkout.stock',product['stock'],count)
        return stock if type(stock) is int and stock>=0 else None

    def action(self, event, state, actor, args, new_id):
        role, owner = actor['role'],actor['id']
        catalog,orders,carts = (state[key] for key in ('catalog','orders','carts'))
        cart = carts.get(owner,EMPTY_CART)
        result, outbox = {}, []
        decide,integer = self.weights.decide,self.weights.integer
        if event in CART_EVENTS:
            refuse_unless(role=='buyer')
            sku = args.get('sku')
            refuse_unless(isinstance(sku,str) and sku in catalog and catalog[sku]['active'],'Unbekannter Artikel.')
            qty = args.get('quantity',0)
            refuse_unless(type(qty) is int and (qty==0 if event=='cart.remove' else 1<=qty<=100),
                          'Bitte gib eine Menge zwischen 1 und 100 an.')
            previous = cart['items'].get(sku,0)
            refuse_unless(decide(event,presence(previous))[0])
            operands = (previous,catalog[sku]['stock']) if event=='cart.remove' else (previous,qty,catalog[sku]['stock'])
            next_qty = integer(event,*operands)
            refuse_unless(0<=next_qty<=100,'Die Menge liegt außerhalb des Testbereichs.')
            items = {**cart['items'],sku:next_qty}
            writes = {'carts':{**carts,owner:{**cart,'items':{k:v for k,v in items.items() if v}}}}
        elif event=='checkout.place':
            refuse_unless(role=='buyer')
            refuse_unless(decide(event,presence(cart['items']))[0],'Der Warenkorb kann so nicht bestellt werden.')
            quote = self.quote(catalog,cart['items'])
            refuse_unless(args.get('quote_key')==digest(quote) and args.get('consent') is True,
                          'Bitte bestätige den aktuellen Gesamtpreis.')
            contact = {field:args.get(field) for field in ('name','address','email')}
            refuse_unless(all(isinstance(v,str) and 0<len(v.strip())<=256 for v in contact.values())
                          and '@' in contact['email'],'Bitte vervollständige die Kontaktdaten.')
            fields = {}
            for key in ('stage','payment','delivery'):
                ok,value = decide('create.'+key,'empty')
                refuse_unless(ok and value!='empty')
                fields[key] = value
            order = {'id':new_id,'buyer':owner,**contact,**fields,'quote':quote,'tracking':'',
                     'sequence':len(orders)+1,'consent':{'confirmed':True,'quote_key':args['quote_key']}}
            remaining = {}
            updated_catalog = dict(catalog)
            for sku,count in cart['items'].items():
                item = catalog[sku]
                stock = self.remaining_stock(item,count)
                refuse_unless(stock is not None,
                              'Der Bestand hat sich geändert. Bitte prüfe deinen Warenkorb.')
                updated_catalog[sku] = {**item,'stock':stock}
                if value:=integer('checkout.cart',count):
                    remaining[sku] = value
            writes = {'orders':{**orders,new_id:order},'catalog':updated_catalog,
                      'carts':{**carts,owner:{'last':new_id,'items':remaining}}}
            outbox = [{'kind':'neural-test-payment','order_id':new_id}]
            result = {'order_id':new_id}
        elif event in ORDER_EVENTS or event=='order_delivery.track':
            refuse_unless(role=='admin' or (role=='provider' and event=='order_transaction.paid'))
            oid = args.get('order_id')
            refuse_unless(isinstance(oid,str) and oid in orders,'Bestellung nicht gefunden.')
            order = orders[oid]
            if event=='order_delivery.track':
                proposed = args.get('tracking')
                refuse_unless(isinstance(proposed,str) and 0<len(proposed.strip())<=128,
                              'Bitte gib eine Sendungsnummer an.')
                ok,value = decide(event,presence(order['tracking']))
                refuse_unless(ok)
                changed = {'tracking':proposed if value=='<argument>' else order['tracking']}
            else:
                key,_ = ORDER_EVENTS[event]
                ok,value = decide(event,order[key])
                refuse_unless(ok)
                changed = {key:value}
            writes = {'orders':{**orders,oid:{**order,**changed}}}
        else:
            raise Refusal('Unbekannte Aktion.')
        return {'writes':writes,'result':result,'outbox':outbox,
                'trace':{'checkpoint':self.identity,'weights':self.weights.identity,'action':event}}

    def order_view(self, order, admin=False):
        visible = {k:order[k] for k in ('id','name','address','stage','payment','delivery','quote','tracking')}
        actions = []
        if admin:
            args = {'order_id':order['id']}
            for event,(key,label) in ORDER_EVENTS.items():
                current = order[key]
                ok, next_value = self.weights.decide(event,current)
                if ok and current!=next_value:
                    actions.append(control(event,label,args))
            if self.weights.decide('order_delivery.track',presence(order['tracking']))[0]:
                actions.append(control('order_delivery.track','Sendungsnummer speichern',args,
                                       [field('tracking','Sendungsnummer',value=order['tracking'])]))
        return {**visible,'actions':actions}

    def project(self, task, state, actor, args):
        refuse_unless(actor['role'] in self.roles(task))
        cart = state['carts'].get(actor['id'],EMPTY_CART)
        catalog = state['catalog']
        query = args.get('q','').casefold()
        if task=='admin':
            page = args.get('page',0)
            orders = [o for o in state['orders'].values() if query in o['id'] or query in o['name'].casefold()]
            orders.sort(key=lambda order:order['sequence'],reverse=True)
            rows = [self.order_view(order,admin=True) for order in orders[page*20:page*20+20]]
            nodes = [panel('orders','Bestellungen',data=rows,page=page,size=20,total=len(orders))]
        elif task=='order':
            order = state['orders'].get(cart['last'])
            nodes = [panel('order','Deine Bestellung',data=self.order_view(order),actions=[])] if order else [
                panel('empty','Hier erscheint deine Bestellung',text='Im Sortiment kannst du einen Kauf starten.',
                      link={'task':'storefront','label':'Zum Sortiment'})]
        else:
            quote = self.quote(catalog,cart['items'])
            lines = []
            for row in quote['lines']:
                sku,qty = row['sku'],row['quantity']
                actions = []
                if self.weights.decide('cart.set',presence(qty))[0]:
                    actions.append(control('cart.set','Menge ändern',{'sku':sku},[field('quantity','Menge','number',qty)]))
                if self.weights.decide('cart.remove',presence(qty))[0]:
                    actions.append(control('cart.remove','Entfernen',{'sku':sku}))
                lines.append({**row,'actions':actions})
            checkout = self.weights.decide('checkout.place',presence(cart['items']))[0]
            shortage = any(self.remaining_stock(catalog[sku],qty) is None for sku,qty in cart['items'].items())
            if task=='storefront':
                products = []
                for sku,row in catalog.items():
                    if not row['active'] or query not in (row['name']+' '+row['variant']).casefold():
                        continue
                    previous = cart['items'].get(sku,0)
                    ok,_ = self.weights.decide('cart.add',presence(previous))
                    changed = ok and self.weights.integer('cart.add',previous,1,row['stock'])>previous
                    actions = [control('cart.add','In den Warenkorb',{'sku':sku,'quantity':1})] if changed else []
                    products.append({**row,'actions':actions})
                nodes = [panel('products','Kannen und Zubehör',data=products)]
                if checkout:
                    nodes.append(panel('lines','Warenkorb',data=lines,link={'task':'checkout','label':'Zur Kasse'}))
            elif checkout and shortage:
                nodes = [panel('lines','Deine Auswahl',data=lines),
                         panel('empty','Der Bestand hat sich geändert',text='Passe die Menge an oder entferne den Artikel.')]
            elif checkout:
                fields = [field('name','Vollständiger Name'),field('email','E-Mail','email'),field('address','Lieferadresse'),
                          field('consent','Ich bestätige die Testbestellung zum angezeigten Preis.','checkbox',False)]
                buy = control('checkout.place','Testbestellung aufgeben',{'quote_key':digest(quote)},fields,'order')
                nodes = [panel('lines','Deine Auswahl',data=lines),panel('totals','Zusammenfassung',data=quote),
                         panel('controls','Lieferung und Testzahlung',actions=[buy])]
            else:
                nodes = [panel('empty','Dein Warenkorb ist leer',text='Wähle zuerst einen Artikel aus.',
                               link={'task':'storefront','label':'Zum Sortiment'})]
        return {'title':TASKS[task],'nodes':nodes,'checkpoint':self.identity,'projection':uuid4().hex}
