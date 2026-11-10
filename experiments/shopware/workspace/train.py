"""Learn Admin UI readouts and additional commerce transitions from live traces."""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import torch

from holodeck.neural import Weights
from holodeck.projection import BINDING_AXES, EVENTS
from holodeck.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
WIDTH = 12
FIELD_ALIASES = {
    'orders': {'Order number':'id','Customer name':'name','Delivery address':'address','Total':'gross',
               'Order status':'stage','Payment status':'payment','Delivery status':'delivery',
               'Tracking codes':'tracking','Tracking number':'tracking','Items':'items'},
    'catalog': {'Name':'name','Product number':'sku','Euro':'price','In stock':'stock'},
}
ORDER_FILTERS = {'Order status':'stage','Payment status':'payment','Delivery status':'delivery'}


def module(name, directory):
    spec = importlib.util.spec_from_file_location(name, directory/'train.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def classify(row):
    return (row['event'],row['before'],row['after'] if row['status']==200 else '<refused>')


def captured_readouts(ui, transitions):
    rows, bindings = [], {}
    observed = {r['event'] for r in transitions if r['split']=='train' and r['status']==200}
    observed.add('order_delivery.track')  # successful Store/Admin traces in neural/shopware.jsonl
    for source, aliases in FIELD_ALIASES.items():
        screens = [sample for sample in ui if sample['source']==source and sample['kind']=='grid']
        if not screens:
            raise ValueError(f'No observed {source} grid')
        evidence = [sample for sample in ui if sample['source']==source and sample['kind']!='grid']
        tools = tuple(dict.fromkeys(sample['kind'] for sample in evidence if sample['kind'] in ('search','sort','filter','expand')))
        if not {'search','sort'} <= set(tools):
            raise ValueError(f'Missing live search or sort evidence for {source}')
        headers = screens[0]['headers']
        fields = list(dict.fromkeys(aliases[h['label']] for h in headers if h['label'] in aliases))
        sorts = [aliases[h['label']] for h in headers if h['label'] in aliases and h['sortable']]
        detail = next((sample for sample in evidence if sample['kind']=='expand'),None)
        details = []
        if detail:
            for label in detail['fields']:
                field = aliases.get(label)
                if field:
                    if field not in fields:
                        fields.append(field)
                    details.append(field)
        menu = next((sample for sample in evidence if sample['kind']=='filter'),{})
        filters = [ORDER_FILTERS[label] for label in menu.get('labels',[]) if label in ORDER_FILTERS]
        transactions = [sample for sample in evidence if sample['kind']=='command']
        if source=='orders' and not transactions:
            raise ValueError('No native Admin mutation in the UI trace')
        # The clicked detail action grounds its binding. The live state matrix supplies
        # other transitions that share the same entity identifier in the typed interface.
        events = [event for event in EVENTS if event in observed] if transactions else []
        for event in events:
            for name in EVENTS[event][1]:
                bindings[(source,event,name)] = ('row.id' if name=='order_id' else 'input',)
            bindings[(source,event,'navigate')] = ('_',)
        group = {'fields':fields,'events':events,'tools':tools,'filters':filters,'sorts':sorts,'details':details}
        if any(len(v)>WIDTH for v in group.values()):
            raise ValueError('Captured widget exceeds its slot budget')
        if not screens[0]['role']=='table':
            raise ValueError('Observed widget is not a table')
        label = ('table',)+tuple(x for axis in BINDING_AXES for x in [*group[axis],*(['_']*(WIDTH-len(group[axis])))])
        rows.append(((source,),label))
    return rows,list(bindings.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    torch.manual_seed(904)
    torch.set_num_threads(1)
    sys.path.insert(0,str(ROOT/'projection'))
    ui_trainer = module('projection_trainer',ROOT/'projection')
    commerce = module('commerce_trainer',ROOT/'neural')
    transitions = json_lines(Path(__file__).with_name('transitions.jsonl'))
    ui = json_lines(Path(__file__).with_name('ui.jsonl'))
    prior = json_lines(ROOT/'neural/shopware.jsonl')
    old, _ = commerce.examples([r for r in prior if r['split']=='train'])
    new = [classify(row) for row in transitions if row['split']=='train']
    samples = [*old,*new]
    test = [classify(row) for row in transitions if row['split']=='test']
    existing_test,_ = commerce.examples([r for r in prior if r['split']=='test'])
    baseline = ROOT.parents[1]/'src/holodeck/deck.npz'
    with np.load(baseline,allow_pickle=False) as archive:
        arrays = {k:archive[k].copy() for k in archive.files if not k.startswith('net.') and k!='schema'}
        meta = json.loads(str(archive['schema']))
    for i,key in enumerate(('events','observations','outputs')):
        meta[key] = sorted({row[i] for row in samples})
    arrays['schema'] = np.array(json.dumps(meta,sort_keys=True))
    arrays.update(commerce.fit_categories(meta,samples))
    readout,wires = captured_readouts(ui,transitions)
    sources = {'layout':[(('1',),('stack',)),(('2',),('split',))], 'readout':readout, 'wire':wires}
    schema = {'version':1,'groups':BINDING_AXES,'width':WIDTH}
    for name, rows in sources.items():
        schema[name], values = ui_trainer.fit(rows,48)
        arrays.update({f'ws.{name}.{key}':value for key,value in values.items()})
    arrays['ws.schema'] = np.array(json.dumps(schema,sort_keys=True))
    np.savez_compressed(args.output,**arrays)
    weights = Weights(args.output)
    workspace = Workspace(weights.values,'test')
    for name,rows in sources.items():
        for key,target in rows:
            if tuple(workspace.predict(name,key))!=target:
                raise ValueError(f'Wrong UI readout: {name} {key}')
    errors = []
    for event,before,after in test+existing_test:
        result = weights.decide(event,before)
        goal = (after!='<refused>',before if after in ('<refused>','<same>') else after)
        if result!=goal:
            errors.append([event,before,goal,result])
    print(json.dumps({'new_observations':dict(Counter(row['split'] for row in transitions)),
                      'new_events':len({row['event'] for row in transitions}),
                      'new_ui_observations':len(ui),'new_held_out':len(test),'original_held_out':len(existing_test),
                      'checkpoint':weights.identity,'errors':errors},indent=2))
    if errors:
        raise SystemExit(1)


if __name__=='__main__':
    main()
