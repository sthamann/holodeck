"""Turn an authenticated text request into a typed, learned admin projection."""
from collections import OrderedDict
from copy import deepcopy
import json
from threading import Lock
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np

from .projection import EVENTS, FIELDS, SOURCE_FIELDS, encode_features, materialize
from .runtime import Refusal

SOURCES = ('orders','catalog')
STATUS_FIELDS = {
    'orders': {'stage','payment','delivery'},
    'catalog': {'availability'},
}
NUMBER_FIELDS = {'orders': {'gross'}, 'catalog': {'stock','price'}}
FILTERS = {source: STATUS_FIELDS[source] | NUMBER_FIELDS[source] for source in SOURCES}
OP = ('eq','lt','lte','gt','gte')
# Literals accepted by the business records, independent of any requested page.
STATUSES = {
    'stage': ('open','in_progress','completed','cancelled'),
    'payment': ('open','in_progress','paid','paid_partially','refunded','refunded_partially','failed',
                'cancelled','reminded','authorized','chargeback','unconfirmed'),
    'delivery': ('open','shipped','shipped_partially','returned','returned_partially','cancelled'),
    'availability': ('in_stock','sold_out'),
}
COMPARISONS = {
    'eq': lambda a,b: a==b, 'lt': lambda a,b: a<b, 'lte': lambda a,b: a<=b,
    'gt': lambda a,b: a>b, 'gte': lambda a,b: a>=b,
}
FOCUS = ('all','dispatch','payment','read')
FOCUS_EVENTS = {
    'all': set(EVENTS),
    'dispatch': {event for event in EVENTS if event.startswith('order_delivery.') or event in ('order.process','order.complete')},
    'payment': {event for event in EVENTS if event.startswith('order_transaction.')},
    'read': set(),
}


def object_schema(properties):
    return {'type':'object','additionalProperties':False,'required':list(properties),'properties':properties}


def enumeration(values):
    return {'type':'string','enum':list(values)}


def format_schema():
    field = enumeration(sorted(set().union(*(SOURCE_FIELDS[s] for s in SOURCES)) | {''}))
    predicate = object_schema({'field':field,'op':enumeration(OP),'value':{'type':['string','integer']}})
    view = object_schema({
        'source': enumeration(SOURCES), 'title':{'type':'string','maxLength':90},
        'filters':{'type':'array','maxItems':6,'items':predicate}, 'sort':field,
        'direction':enumeration(('asc','desc')), 'focus':enumeration(FOCUS),
    })
    return object_schema({'title':{'type':'string','maxLength':90}, 'unsupported':{'type':'string','maxLength':180},
                          'views':{'type':'array','maxItems':2,'items':view}})


SYSTEM = """Du übersetzt freie Händleranfragen in eine deklarative Admin-Ansicht.
Eine Ansicht kann Bestellungen (orders) und/oder Produkte (catalog) enthalten. Gib JSON im vorgegebenen Schema aus.
orders: id, name, address, stage (Bestellstatus), payment (Zahlung), delivery (Versand), gross (Bruttobetrag in Cent), tracking, items.
catalog: sku, name, variant, price (Preis in Cent), stock (Stück), availability, image.
Statuswerte: stage=open,in_progress,completed,cancelled. payment=open,in_progress,paid,paid_partially,refunded,refunded_partially,failed,cancelled,reminded,authorized,chargeback,unconfirmed.
delivery=open,shipped,shipped_partially,returned,returned_partially,cancelled. availability=in_stock,sold_out.
Ein Zahlbetrag in Euro wird exakt in Cent umgerechnet. Numerische Filter dürfen eq,lt,lte,gt,gte verwenden. Statusfilter verwenden eq.
Eine Bedingung darf nur in der passenden Datenquelle stehen. Kein Sortierwunsch bedeutet sort="" und direction="asc".
focus=dispatch für Versandbearbeitung, payment für Zahlungsbearbeitung, read bei ausdrücklichem Nur-Lesen-Wunsch, sonst all.
Die Ausgabe beschreibt nur eine Ansicht. Sie führt keine Änderung aus. Die Quelle und ihre Interaktionen müssen zur Anfrage passen.
Bei Anforderungen außerhalb des Schemas setze unsupported auf eine kurze Erklärung und views auf eine leere Liste. Sonst ist unsupported="".
"""


