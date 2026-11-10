"""Fit the standalone neural checkpoint from recorded Shopware responses."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from holodeck.neural import Weights, arithmetic_pool, evaluate, features

FIELDS = {'order':0,'order_transaction':1,'order_delivery':2}


def examples(journeys):
    categories = []
    numbers = defaultdict(set)

    def scalar(head, registers, label):
        numbers[head].add((tuple(registers),label))

    def category(event, before, after, ok):
        categories.append((event,before,after if ok else '<refused>'))

    for journey in journeys:
        products = journey['products']
        shipping = journey['shipping']
        for row in journey['events']:
            event = row['event']
            entity = event.split('.')[0]
            before,after = row['before'],row['after']
            ok = row['status'] == 200
            if event == 'order_delivery.track':
                if ok and after not in (before,row['arg']):
                    raise ValueError('Text write has no observed source')
                category(event,'present' if before else 'empty',
                         '<argument>' if after == row['arg'] else '<same>',ok)
                continue
            if entity in FIELDS:
                target = FIELDS[entity]
                if any(a!=b for i,(a,b) in enumerate(zip(before,after)) if i!=target):
                    raise ValueError('Oracle wrote outside the declared record')
                category(event,before[target],after[target],ok)
                continue
            for snapshot in (before,after) if products[0]['rate'] in (7,19) else ():
                lines = snapshot['lines']
                goods,ship,gross,tax,net = snapshot['totals']
                count = sum(q for q,_,_ in lines)
                for (q,line_gross,line_tax),item in zip(lines,products,strict=True):
                    values = [item['price'],q,item['rate']]
                    scalar('line_gross',values,line_gross)
                    scalar('line_tax',[line_gross,item['rate']],line_tax)
                scalar('line_tax',[ship,products[0]['rate']],snapshot['shipping_tax'])
                scalar('shipping.present' if count else 'shipping.empty', [shipping,count],ship)
                registers = [sum(x[1] for x in lines),sum(x[2] for x in lines),ship,snapshot['shipping_tax']]
                scalar('goods',registers,goods)
                scalar('gross',registers,gross)
                scalar('tax',registers,tax)
                scalar('net',[gross,tax],net)
            if entity == 'cart':
                slot = row['slot']
                previous = before['lines'][slot][0]
                category(event,'present' if previous else 'empty','<same>',ok)
                if ok:
                    registers = [previous,products[slot]['stock']]
                    if event != 'cart.remove':
                        registers.insert(1,row['arg'])
                    scalar(event,registers,after['lines'][slot][0])
            elif event == 'checkout.place':
                category(event,'present' if any(x[0] for x in before['lines']) else 'empty','<same>',ok)
                if ok:
                    if row['order']['totals'] != before['totals']:
                        raise ValueError('Oracle order totals differ from the quote')
                    for field in ('stage','payment','delivery'):
                        category('create.'+field,'empty',row['order'][field],True)
                    for i,(old,new) in enumerate(zip(row['stock_before'],row['stock_after'],strict=True)):
                        count = before['lines'][i][0]
                        scalar('checkout.stock',[old,count],new)
                        scalar('checkout.cart',[count],after['lines'][i][0])
    return categories,numbers


def fit_categories(meta, samples):
    mapping = {}
    for event,before,after in samples:
        old = mapping.setdefault((event,before),after)
        if old != after:
            raise ValueError(f'Contradictory observations {event} {before}: {old}, {after}')
    keys = list(mapping)
    x = torch.from_numpy(np.stack([features(meta,*key) for key in keys]))
    y = torch.tensor([meta['outputs'].index(mapping[key]) for key in keys])
    model = nn.Sequential(nn.Linear(x.shape[-1],64),nn.ReLU(),nn.Linear(64,64),nn.ReLU(),nn.Linear(64,len(meta['outputs'])))
    optimizer = torch.optim.AdamW(model.parameters(),lr=0.015,weight_decay=0.001)
    for _ in range(480):
        loss = nn.functional.cross_entropy(model(x),y)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    return {'net.'+key:value.detach().numpy() for key,value in model.state_dict().items()}


def fit_integer(name, rows):
    rows = sorted(rows)
    pool = arithmetic_pool(len(rows[0][0]))
    matches = torch.tensor([[evaluate(program, x)==y for x,y in rows] for program in pool])
    supported = matches.all(dim=1)
    if not supported.any():
        errors = (~matches).sum(dim=1)
        program = pool[int(errors.argmin())]
        examples = [(x,y,evaluate(program,x)) for x,y in rows if evaluate(program,x)!=y]
        raise ValueError(f'No exact arithmetic for {name}, {len(examples)}/{len(rows)} errors: {examples[:5]}')
    cost = torch.tensor([len(op) + (op[0]==5) for op in pool],dtype=torch.float32)
    # Favor the smallest expression when the same observations admit equivalent arithmetic.
    logits = nn.Parameter(-cost * 0.15 + torch.randn(len(pool))*0.001)
    optimizer = torch.optim.Adam([logits],lr=0.15)
    for _ in range(150):
        logp = logits.log_softmax(dim=0)
        likelihood = torch.logsumexp(logp[:,None].masked_fill(~matches,-torch.inf),dim=0)
        loss = -likelihood.mean() + 0.04 * (logp.exp()*cost).sum()
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    if not supported[int(logits.argmax())]:
        raise ValueError(f'Integer head did not converge: {name}')
    return logits.detach().numpy()


def score(weights, data):
    categories,numbers = examples(data)
    errors = []
    for event,before,after in categories:
        ok, value = weights.decide(event,before)
        expected = (after!='<refused>', before if after in ('<same>','<refused>') else after)
        if (ok,value) != expected:
            errors.append((event,before,expected,(ok,value)))
    for key,rows in numbers.items():
        for x,y in sorted(rows):
            if (actual:=weights.integer(key,*x)) != y:
                errors.append((key,x,y,actual))
    return {'decisions':len(categories),'integer_rows':sum(map(len,numbers.values())), 'errors':errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    torch.manual_seed(73)
    torch.set_num_threads(1)
    cases = [json.loads(line) for path in args.trace for line in path.read_text().splitlines()]
    train = [c for c in cases if c['split']=='train']
    test = [c for c in cases if c['split']=='test']
    categories,numbers = examples(train)
    meta = {'format':2, 'arities':{name:len(next(iter(rows))[0]) for name,rows in numbers.items()},
            'events':sorted({r[0] for r in categories}),
            'observations':sorted({r[1] for r in categories}), 'outputs':sorted({r[2] for r in categories})}
    weights = fit_categories(meta,categories)
    weights.update({'int.'+name:fit_integer(name,rows) for name,rows in sorted(numbers.items())})
    np.savez_compressed(args.output,schema=json.dumps(meta,sort_keys=True),**weights)
    fitted = Weights(args.output)
    report = {'checkpoint':fitted.identity,'parameters':sum(x.size for x in weights.values()),
              'journeys':dict(Counter(row['split'] for row in cases)),
              'observations':dict(Counter(row['split'] for row in cases for _ in row['events'])),
              'train':score(fitted,train),'test':score(fitted,test)}
    print(json.dumps(report,indent=2))
    if report['train']['errors'] or report['test']['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
