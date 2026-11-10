"""Frozen generic interpreter and transactional object store. No commerce handlers."""
from __future__ import annotations
import copy, hashlib, json, sqlite3
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
VOCAB=json.loads((HERE/'vocabulary.json').read_text())
VOCAB_HASH=hashlib.sha256((HERE/'vocabulary.json').read_bytes()).hexdigest()

def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def root_of(graph): return next(o for o in graph['objects'] if o['id']==graph['root'])

def retrieve(graph,event,tenant):
    if graph['tenant']!=tenant: raise ValueError('tenant mismatch')
    if event['root']!=graph['root']: raise ValueError('root mismatch')
    result=[o for o in graph['objects'] if o['id']==event['root'] or o['parent']==event['root']]
    if len(result)>VOCAB['limits']['max_objects']: raise ValueError('subgraph limit')
    if not any(o['id']==event['root'] for o in result): raise ValueError('missing root')
    if len({o['id'] for o in result})!=len(result): raise ValueError('ambiguous object identity')
    return result

def evaluate(expr, obj, graph, event, depth=0):
    if depth>VOCAB['limits']['expression_depth']: raise ValueError('expression depth')
    op=expr[0]
    if op not in VOCAB['operations']: raise ValueError('unknown primitive')
    if op=='const': value=expr[1]
    elif op=='read':
        source={'self':obj['fields'],'root':root_of(graph)['fields'], 'config':graph['config'],'event':event}[expr[1]]
        value=source[expr[2]]
    elif op=='sum':
        value=sum(o['fields'][expr[2]] for o in graph['objects'] if o['parent']==event['root'] and o['kind']==expr[1])
    elif op=='select':
        value=evaluate(expr[2] if evaluate(expr[1],obj,graph,event,depth+1) else expr[3],obj,graph,event,depth+1)
    else:
        a=[evaluate(child,obj,graph,event,depth+1) for child in expr[1:]]
        if op=='add': value=a[0]+a[1]
        elif op=='sub': value=a[0]-a[1]
        elif op=='mul': value=a[0]*a[1]
        elif op=='muldiv':
            if a[2]==0: raise ValueError('zero divisor')
            numerator=a[0]*a[1]; denominator=a[2]
            sign=-1 if (numerator<0)!=(denominator<0) else 1
            value=sign*((2*abs(numerator)+abs(denominator))//(2*abs(denominator)))
        elif op=='min': value=min(a)
        elif op=='max': value=max(a)
        elif op=='eq': value=int(a[0]==a[1])
        elif op=='le': value=int(a[0]<=a[1])
        else: raise ValueError('unsupported operation')
    if type(value) is not int or abs(value)>=2**VOCAB['limits']['integer_bits']: raise ValueError('integer range/type')
    return value

def expression_text(e):
    op=e[0]
    if op=='const': return str(e[1])
    if op=='read': return '.'.join(e[1:])
    if op=='sum': return 'Summe('+e[1]+'.'+e[2]+')'
    if op=='select': return f'wenn {expression_text(e[1])}, dann {expression_text(e[2])}, sonst {expression_text(e[3])}'
    if op=='muldiv': return f'runden({expression_text(e[1])} × {expression_text(e[2])} / {expression_text(e[3])})'
    symbol={'add':'+','sub':'−','mul':'×','eq':'=','le':'≤'}.get(op)
    if symbol: return '('+f' {symbol} '.join(expression_text(x) for x in e[1:])+')'
    return op+'('+', '.join(expression_text(x) for x in e[1:])+')'

class Model:
    def __init__(self, path, ablate=False):
        path=Path(path); self.meta=json.loads(path.with_suffix('.json').read_text())
        if self.meta['vocabulary_sha256']!=VOCAB_HASH: raise ValueError('incompatible primitive vocabulary')
        with np.load(path.with_suffix('.npz'),allow_pickle=False) as archive:
            self.weights={k:archive[k].copy() for k in archive.files}
        self.identity=hashlib.sha256(path.with_suffix('.npz').read_bytes()+path.with_suffix('.json').read_bytes()).hexdigest()
        if ablate:
            rng=np.random.default_rng(182)
            self.weights={k:rng.normal(size=w.shape) for k,w in self.weights.items()}

    def program(self,event):
        if event['kind'] not in self.meta['plans']: raise ValueError('unlearned event')
        result=[]
        for key in self.meta['plans'][event['kind']]:
            head=self.meta['heads'][key]
            index=int(self.weights[key].argmax())
            result.append({'kind':head['kind'],'field':head['field'],'expr':head['candidates'][index], 'head':key})
        return result

    def forward(self,graph,event,tenant):
        retrieved=retrieve(graph,event,tenant)
        work=copy.deepcopy(graph)
        # Only the retrieved, tenant-checked subgraph is available to arithmetic and binding.
        work['objects']=copy.deepcopy(retrieved)
        program=self.program(event); writes=[]
        for instruction in program:
            for obj in work['objects']:
                if obj['kind']!=instruction['kind']: continue
                value=evaluate(instruction['expr'],obj,work,event)
                if value!=obj['fields'][instruction['field']]:
                    writes.append({'id':obj['id'],'field':instruction['field'],'old':obj['fields'][instruction['field']],'value':value})
                obj['fields'][instruction['field']]=value
        # Objects outside the selected root remain unchanged.
        changed={o['id']:o for o in work['objects']}
        result=copy.deepcopy(graph); result['objects']=[changed.get(o['id'],o) for o in result['objects']]
        return {'graph':result,'writes':writes,'program':program,'read_ids':[o['id'] for o in retrieved],'model':self.identity}

class Store:
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=20,isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS graphs (tenant TEXT, root TEXT, revision INTEGER, value TEXT, PRIMARY KEY(tenant,root))')
        self.db.execute('CREATE TABLE IF NOT EXISTS receipts (tenant TEXT, event_id TEXT, request_hash TEXT, value TEXT, PRIMARY KEY(tenant,event_id))')

    def seed(self,graph):
        self.db.execute('INSERT INTO graphs VALUES (?,?,?,?)',(graph['tenant'],graph['root'],graph['revision'],json.dumps(graph)))

    def read(self,tenant,root):
        row=self.db.execute('SELECT value FROM graphs WHERE tenant=? AND root=?',(tenant,root)).fetchone()
        if row is None: raise ValueError('unknown tenant/root')
        return json.loads(row[0])

    def apply(self,tenant,event,model,expected_revision):
        # The caller supplies authenticated tenant and an immutable loaded Model, never a client delta.
        request_hash=digest(event)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior=self.db.execute('SELECT request_hash,value FROM receipts WHERE tenant=? AND event_id=?',(tenant,event['id'])).fetchone()
            if prior:
                if prior[0]!=request_hash: raise ValueError('idempotency key reused for different input')
                self.db.execute('COMMIT'); return json.loads(prior[1])
            before=self.read(tenant,event['root'])
            if before['revision']!=expected_revision: raise ValueError('revision conflict')
            result=model.forward(before,event,tenant)
            after=result.pop('graph'); after['revision']=before['revision']+1
            receipt={**result,'revision':after['revision'],'event':event['id'],'state_hash':digest(after)}
            self.db.execute('UPDATE graphs SET revision=?,value=? WHERE tenant=? AND root=?',(after['revision'],json.dumps(after),tenant,event['root']))
            self.db.execute('INSERT INTO receipts VALUES (?,?,?,?)',(tenant,event['id'],request_hash,json.dumps(receipt)))
            self.db.execute('COMMIT'); return receipt
        except BaseException:
            self.db.execute('ROLLBACK'); raise

    def close(self): self.db.close()

def numeric_delta(before,after):
    old={o['id']:o for o in before['objects']}; result=[]
    for obj in after['objects']:
        for field,value in obj['fields'].items():
            if old[obj['id']]['fields'][field]!=value: result.append((obj['id'],field,value))
    return sorted(result)
