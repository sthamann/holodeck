"""Bind trained state transitions to local records and renderable components."""
import json
from pathlib import Path
from uuid import uuid4

from .neural import Weights
from .projection import INTENTS, Projector, materialize
from .runtime import Refusal, digest
from .workspace import Workspace


TASKS = {'storefront':'Sortiment','checkout':'Kasse','order':'Bestellstatus','admin':'Verwaltung'}
ORDER_EVENTS = {
    'order.process':'stage', 'order.complete':'stage',
    'order_transaction.paid':'payment', 'order_transaction.refund':'payment',
    'order_delivery.ship':'delivery', 'order_delivery.retour':'delivery',
    'order_transaction.fail':'payment', 'order_transaction.reopen':'payment',
    'order_delivery.ship_partially':'delivery', 'order_delivery.reopen':'delivery',
    'order_delivery.retour_partially':'delivery', 'order_transaction.refund_partially':'payment',
}
CART_EVENTS = {'cart.add','cart.set','cart.remove'}
EXTENDED_ORDER_EVENTS = {'order_delivery.retour_partially','order_transaction.refund_partially'}
EMPTY_CART = {'items':{},'last':''}
AMOUNTS = ('goods','shipping','gross','tax','net')


def presence(value):
    return 'present' if value else 'empty'


def refuse_unless(condition, message='Das Modell lässt diese Aktion im aktuellen Zustand nicht zu.'):
    if not condition:
        raise Refusal(message)


