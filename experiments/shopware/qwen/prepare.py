"""Create completion supervision from reference outcomes and observed UI controls."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

from holodeck.neural import arithmetic_pool, evaluate
from contract import ADMIN_REQUESTS, CHECKOUT_REQUESTS, compact, pricing_prompt, prompt

SHOPWARE = Path(__file__).resolve().parents[1]
REGISTERS = ('d','x','a','c','j','n')
INPUTS = ('g','b','t','r','v')


def observations(row):
    before,after,policy = row['before'],row['after'],row['coupon']
    line = next((item for item in after['lines'] if item['type']=='promotion'),None)
    mode = policy['mode'] if line else 'none'
    return (mode, (before['goods'],before['gross'],before['tax'],row['rate'],policy['value'] if line else 0),
            (-line['total'] if line else 0,-line['tax'] if line else 0,
             after['goods'],after['gross'],after['tax'],after['net']))


def expression(op, inputs):
    code, i, *rest = op
    values = (*inputs,0,1,100)
    if code==0:
        return values[i]
    a,b = values[i],values[rest[0]]
    if code in (4,5):
        divisor = values[rest[1]]
        return ['muldiv',a,b,['add',100,divisor] if code==5 else divisor]
    return [{1:'add',2:'sub',3:'mul',6:'min',7:'max'}[code],a,b]


def fit_laws(rows):
    laws = {}
    for mode in ('none','percentage','absolute'):
        samples = [(x,y) for row in rows for m,x,y in [observations(row)] if mode==m]
        if not samples:
            raise ValueError('Missing teacher coverage for '+mode)
        instructions = []
        for index, names in enumerate((('g','v'),('d','r'),('g','d'),('b','d'),('t','x'),('c','j'))):
            input_names = (*INPUTS,*REGISTERS[:index])
            points = [tuple(dict(zip(input_names,(*x,*y[:index]),strict=True))[name] for name in names)
                      for x,y in samples]
            # Include a typed constant register as a possible divisor.
            candidates = sorted((op for op in arithmetic_pool(3) if op[0]<8),
                                key=lambda op:(len(op)+(op[0]==5),op))
            best = next(op for op in candidates if all(evaluate(op,(*p,100))==y[index]
                                                       for p,(_,y) in zip(points,samples,strict=True)))
            instructions.append(expression(best,(*names,100)))
        laws[mode] = instructions
    return laws


def component(kind, source, fields=(), **attrs):
    return {'kind':kind,'source':source,'fields':list(fields),**attrs}


def checkout_ui():
    return [
        component('stats','quote',('goods','discount','shipping','gross'),title='Preisübersicht'),
        component('list','cart',('name','quantity','unit','gross'),title='Artikel im Warenkorb'),
        component('form','_',title='Aktionscode',events=['cart.promo'],wires={'cart.promo':{'code':'input'}}),
        component('form','_',title='Bestellung abschließen',events=['checkout.place'],
                  wires={'checkout.place':{'quote_key':'context.quote',**{k:'input' for k in ('name','email','address','consent')}}}),
    ]


def admin_ui(reference, filtered):
    mapping={'Order number':'id','Customer name':'name','Total':'gross','Order status':'stage',
             'Payment status':'payment','Delivery status':'delivery'}
    grid=next(r for r in reference if r['kind']=='grid')
    sort=next(r for r in reference if r['kind']=='sort')
    event=next(r['event'] for r in reference if r['kind']=='command' and r['status']==200)
    fields=[mapping[c['label']] for c in grid['headers'] if c['label'] in mapping]
    fields+=['address','items']
    return {
        'ui':[component('table','orders',fields,title='Versandübersicht' if filtered else 'Bestellungen',
              events=[event],wires={event:{'order_id':'row.id'}},tools=['search','sort','filter','expand'],
              filters=['payment','delivery'],sorts=[mapping[sort['label']]],details=['address','items'])],
        'where':{'payment':'paid','delivery':'open'} if filtered else {},
        **({'sort':['gross','desc']} if filtered else {}),
    }


def main():
    cli=argparse.ArgumentParser()
    cli.add_argument('output',type=Path)
    cli.add_argument('--refine-pricing',action='store_true')
    args=cli.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with gzip.open(SHOPWARE/'scale/journeys.jsonl.gz','rt') as f:
        rows=[r for line in f if (r:=json.loads(line))['split']=='train']
    native=[r for line in (SHOPWARE/'workspace/ui.jsonl').read_text().splitlines()
            if (r:=json.loads(line))['source']=='orders']
    promo=[r for line in (SHOPWARE/'scale/ui.jsonl').read_text().splitlines()
           if (r:=json.loads(line))['event']=='cart.promo' and r['verified']]
    if not promo:
        raise ValueError('Missing native coupon interaction')
    dev=[r for r in rows if r['case']%19==0]
    train=[r for r in rows if r['case']%19!=0]
    laws=fit_laws(train)
    for partition,data in (('train',train),('valid',dev)):
        output=[]
        for row in data:
            mode,x,_=observations(row)
            ctx=dict(zip(INPUTS,x,strict=True))
            ctx['mode']=mode
            request=CHECKOUT_REQUESTS[row['case']%len(CHECKOUT_REQUESTS)]
            output.append({'prompt':prompt('checkout',request,ctx),
                           'completion':compact({'calc':laws[mode],'ui':checkout_ui()})})
            if args.refine_pricing:
                output.append({'prompt':pricing_prompt(ctx),'completion':compact({'calc':laws[mode]})})
        # UI targets are semantic annotations from the native trace, paired with
        # the pre-states of distinct real orders from the API corpus.
        for i,row in enumerate(data[::2]):
            request=ADMIN_REQUESTS[i%len(ADMIN_REQUESTS)]
            ctx={'order_count':1,'sample':[{'payment':row['order']['events'][0]['after'],
                     'delivery':row['order']['events'][1]['before'],'gross':row['order']['gross']}]}
            output.append({'prompt':prompt('admin',request,ctx),
                           'completion':compact(admin_ui(native,i%len(ADMIN_REQUESTS)<2))})
        (args.output/(partition+'.jsonl')).write_text(''.join(compact(row)+'\n' for row in output))
        print(partition,len(output),Counter(observations(row)[0] for row in data))
    print('Trace-induced programs',compact(laws))
    print('Native admin observations',len(native),'native coupon interactions',len(promo))


if __name__=='__main__':
    main()
