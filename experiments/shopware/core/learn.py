"""Data-driven expression induction and differentiable discrete program selection.

No commerce formulas or event handlers are provided to the learner. The schema
supplies types, observed graph deltas supply targets. Generic candidates are
proposed, then categorical weight heads learn from their per-example outcomes.
This is an inductive program model, not a fine-tuned foundation model.
"""
from __future__ import annotations
import copy, hashlib, json, time
from pathlib import Path
import numpy as np
import torch
from vm import HERE, VOCAB_HASH, evaluate, expression_text, root_of

SCHEMA=json.loads((HERE/'schema.json').read_text())
torch.set_num_threads(2)

def complexity(e): return 1+sum(complexity(x) for x in e[1:] if isinstance(x,list))

def features(rows,kind):
    specs=[]
    for scope, schema in [('self',SCHEMA[kind]),('root',SCHEMA['root']),('config',SCHEMA['config']),('event',SCHEMA['event'])]:
        for field, dtype in schema.items(): specs.append((dtype,['read',scope,field]))
    for child,schema in SCHEMA.items():
        if child in ('config','event','root'): continue
        for field,dtype in schema.items():
            if dtype!='enum': specs.append((dtype,['sum',child,field]))
    result=[]; seen=set()
    for dtype,e in specs:
        try: v=np.array([evaluate(e,o,g,event) for g,event,o in rows],dtype=np.int64)
        except (KeyError, ValueError): continue
        signature=(dtype,v.tobytes())
        if signature in seen: continue
        seen.add(signature); result.append((dtype,e,v))
    for dtype in ['money','scalar','time','enum']:
        for number in ([-1,0,1,2] if dtype=='enum' else [0,1,100]):
            v=np.full(len(rows),number,dtype=np.int64)
            if (dtype,v.tobytes()) not in seen: result.append((dtype,['const',number],v))
    return result

def propose(rows,kind,field,y):
    """Bounded universal grammar search. Returns candidates, not target programs."""
    dtype=SCHEMA[kind][field]; fs=features(rows,kind)
    same=[(e,v) for t,e,v in fs if t==dtype]
    scalar=[(e,v) for t,e,v in fs if t=='scalar']
    exact=[]; near=[]; seen=set()
    def offer(e,v):
        if np.any(np.abs(v)>10**13): return
        key=json.dumps(e,separators=(',',':'))
        if key in seen: return
        seen.add(key)
        match=v==y
        item=(e,v)
        if match.all(): exact.append(item)
        elif len(near)<40 or match.any():
            near.append(item)
            if len(near)>100: near.sort(key=lambda x:(-int(np.sum(x[1]==y)),complexity(x[0]))); del near[50:]
    for e,v in same: offer(e,v)
    # Every pair uses identical rules, independent of field or event names.
    for a,av in same:
        for b,bv in same:
            for op,val in [('add',av+bv),('sub',av-bv),('min',np.minimum(av,bv)),('max',np.maximum(av,bv))]:
                offer([op,a,b],val)
    if dtype=='money':
        amounts=list(same)
        for a,av in same:
            for b,bv in scalar:
                e=['mul',a,b]; v=av*bv; offer(e,v)
                if b[0]!='const' and a[0]!='const': amounts.append((e,v))
        denominators=list(scalar)
        for e,v in scalar:
            if e[0]!='const': denominators.append((['add',['const',100],e],100+v))
        for a,av in amounts:
            for b,bv in scalar:
                if np.any(np.abs(av)>10**9) or np.any(np.abs(bv)>10**5): continue
                numerator=av*bv
                for c,cv in denominators:
                    if np.any(cv==0): continue
                    # Exact integer HALF_UP, matching the interpreter for these nonnegative fixtures.
                    v=(np.abs(numerator)*2+np.abs(cv))//(2*np.abs(cv))*np.sign(numerator)*np.sign(cv)
                    e=['muldiv',a,b,c]; offer(e,v)
                    # Generic min/max composition; retain only when the target can be covered.
                    if np.all(v>=y) or np.all(v<=y):
                        for cap,capv in same:
                            if np.all(v>=y) and np.all(capv>=y) and np.all((v==y)|(capv==y)):
                                offer(['min',e,cap],np.minimum(v,capv))
                            if np.all(v<=y) and np.all(capv<=y) and np.all((v==y)|(capv==y)):
                                offer(['max',e,cap],np.maximum(v,capv))
    if not exact: return None
    exact.sort(key=lambda item:(complexity(item[0]),len(json.dumps(item[0]))))
    # Several observationally equivalent candidates stay visible as an identifiability warning.
    pool=exact[:12]+sorted(near,key=lambda x:(-int(np.sum(x[1]==y)),complexity(x[0])))[:20]
    # Candidate position is independent of correctness. Zero weights must not decode
    # the answer merely because synthesis placed every exact candidate first.
    pool.sort(key=lambda item:hashlib.sha256(json.dumps(item[0]).encode()).hexdigest())
    return pool,len(exact)

