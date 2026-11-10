"""Fit the expanded deck from Shopware order journeys and typed UI supervision."""
import argparse
from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import torch

from holodeck.neural import Weights
from holodeck.projection import BINDING_AXES, EVENTS, SLOTS, TREE_AXES, WIDGETS, Projector

SHOPWARE = Path(__file__).resolve().parents[1]
ROOT = SHOPWARE.parents[1]
WIDTH = 16


def load_module(name, directory):
    spec = importlib.util.spec_from_file_location(name,directory/'train.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(path):
    opener = gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt') as stream:
        return [json.loads(line) for line in stream]


def classify(row):
    return row['event'],row['before'],row['after'] if row['status']==200 else '<refused>'


def collect(data, quantities):
    categories, numbers = [], {}
    def add(name, values, observed):
        numbers.setdefault(name,set()).add((tuple(values),observed))

    for row in data:
        request, observed = row['catalog']['request'],row['catalog']['observed']
        for key in ('price','stock'):
            add('scale.catalog.'+key,(request[key],),observed[key])
        if request['name']!=observed['name']:
            raise ValueError('Shopware did not echo the requested catalog name.')
        categories.append(('catalog.name','text','<argument>'))
        coupon = row['coupon']
        categories.append(('cart.promo',coupon['condition'],'<same>' if coupon['accepted'] else '<refused>'))
        before,after = row['before'],row['after']
        if coupon['accepted']:
            discount = next(i for i in after['lines'] if i['type']=='promotion')
            amount, tax = -discount['total'],-discount['tax']
            values = (before['goods'],coupon['value'])
            if coupon['mode']=='percentage':
                values += (100,)
            add('scale.discount.'+coupon['mode'],values,amount)
            add('scale.discount.tax',(amount,row['rate']),tax)
            add('scale.discount.balance',(before['goods'],amount),after['goods'])
            add('scale.discount.balance',(before['gross'],amount),after['gross'])
            add('scale.discount.balance',(before['tax'],tax),after['tax'])
        categories.extend(classify(event) for event in row['order']['events'])
    for row in quantities:
        add('cart.set',(row['before'],row['requested'],row['stock']),row['after'])
    return categories,numbers


def expanded_lessons(controls):
    from curriculum import item, lessons
    for held,context,raw in lessons():
        nodes = deepcopy(raw)
        for node in nodes:
            if node['source']=='catalog' and context[0]=='admin':
                node['events'] += (controls['Admin']['event'],)
            if node['source']=='orders':
                node['events'] += ('order_delivery.retour_partially','order_transaction.refund_partially')
            if node['source']=='quote':
                node['fields'] = ('discount',*node['fields'])
        if context[0]=='checkout' and context[3]=='ready':
            index = next(i for i,n in enumerate(nodes) if n['kind']=='form')
            nodes.insert(index,item('form',nodes[index]['parent'],title='Rabattcode',
                                    events=(controls['Storefront']['event'],)))
        yield held,context,nodes

def action_wires(event, controls):
    from curriculum import wires
    if event=='catalog.edit':
        fields = {'price' if field=='gross' else field:'input' for field in controls['Admin']['fields']}
        return {'sku':'row.sku','version':'row.version',**fields,'navigate':'_'}
    return wires(event)


def fit_ui(trainer, arrays, controls):
    tree, test = [], []
    labels = {}
    for held,context,nodes in expanded_lessons(controls):
        y = trainer.flatten(nodes,TREE_AXES,SLOTS)
        (test if held else tree).append((context,y))
        if held:
            continue
        for node in nodes:
            if node['kind'] not in WIDGETS:
                continue
            key = (node['kind'],node['source'],node['title'],*context[:3])
            value = tuple(x for axis in BINDING_AXES for x in [*node[axis],*(['_']*(WIDTH-len(node[axis])))])
            if labels.setdefault(key,value)!=value:
                raise ValueError('Incompatible UI lesson')
    arguments = {}
    for key, value in labels.items():
        events = value[WIDTH:WIDTH*2]
        for event in events:
            if event in EVENTS:
                for param, pointer in action_wires(event,controls).items():
                    arguments[(event,key[1],param)] = (pointer,)
    schema = {'version':3,'width':WIDTH}
    for name,data in (('tree',tree),('bindings',list(labels.items())),('argument',list(arguments.items()))):
        schema[name], values = trainer.fit(data,72 if name=='tree' else 64,
            trainer.invariant_features(tree) if name=='tree' else None)
        arrays.update({f'ui.{name}.{k}':v for k,v in values.items()})
    arrays['ui.schema'] = np.array(json.dumps(schema,sort_keys=True))
    projector = Projector(arrays)
    for held,context,expected in expanded_lessons(controls):
        actual = projector.decode(context)
        for a,b in zip(actual,expected,strict=True):
            if any(a[k]!=b[k] for k in TREE_AXES):
                raise ValueError(f'Wrong tree: {context}')
            if a['kind'] in WIDGETS:
                if any(a[k]!=list(b[k]) for k in BINDING_AXES):
                    raise ValueError(f'Wrong binding: {context} {a}')
                if any(a['wires'][event]!=action_wires(event,controls) for event in a['events'] if event in EVENTS):
                    raise ValueError(f'Wrong wire: {context} {a}')
    return {'train':len(tree),'test':len(test),'binding_contexts':len(labels)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    torch.manual_seed(173)
    sys.path.insert(0,str(SHOPWARE/'projection'))
    commerce = load_module('scale_commerce_train',SHOPWARE/'neural')
    ui = load_module('scale_projection_train',SHOPWARE/'projection')
    data = sorted(rows(args.trace),key=lambda row:row['case'])
    train = [r for r in data if r['split']=='train']
    test = [r for r in data if r['split']=='test']
    quantities = rows(Path(__file__).with_name('quantities.jsonl'))
    prior,_ = commerce.examples([r for r in rows(SHOPWARE/'neural/shopware.jsonl') if r['split']=='train'])
    matrix = [classify(r) for r in rows(SHOPWARE/'workspace/transitions.jsonl') if r['split']=='train']
    observations,numbers = collect(train,[r for r in quantities if r['split']=='train'])
    samples = [*prior,*matrix,*observations]
    with np.load(ROOT/'src/holodeck/workspace.npz',allow_pickle=False) as archive:
        arrays = {k:archive[k].copy() for k in archive.files if not k.startswith(('net.','ui.')) and k!='schema'}
        meta = json.loads(str(archive['schema']))
    meta['arities'].update({name:len(next(iter(values))[0]) for name,values in numbers.items()})
    for i,key in enumerate(('events','observations','outputs')):
        meta[key] = sorted({row[i] for row in samples})
    arrays['schema'] = np.array(json.dumps(meta,sort_keys=True))
    arrays.update(commerce.fit_categories(meta,samples))
    arrays.update({'int.'+k:commerce.fit_integer(k,points) for k,points in sorted(numbers.items())})
    torch.manual_seed(612)
    capture = rows(Path(__file__).with_name('ui.jsonl'))
    targets = {'Admin':'catalog.edit','Storefront':'cart.promo'}
    controls = {r['surface']:r for r in capture
                if 200<=r['status']<400 and r['verified'] and targets.get(r['surface'])==r['event']}
    if set(controls)!=set(targets) or set(controls['Admin']['fields'])!={'name','gross','stock'}:
        raise ValueError('Missing browser evidence for the new control lessons')
    ui_stats = {**fit_ui(ui,arrays,controls),'native_observations':len(capture)}
    np.savez_compressed(args.output,**arrays)
    fitted = Weights(args.output)
    decisions,integers = collect(test,[r for r in quantities if r['split']=='test'])
    for event,before,after in decisions:
        goal = (after!='<refused>',before if after in ('<refused>','<same>') else after)
        if fitted.decide(event,before)!=goal:
            raise ValueError((event,before,goal,fitted.decide(event,before)))
    for name, examples in integers.items():
        for x,y in examples:
            if fitted.integer(name,*x)!=y:
                raise ValueError((name,x,y,fitted.integer(name,*x)))
    print(json.dumps({'train':len(train),'test':len(test),'fit_heads':len(numbers),
                      'ui':ui_stats,'test_decisions':len(decisions),
                      'test_numeric_labels':sum(map(len,integers.values())),
                      'checkpoint':fitted.identity},indent=2))


if __name__=='__main__':
    main()
