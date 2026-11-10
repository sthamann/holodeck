"""Serve identical Holodeck runtime arms with an optional genuine Qwen adapter."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, partial
from hashlib import sha256
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock
from uuid import uuid4

from holodeck.neural_shop import AMOUNTS, EMPTY_CART, TASKS, NeuralDeck
from holodeck.projection import BINDING_AXES, EVENTS, SOURCE_FIELDS, materialize
from holodeck.runtime import Ledger, OPS, Refusal, digest
from holodeck.server import HERE, Handler, test_payment

from contract import CHECKOUT_REQUESTS, pricing_prompt, prompt

REGISTERS = ('d','x','a','c','j','n')
ARITHMETIC = {'add':(2,OPS['add']),'sub':(2,OPS['sub']),'min':(2,min),'muldiv':(3,OPS['muldiv'])}
STATUS = {
    'stage': {'open','in_progress','completed','cancelled'},
    'payment': {'open','paid','failed','refunded','refunded_partially','cancelled'},
    'delivery': {'open','shipped','shipped_partially','returned','returned_partially','cancelled'},
}


def checked(condition, message):
    if not condition:
        raise Refusal(message)


def expr(node, variables, depth=0):
    checked(depth<6,'Die gelernte Berechnung ist zu tief.')
    if type(node) is int:
        checked(abs(node)<=100,'Ungültige Konstante.')
        return node,None
    if isinstance(node,str) and node in variables:
        return variables[node]
    checked(isinstance(node,list) and len(node)>0 and isinstance(node[0],str) and node[0] in ARITHMETIC,
            'Ungültiger Operator oder Datenverweis.')
    arity,operation=ARITHMETIC[node[0]]
    checked(len(node)==arity+1,'Falsche Anzahl an Operanden.')
    args,units=zip(*(expr(x,variables,depth+1) for x in node[1:]),strict=True)
    if node[0]=='muldiv':
        checked(args[-1]>0,'Ungültige Division.')
        checked(units[1] is None or units[2] is None or units[1]==units[2],
                'Unpassende Einheiten im Verhältnis.')
        return operation(*args),units[0]
    checked(units[0] is None or units[1] is None or units[0]==units[1],
            'Cent und Prozent können hier nicht verbunden werden.')
    return operation(*args),units[0] or units[1]


def arithmetic(program, context, shipping):
    checked(isinstance(program,list) and len(program)==len(REGISTERS),'Die Kasse enthält kein vollständiges Rechenprogramm.')
    units={'g':'cents','b':'cents','t':'cents','r':'percent',
           'v':'percent' if context['mode']=='percentage' else 'cents'}
    variables={key:(context[key],unit) for key,unit in units.items()}
    for target,node in zip(REGISTERS,program,strict=True):
        value,unit=expr(node,variables)
        checked(unit in (None,'cents'),'Das Ergebnis muss in Cent vorliegen.')
        variables[target]=value,'cents'
    d,x,a,c,j,n=(variables[k][0] for k in REGISTERS)
    checked(context['mode']!='none' or (d==0 and x==0),'Der Preisplan enthält einen Rabatt ohne Aktionscode.')
    checked(0<=d<=context['g'] and x>=0 and a==context['g']-d and c==a+shipping
            and 0<=j<=c and j==context['t']-x and n==c-j,'Die Buchungsinvarianten stimmen nicht.')
    return {'discount':d,'goods':a,'gross':c,'tax':j,'net':n}


def ui_program(model, task):
    nodes=model.get('ui')
    checked(isinstance(nodes,list) and 0<len(nodes)<=8,'Das Modell hat keine gültige Komponentenliste erzeugt.')
    layouts={'checkout':{'stats','list','form'},'admin':{'table'}}
    allowed_events={'checkout':{'cart.promo','checkout.place'},'admin':{'order_delivery.ship'}}
    allowed_sources={'checkout':{'quote','cart','_'},'admin':{'orders'}}
    root={'kind':'stack','source':'_','parent':'_','title':'_'}
    program=[root]
    for raw in nodes:
        checked(isinstance(raw,dict),'Ungültiger UI Baustein.')
        kind,source=raw.get('kind'),raw.get('source')
        checked(isinstance(kind,str) and isinstance(source,str) and kind in layouts[task] and source in allowed_sources[task],
                'Der UI Baustein hat einen unbekannten Typ.')
        title=raw.get('title','')
        checked(isinstance(title,str) and len(title)<=120,'Ungültiger Titel.')
        node={'kind':kind,'parent':0,'source':source,'title':title,'text':'_'}
        for axis in BINDING_AXES:
            values=raw.get(axis,[])
            checked(isinstance(values,list) and len(values)<=16 and all(isinstance(v,str) for v in values),
                    'Ungültige UI Bindung.')
            node[axis]=values
        wires=raw.get('wires',{})
        checked(isinstance(wires,dict) and set(wires)==set(node['events']) and set(wires)<=allowed_events[task],
                'Ungültige Aktionsreferenz.')
        node['wires']={}
        for event,wire in wires.items():
            checked(isinstance(wire,dict) and set(wire)==set(EVENTS[event][1]) and all(isinstance(v,str) for v in wire.values()),
                    'Ungültige Aktionsargumente.')
            node['wires'][event]={**wire,'navigate':'order:status' if event=='checkout.place' else '_'}
        program.append(node)
    return program


class Generator:
    def __init__(self,path,adapter):
        self.worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='qwen-metal')
        self.worker.submit(self.load,path,adapter).result()
        self.identity=digest([path.name,sha256((adapter/'adapters.safetensors').read_bytes()).hexdigest() if adapter else None])

    def load(self,path,adapter):
        from mlx_lm import load
        self.model,self.tokenizer=load(str(path),adapter_path=str(adapter) if adapter else None)

    @lru_cache(maxsize=160)
    def generate(self,model_prompt,first_field):
        return self.worker.submit(self.decode,model_prompt,first_field).result()

    def decode(self,model_prompt,first_field):
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        decoder=json.JSONDecoder()
        # A task-specific structural prefix is shared by the two arms. It
        # supplies only the required first property, never its value.
        text='{"'+first_field
        # MLX completion training formats prompt and completion as chat.
        # Feed the exact same assistant prefix to both inference arms.
        tokens=self.tokenizer.apply_chat_template([{'role':'user','content':model_prompt}],
                                                   add_generation_prompt=True,return_dict=False,enable_thinking=False)
        tokens+=self.tokenizer.encode(text,add_special_tokens=False)
        for chunk in stream_generate(self.model,self.tokenizer,tokens,max_tokens=512,sampler=make_sampler(temp=0)):
            text+=chunk.text
            try:
                value,_=decoder.raw_decode(text)
            except json.JSONDecodeError:
                continue
            return value
        raise Refusal('Qwen hat keinen vollständigen ausführbaren Plan geliefert.')


class LiveWorkspace:
    def __init__(self):
        self.requests={}
        self.lock=Lock()

    def create(self,request):
        checked(isinstance(request,str) and 4<=len(request.strip())<=600,'Bitte beschreibe den gewünschten Admin.')
        key=uuid4().hex
        with self.lock:
            if len(self.requests)>=64:
                self.requests.pop(next(iter(self.requests)))
            self.requests[key]={'request':request,'plan':None}
        return key

    def render(self,key,deck,state,actor,args):
        sources,actions,_,_,_=deck.read_model(state,actor)
        with self.lock:
            entry=self.requests.get(key)
            checked(entry is not None,'Die Ansicht ist abgelaufen.')
            if entry['plan'] is None:
                sample=[{k:row[k] for k in ('payment','delivery','gross')} for row in sources['orders'][:6]]
                entry['plan']=deck.generator.generate(prompt('admin',entry['request'],
                    {'order_count':len(sources['orders']),'sample':sample}),'ui')
            plan=entry['plan']
        where=plan.get('where',{})
        checked(isinstance(where,dict) and all(k in STATUS and isinstance(v,str) and v in STATUS[k] for k,v in where.items()),
                'Ungültiger Filter in der generierten Ansicht.')
        sources['orders']=[row for row in sources['orders'] if all(row[k]==v for k,v in where.items())]
        query=dict(args.get('ops',{}))
        raw_query=query.get('orders',{})
        order_query=dict(raw_query) if isinstance(raw_query,dict) else {}
        sort=plan.get('sort')
        if sort is not None:
            checked(isinstance(sort,list) and len(sort)==2 and isinstance(sort[0],str) and sort[0] in SOURCE_FIELDS['orders']-{'items'}
                    and sort[1] in ('asc','desc'),'Ungültige Standardsortierung.')
            if 'sort' not in order_query:
                # Bind the program's requested default order before interactive sorting.
                sources['orders'].sort(key=lambda row:row[sort[0]],reverse=sort[1]=='desc')
        query['orders']=order_query
        program=ui_program(plan,'admin')
        tree=materialize(program,'admin',sources,actions,'',deck.brand,query,
                         lambda oid:deck.order_affordances(state['orders'][oid]))
        return deck.response('admin',tree,program,plan,key)

    def authorize(self,key,deck,event,state,args):
        with self.lock:
            checked(key in self.requests and self.requests[key]['plan'] is not None,
                    'Die Aktion braucht eine bereits generierte Ansicht.')
        order_id=args.get('order_id')
        checked(isinstance(order_id,str) and order_id in state['orders'],'Unbekannte Bestellung.')
        # Authorize the target against the pinned model program and fresh
        # records independently of the page the browser was showing.
        target={**state,'orders':{order_id:state['orders'][order_id]}}
        result=self.render(key,deck,target,{'id':'merchant','role':'admin'},{})
        visible=[a for widget in result['tree']['children'] for row in widget.get('rows',[]) for a in row['actions']]
        checked(any(a['event']==event and a['args']==args for a in visible),
                'Aktion passt nicht zum aktuellen Zustand der generierten Ansicht.')


class QwenDeck(NeuralDeck):
    def __init__(self,merchant,generator,separate_pricing=False):
        super().__init__(HERE/'scale.npz',merchant)
        self.generator=generator
        self.separate_pricing=separate_pricing
        self.identity=digest([self.identity,generator.identity,separate_pricing])
        self.workspace=LiveWorkspace()

    def checkout_plan(self,base,coupon,*,view=True):
        policy=self.promotions.get(coupon)
        valid=bool(policy and self.weights.decide('cart.promo',self.promotion_condition(coupon))[0])
        context={'g':base['goods'],'b':base['gross'],'t':base['tax'],'r':self.rate,
                 'v':policy['value'] if valid else 0,'mode':policy['mode'] if valid else 'none'}
        if not self.separate_pricing:
            return self.generator.generate(prompt('checkout',CHECKOUT_REQUESTS[0],context),'calc'),context
        price=self.generator.generate(pricing_prompt(context),'calc')
        if view:
            surface=self.generator.generate(prompt('checkout',CHECKOUT_REQUESTS[0],context),'calc')
            price={'calc':price.get('calc'),'ui':surface.get('ui')}
        return price,context

    def quote(self,catalog,quantities,coupon=None):
        base=super().quote(catalog,quantities,None)
        if not quantities or not coupon:
            return base
        plan,context=self.checkout_plan(base,coupon,view=False)
        return {**base,**arithmetic(plan.get('calc'),context,base['shipping']),'coupon':coupon}

    def response(self,task,tree,program,plan,workspace=None):
        return {'title':TASKS[task],'tree':tree,'program':program,'qwen_plan':plan,'brand':self.brand,
                'context':(task,'qwen'),'checkpoint':self.identity,'projection':uuid4().hex,
                'workspace':workspace,'workspaces':task=='admin'}

    def project(self,task,state,actor,args):
        if task!='checkout':
            return super().project(task,state,actor,args)
        checked(actor['role']=='buyer','Keine Berechtigung für den Checkout.')
        cart=state['carts'].get(actor['id'],EMPTY_CART)
        if not cart['items']:
            return super().project(task,state,actor,args)
        base=super().quote(state['catalog'],cart['items'],None)
        plan,context=self.checkout_plan(base,cart.get('coupon'))
        # A working checkout requires both a valid commerce program and its UI.
        arithmetic(plan.get('calc'),context,base['shipping'])
        program=ui_program(plan,task)
        sources,actions,key,_,_=self.read_model(state,actor)
        tree=materialize(program,'buyer',sources,actions,key,self.brand,args.get('ops',{}))
        return self.response(task,tree,program,plan)


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--model',type=Path,required=True)
    cli.add_argument('--adapter',type=Path)
    cli.add_argument('--merchant',type=Path,required=True)
    cli.add_argument('--database',type=Path,required=True)
    cli.add_argument('--port',type=int,required=True)
    cli.add_argument('--separate-pricing',action='store_true')
    args=cli.parse_args()
    generator=Generator(args.model,args.adapter)
    deck=QwenDeck(args.merchant,generator,args.separate_pricing)
    ledger=Ledger(args.database,deck,deck.initial)
    ledger.settle(test_payment)
    handler=partial(Handler,ledger=ledger,admin_token=uuid4().hex,admin_password='deck-demo')
    with ThreadingHTTPServer(('127.0.0.1',args.port),handler) as server:
        print(f'Holodeck Qwen http://127.0.0.1:{args.port} · {deck.identity[:12]}',flush=True)
        server.serve_forever()


if __name__=='__main__':
    main()