class NeuralDeck:
    def __init__(self, checkpoint, merchant, workspace_model=None):
        self.weights = Weights(checkpoint)
        fixture = json.loads(Path(merchant).read_text())
        self.brand = {'name': 'Holoshop', 'tone': 'visual', 'theme': 'kanne', **fixture.get('brand', {})}
        if self.brand['tone'] not in ('visual', 'dense') or self.brand['theme'] not in ('kanne', 'werkraum'):
            raise ValueError('Unknown merchant presentation profile')
        self.workspace = Workspace(self.weights.values,workspace_model) if workspace_model and 'ws.schema' in self.weights.values else None
        self.projector = Projector(self.weights.values) if 'ui.schema' in self.weights.values else None
        self.extended = 'int.scale.catalog.price' in self.weights.values
        self.promotions = fixture.get('promotions', {})
        for code, policy in self.promotions.items():
            mode,value = policy['mode'],policy['value']
            if (not code or code!=code.strip().upper() or type(policy['active']) is not bool
                or mode not in ('absolute','percentage') or type(value) is not int
                or not 1<=value<=(100 if mode=='percentage' else 10**8)):
                raise ValueError('Invalid configured promotion')
        self.shipping = fixture['shipping']
        self.rate = fixture['rate']
        if self.rate not in (7,19) or any(p['rate']!=self.rate for p in fixture['catalog'].values()):
            raise ValueError('The measured profile is a uniform 7% or 19% gross shop')
        self.initial = {'catalog':fixture['catalog'],'orders':{},'carts':{}}
        self.identity = digest([self.weights.identity,fixture])

    def roles(self, task):
        return ('admin',) if task=='admin' else ('buyer',) if task in TASKS else ()

    def quote(self, catalog, quantities, coupon=None):
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
        result = {'goods':goods,'shipping':shipping,'gross':gross,'tax':tax,
                  'net':integer('net',gross,tax),'lines':lines}
        if self.extended:
            result['discount'] = 0
            if coupon and self.weights.decide('cart.promo',self.promotion_condition(coupon))[0]:
                policy = self.promotions[coupon]
                mode,value = policy['mode'],policy['value']
                inputs = (goods,value,100) if mode=='percentage' else (goods,value)
                reduction = integer('scale.discount.'+mode,*inputs)
                reduction_tax = integer('scale.discount.tax',reduction,self.rate)
                result['coupon'] = coupon
                result['discount'] = reduction
                result['goods'] = integer('scale.discount.balance',goods,reduction)
                result['gross'] = integer('scale.discount.balance',gross,reduction)
                result['tax'] = integer('scale.discount.balance',tax,reduction_tax)
                result['net'] = integer('net',result['gross'],result['tax'])
        return result

    def promotion_condition(self, code):
        policy = self.promotions.get(code)
        if policy is None:
            return 'foreign'
        return 'active' if policy['active'] else 'inactive'

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
            updated = {**cart,'items':{k:v for k,v in items.items() if v}}
            if not updated['items']:
                updated.pop('coupon',None)
            writes = {'carts':{**carts,owner:updated}}
        elif event=='cart.promo':
            refuse_unless(role=='buyer' and self.extended and cart['items'])
            raw = args.get('code')
            refuse_unless(isinstance(raw,str) and 0<len(raw.strip())<=64,'Bitte einen Rabattcode eingeben.')
            code = raw.strip().upper()
            refuse_unless(decide(event,self.promotion_condition(code))[0],'Der Rabattcode ist hier nicht gültig.')
            writes = {'carts':{**carts,owner:{**cart,'coupon':code}}}
        elif event=='catalog.edit':
            refuse_unless(role=='admin' and self.extended)
            sku = args.get('sku')
            refuse_unless(isinstance(sku,str) and sku in catalog,'Unbekannter Artikel.')
            old = catalog[sku]
            refuse_unless(args.get('version')==digest(old),'Der Artikel wurde geändert. Lade die Ansicht neu.')
            name,price,stock = (args.get(k) for k in ('name','price','stock'))
            refuse_unless(isinstance(name,str) and 0<len(name.strip())<=128 and
                          type(price) is int and type(stock) is int and 0<price<=10**8 and 0<=stock<=10**6,
                          'Bitte Namen, Preis und Bestand prüfen.')
            ok,pointer = decide('catalog.name','text')
            refuse_unless(ok and pointer=='<argument>')
            new = {**old,'name':name,
                   'price':integer('scale.catalog.price',price),
                   'stock':integer('scale.catalog.stock',stock)}
            writes = {'catalog':{**catalog,sku:new}}
        elif event=='checkout.place':
            refuse_unless(role=='buyer')
            refuse_unless(decide(event,presence(cart['items']))[0],'Der Warenkorb kann so nicht bestellt werden.')
            quote = self.quote(catalog,cart['items'],cart.get('coupon'))
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
            refuse_unless(self.extended or event not in EXTENDED_ORDER_EVENTS)
            refuse_unless(role=='admin' or (role=='provider' and event in ('order_transaction.paid','order_transaction.fail')))
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
                key = ORDER_EVENTS[event]
                ok,value = decide(event,order[key])
                refuse_unless(ok)
                changed = {key:value}
            writes = {'orders':{**orders,oid:{**order,**changed}}}
        else:
            raise Refusal('Unbekannte Aktion.')
        return {'writes':writes,'result':result,'outbox':outbox,
                'trace':{'checkpoint':self.identity,'weights':self.weights.identity,'action':event}}

    def order_affordances(self, order):
        decide = self.weights.decide
        actions = set()
        for event, key in ORDER_EVENTS.items():
            if not self.extended and event in EXTENDED_ORDER_EVENTS:
                continue
            before = order[key]
            ok, after = decide(event, before)
            if ok and before != after:
                actions.add(event)
        if decide('order_delivery.track', presence(order['tracking']))[0]:
            actions.add('order_delivery.track')
        return actions

    @staticmethod
    def order_record(order):
        return {**{k: order[k] for k in ('id', 'name', 'address', 'stage', 'payment', 'delivery', 'tracking')},
                'gross': order['quote']['gross'],
                'items': [{k: line[k] for k in ('name', 'quantity')} for line in order['quote']['lines']]}

    def read_model(self, state, actor):
        """Expose typed records and live commerce capabilities, without UI decisions."""
        role = actor['role']
        decide = self.weights.decide
        catalog = state['catalog']
        cart = state['carts'].get(actor['id'], EMPTY_CART)
        products = [{**item, 'availability': 'in_stock' if item['stock'] else 'sold_out'}
                    for item in catalog.values() if item['active'] or role == 'admin']
        sources, affordances = {'catalog': products}, {}
        if role == 'admin':
            if self.extended:
                for row in products:
                    sku = row['sku']
                    row['version'] = digest(catalog[sku])
                    affordances[('catalog',sku)] = {'catalog.edit'}
            orders = sorted(state['orders'].values(), key=lambda order: order['sequence'], reverse=True)
            sources['orders'] = [self.order_record(order) for order in orders]
            sources['metrics'] = [{
                'total_orders': len(orders), 'paid_orders': sum(o['payment']=='paid' for o in orders),
                'open_deliveries': sum(o['delivery']=='open' for o in orders),
                'active_products': sum(p['active'] for p in products),
            }]
            return sources, affordances, '', 'empty', 'absent'

        quote = self.quote(catalog, cart['items'],cart.get('coupon'))
        sources['cart'] = quote['lines']
        sources['quote'] = [{key:quote[key] for key in AMOUNTS}]
        if self.extended:
            sources['quote'][0]['discount'] = quote['discount']
        order = state['orders'].get(cart['last'])
        owned = order is not None and order['buyer'] == actor['id']
        sources['receipt'] = [self.order_record(order)] if owned else []
        for product in products:
            sku = product['sku']
            before = cart['items'].get(sku, 0)
            ok = decide('cart.add', presence(before))[0]
            if ok and self.weights.integer('cart.add', before, 1, product['stock']) > before:
                affordances[('catalog', sku)] = {'cart.add'}
        for row in sources['cart']:
            observed = presence(row['quantity'])
            affordances[('cart', row['sku'])] = {
                event for event in ('cart.set', 'cart.remove') if decide(event, observed)[0]
            }
        cart_status = 'empty'
        if decide('checkout.place', presence(cart['items']))[0]:
            shortage = any(self.remaining_stock(catalog[sku], qty) is None for sku, qty in cart['items'].items())
            cart_status = 'blocked' if shortage else 'ready'
            if not shortage:
                affordances[('_', None)] = {'checkout.place'}
                if self.extended:
                    affordances[('_', None)].add('cart.promo')
        return sources, affordances, digest(quote), cart_status, 'present' if owned else 'absent'

    def authorize_scope(self, scope, event, state, actor, args):
        refuse_unless(actor['role']=='admin' and self.workspace is not None)
        self.workspace.authorize(scope,self,event,state,args)

    def project(self, task, state, actor, args):
        refuse_unless(actor['role'] in self.roles(task))
        if args.get('workspace'):
            refuse_unless(task=='admin' and actor['role']=='admin' and self.workspace is not None)
            return self.workspace.render(args['workspace'],self,state,actor,args)
        if self.projector is None:
            raise Refusal('Dieser Modellstand enthält keine Oberflächengewichte.')
        intents = INTENTS[task]
        intent = args.get('intent') or intents[0]
        if intent not in intents:
            raise Refusal('Unbekannte Ansicht.')
        sources, actions, quote_key, cart, receipt = self.read_model(state, actor)
        context = (task, intent, self.brand['tone'], cart, receipt)
        program = self.projector.decode(context)
        queries = args.get('ops', {})
        tree = materialize(program, actor['role'], sources, actions, quote_key, self.brand, queries,
                           lambda oid: self.order_affordances(state['orders'][oid]))
        return {'title':TASKS[task], 'tree':tree, 'program':program, 'brand':self.brand,
                'context':context, 'checkpoint':self.identity, 'projection':uuid4().hex,
                'workspaces': self.workspace is not None and actor['role']=='admin'}