def interpret(prompt, model):
    body = {'model':model,'system':SYSTEM,'prompt':prompt,'format':format_schema(),'stream':False,'think':False,
            'options':{'temperature':0,'num_ctx':4096,'num_predict':650},'keep_alive':'15m'}
    request = Request('http://127.0.0.1:11434/api/generate',json.dumps(body).encode(),
                      {'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=80) as response:
            payload = json.load(response)
        return json.loads(payload['response'])
    except (URLError,TimeoutError,ValueError,KeyError) as error:
        raise Refusal('Der lokale Sprachspezialist hat keine gültige Ansicht geliefert.') from error


def validate(spec):
    def caption(value):
        if not isinstance(value,str) or not value.strip() or len(value)>90:
            raise Refusal('Die Ansicht benötigt einen Titel.')
        return value.strip()
    if not isinstance(spec,dict) or set(spec)!={'title','unsupported','views'} or not isinstance(spec['unsupported'],str):
        raise Refusal('Ungültiger Ansichtsplan.')
    if spec.get('unsupported'):
        raise Refusal(str(spec['unsupported'])[:180])
    views = spec.get('views')
    if not isinstance(views,list) or not 1<=len(views)<=2:
        raise Refusal('Die Anfrage ergibt keine unterstützte Ansicht.')
    result = {'title':caption(spec.get('title')),'views':[]}
    seen = set()
    for view in views:
        if not isinstance(view,dict) or set(view)!={'source','title','filters','sort','direction','focus'}:
            raise Refusal('Ungültiger Ansichtsbaustein.')
        source = view.get('source')
        if not isinstance(source,str) or source not in SOURCES or source in seen:
            raise Refusal('Die Datenquelle ist unbekannt oder doppelt.')
        seen.add(source)
        sort, direction, focus = (view.get(k) for k in ('sort','direction','focus'))
        if not isinstance(sort,str) or sort not in {'',*SOURCE_FIELDS[source]} or direction not in ('asc','desc') or focus not in FOCUS:
            raise Refusal('Ungültige Sortierung oder Aktionsauswahl.')
        filters = view.get('filters')
        if not isinstance(filters,list) or len(filters)>6:
            raise Refusal('Ungültige Filter.')
        for item in filters:
            if not isinstance(item,dict) or set(item)!={'field','op','value'}:
                raise Refusal('Ungültige Filterbedingung.')
            field,op,value = (item[k] for k in ('field','op','value'))
            if not isinstance(field,str) or field not in FILTERS[source] or op not in OP:
                raise Refusal('Dieses Feld ist für den Filter gesperrt.')
            if field in NUMBER_FIELDS[source]:
                if type(value) is not int or not 0<=value<=10**8:
                    raise Refusal('Der Filter benötigt eine Ganzzahl.')
            elif op!='eq' or value not in STATUSES[field]:
                raise Refusal('Unbekannter Status im Filter.')
        result['views'].append({'source':source,'title':caption(view.get('title')),
                                'sort':sort,'direction':direction,'focus':focus,'filters':deepcopy(filters)})
    return result


class Workspace:
    def __init__(self, arrays, model):
        self.schema = json.loads(str(arrays['ws.schema']))
        if self.schema['version']!=1:
            raise ValueError('Unsupported workspace model')
        self.arrays = arrays
        self.model = model
        self.plans = OrderedDict()
        self.lock = Lock()

    def predict(self, head, keys):
        spec = self.schema[head]
        vector = encode_features(spec['inputs'],keys)
        for layer in (0,2,4):
            vector = self.arrays[f'ws.{head}.{layer}.weight'] @ vector + self.arrays[f'ws.{head}.{layer}.bias']
            if layer!=4:
                vector = np.maximum(vector,0)
        result, offset = [],0
        for values in spec['outputs']:
            scores = vector[offset:offset+len(values)]
            index = int(scores.argmax())
            if not np.isfinite(scores).all() or np.count_nonzero(scores==scores[index])!=1:
                raise Refusal('Die UI Gewichte liefern keine eindeutige Ansicht.')
            result.append(values[index])
            offset += len(values)
        return result

    def create(self, prompt):
        if not isinstance(prompt,str) or not 4<=len(prompt.strip())<=1600:
            raise Refusal('Beschreibe die gewünschte Admin-Ansicht.')
        return self.remember(interpret(prompt.strip(),self.model))

    def remember(self, spec):
        plan = validate(spec)
        self.program(plan)
        key = uuid4().hex
        with self.lock:
            self.plans[key] = plan
            if len(self.plans)>64:
                self.plans.popitem(last=False)
        return key

    def program(self, plan):
        layout, = self.predict('layout',(str(len(plan['views'])),))
        root = {'kind':layout,'parent':'_','source':'_','title':'_','text':'_'}
        nodes = [root]
        for view in plan['views']:
            source = view['source']
            raw = self.predict('readout',(source,))
            kind = raw[0]
            attributes = dict(zip(self.schema['groups'],range(len(self.schema['groups'])),strict=True))
            stride = self.schema['width']
            values = {name:[x for x in raw[1+i*stride:1+(i+1)*stride] if x!='_'] for name,i in attributes.items()}
            if view['sort'] and view['sort'] not in values['sorts']:
                raise Refusal('Die gelernte Ansicht kann nach diesem Feld nicht sortieren.')
            chosen = [event for event in values['events'] if event in FOCUS_EVENTS[view['focus']]]
            wires = {}
            for event in chosen:
                wires[event] = {name:self.predict('wire',(source,event,name))[0]
                                for name in (*EVENTS[event][1],'navigate')}
            nodes.append({'kind':kind,'parent':0,'source':source,'title':view['title'],'text':'_',
                          **values,'events':chosen,'wires':wires})
        return nodes

    def plan(self, key):
        with self.lock:
            plan = self.plans.get(key)
        if plan is None:
            raise Refusal('Diese Ansicht ist abgelaufen. Erzeuge sie erneut.')
        return plan

    @staticmethod
    def matches(row, view):
        return all(COMPARISONS[f['op']](row[f['field']],f['value']) for f in view['filters'])

    def authorize(self, key, deck, event, state, args):
        plan = self.plan(key)
        order = state['orders'].get(args.get('order_id'))
        if order is not None:
            views = zip(plan['views'],self.program(plan)[1:],strict=True)
            if any(view['source']=='orders' and event in node['events']
                   and self.matches(deck.order_record(order),view) for view,node in views):
                return
        raise Refusal('Die Bestellung oder Aktion passt nicht mehr zu dieser Ansicht.')

    def render(self, key, deck, state, actor, args):
        plan = self.plan(key)
        sources, actions, quote, _, _ = deck.read_model(state,actor)
        for view in plan['views']:
            source = view['source']
            rows = [row for row in sources[source] if self.matches(row,view)]
            if view['sort']:
                rows.sort(key=lambda row: row[view['sort']],reverse=view['direction']=='desc')
            sources[source] = rows
        program = self.program(plan)
        tree = materialize(program,'admin',sources,actions,quote,deck.brand,args.get('ops',{}),
                           lambda oid: deck.order_affordances(state['orders'][oid]))
        for node, view in zip(tree['children'], plan['views'], strict=True):
            node['criteria'] = [{'label':FIELDS[item['field']][0],'format':FIELDS[item['field']][1],**item}
                                for item in view['filters']]
        return {'title':plan['title'],'tree':tree,'program':program,'brand':deck.brand,
                'context':('admin','workspace',deck.brand['tone'],'empty','absent'),
                'checkpoint':deck.identity,'projection':uuid4().hex,'workspace':key,'plan':plan,'workspaces':True}