def fit_head(candidates,y):
    outputs=torch.from_numpy(np.stack([v for e,v in candidates],axis=0))
    matches=outputs==torch.from_numpy(y)[None,:]
    costs=torch.tensor([complexity(e) for e,v in candidates],dtype=torch.float32)
    torch.manual_seed(37)
    logits=torch.nn.Parameter(-costs*0.15+torch.randn(len(candidates))*0.001)
    optimizer=torch.optim.Adam([logits],lr=0.09)
    for _ in range(180):
        logp=logits.log_softmax(0)
        likelihood=torch.logsumexp(logp[:,None].expand_as(matches).masked_fill(~matches,float('-inf')),dim=0)
        loss=-likelihood.mean()+0.008*(logp.exp()*costs).sum()
        optimizer.zero_grad(); loss.backward(); optimizer.step()
    values=logits.detach().numpy()
    if not np.all(candidates[int(values.argmax())][1]==y): raise RuntimeError('learner did not fit observed deltas')
    return values,float(loss.detach())

def train(events,destination,base=None):
    start=time.monotonic()
    if base:
        from vm import Model
        prior=Model(base); meta=copy.deepcopy(prior.meta); weights=copy.deepcopy(prior.weights)
    else:
        meta={'vocabulary_sha256':VOCAB_HASH,'family':'induced exact dataflow / differentiable selection','plans':{},'heads':{},'training':[]}
        weights={}
    records=[]
    for name in dict.fromkeys(e['event']['kind'] for e in events):
        examples=[copy.deepcopy(e) for e in events if e['event']['kind']==name]
        if base:
            for ex in examples: ex['before']=prior.forward(ex['before'],ex['event'],ex['before']['tenant'])['graph']
        meta['plans'].setdefault(name,[])
        pending=set()
        for ex in examples:
            byid={o['id']:o for o in ex['after']['objects']}
            for obj in ex['before']['objects']:
                for field,value in obj['fields'].items():
                    if value!=byid[obj['id']]['fields'][field]: pending.add((obj['kind'],field))
        while pending:
            solved=[]
            for kind,field in sorted(pending):
                rows=[]; labels=[]
                for ex in examples:
                    byid={o['id']:o for o in ex['after']['objects']}
                    for obj in ex['before']['objects']:
                        if obj['kind']==kind:
                            rows.append((ex['before'],ex['event'],obj)); labels.append(byid[obj['id']]['fields'][field])
                y=np.array(labels,dtype=np.int64)
                proposal=propose(rows,kind,field,y)
                if proposal:
                    candidates,ambiguity=proposal
                    shortest=min(complexity(e) for e,v in candidates if np.all(v==y))
                    solved.append((shortest,kind,field,rows,y,candidates,ambiguity))
            if not solved: raise RuntimeError('grammar cannot explain remaining deltas: '+repr(pending))
            _,kind,field,rows,y,candidates,ambiguity=min(solved,key=lambda x:(x[0],x[1],x[2]))
            values,loss=fit_head(candidates,y)
            key='h'+str(len(meta['heads']))
            meta['heads'][key]={'kind':kind,'field':field,'candidates':[e for e,v in candidates]}
            weights[key]=values; meta['plans'][name].append(key)
            expr=candidates[int(values.argmax())][0]
            for graph,event,obj in rows: obj['fields'][field]=evaluate(expr,obj,graph,event)
            pending.remove((kind,field))
            record={'event':name,'kind':kind,'field':field,'examples':len(rows),'loss':loss,'consistent_candidates':ambiguity,'expression':expression_text(expr)}
            records.append(record); print(json.dumps(record,ensure_ascii=False),flush=True)
    meta['training'].append({'examples':len(events),'heads_added':len(records),'seconds':round(time.monotonic()-start,3),'records':records})
    destination=Path(destination); destination.parent.mkdir(exist_ok=True)
    destination.with_suffix('.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    np.savez(destination.with_suffix('.npz'),**weights)
    return meta

if __name__=='__main__':
    cases=json.loads((HERE/'data/shopware.json').read_text())
    train([e for c in cases if c['split']=='train' for e in c['events']],HERE/'models/base')
