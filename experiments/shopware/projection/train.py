"""Train the UI tree, semantic field and argument binding heads."""
import argparse
from itertools import combinations
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from curriculum import lessons, wires
from holodeck.projection import BINDING_AXES, EVENTS, SLOTS, TREE_AXES, WIDTH, WIDGETS, Projector, encode_features


def flatten(nodes, axes, size):
    return tuple(node.get(axis, '_') for node in [*nodes, *({} for _ in range(size-len(nodes)))] for axis in axes)


def binding_labels(node):
    return tuple(value for key in BINDING_AXES for value in [*node[key], *('_' for _ in range(WIDTH-len(node[key])))])


def datasets():
    train, test = [], []
    bindings, arguments = {}, {}
    for held, context, nodes in lessons():
        (test if held else train).append((context, flatten(nodes, TREE_AXES, SLOTS)))
        if held:
            continue
        for node in nodes:
            if node['kind'] not in WIDGETS:
                continue
            key = (node['kind'], node["source"], node["title"], *context[:3])
            label = binding_labels(node)
            if bindings.setdefault(key, label) != label:
                raise ValueError(f'Conflicting UI labels for {key}')
            for event in node['events']:
                if event in EVENTS:
                    for param, pointer in wires(event).items():
                        arguments[(event,node['source'],param)] = (pointer,)
    return {'tree': train, 'bindings': list(bindings.items()), 'argument': list(arguments.items())}, test


def mask_context(keys, invariant, position):
    mask = invariant[keys[0]][position] if invariant else ()
    return tuple(None if i in mask else key for i,key in enumerate(keys))


def fit(rows, width, invariant=None):
    spec = {'inputs': [sorted(set(x)) for x in zip(*(x for x, _ in rows), strict=True)],
            'outputs': [sorted(set(x), key=str) for x in zip(*(y for _, y in rows), strict=True)]}
    partial = {}
    for keys, labels in rows:
        for index, label in enumerate(labels):
            view = mask_context(keys,invariant,index)
            partial.setdefault(view,{})[index] = label
    keys = list(partial)
    x = torch.from_numpy(np.stack([encode_features(spec['inputs'], key) for key in keys]))
    labels = {}
    for i, values in enumerate(spec['outputs']):
        if len(values) > 1:
            available = [j for j, key in enumerate(keys) if i in partial[key]]
            labels[i] = (available, torch.tensor([values.index(partial[keys[j]][i]) for j in available]))
    model = nn.Sequential(nn.Linear(x.shape[-1],width),nn.ReLU(),nn.Linear(width,width),
                          nn.ReLU(),nn.Linear(width,sum(map(len,spec['outputs']))))
    optimizer = torch.optim.AdamW(model.parameters(),lr=0.012,weight_decay=0.001)
    for epoch in range(1200):
        scores = model(x).split([len(v) for v in spec['outputs']],dim=1)
        loss = torch.stack([nn.functional.cross_entropy(scores[i][available],y)
                            for i,(available,y) in labels.items()]).mean()
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if epoch >= 200 and loss.item() < 0.002:
            break
    if invariant:
        spec['invariant'] = invariant
    return spec, {k:v.detach().numpy() for k,v in model.state_dict().items()}


def invariant_features(rows):
    """Infer which context coordinates can be masked for each output attribute."""
    masks = {}
    for task in sorted({key[0] for key,_ in rows}):
        group = [(key,y) for key,y in rows if key[0]==task]
        pairs = [[] for _ in group[0][0]]
        for (x,y),(z,w) in combinations(group,2):
            difference = [i for i,(a,b) in enumerate(zip(x,z,strict=True)) if a!=b]
            if len(difference)==1:
                pairs[difference[0]].append((y,w))
        per_output = []
        for pos in range(len(group[0][1])):
            mask = []
            for axis in range(1,len(pairs)):
                if not pairs[axis] or any(a[pos]!=b[pos] for a,b in pairs[axis]):
                    continue
                trial = [*mask,axis]
                seen = {}
                for x,y in group:
                    reduced = tuple(None if i in trial else key for i,key in enumerate(x))
                    if seen.setdefault(reduced,y[pos]) != y[pos]:
                        break
                else:
                    mask = trial
            per_output.append(mask)
        masks[task] = per_output
    return masks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commerce', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    torch.manual_seed(612)
    data, test = datasets()
    schema, arrays = {'version':3}, {}
    for head, rows in data.items():
        schema[head], weights = fit(rows, 72 if head == 'tree' else 64, invariant_features(rows) if head=='tree' else None)
        arrays.update({f'ui.{head}.{k}': v for k,v in weights.items()})
    arrays['ui.schema'] = np.array(json.dumps(schema,sort_keys=True))
    decoder = Projector(arrays)
    checks = {}
    for name, head, rows in [('tree_train','tree',data['tree']), ('tree_held_out','tree',test),
                             ('field_bindings','bindings',data['bindings']), ('argument_bindings','argument',data['argument'])]:
        errors = []
        for x,y in rows:
            predicted = decoder.predict(head,x)
            if tuple(predicted) != y:
                errors.append({'input':x,'differences':[[i,a,b] for i,(a,b) in enumerate(zip(predicted,y,strict=True)) if a!=b]})
        checks[name] = {'matched':len(rows)-len(errors),'total':len(rows),'errors':errors}
    novel = len({y for _,y in test} - {y for _,y in data['tree']})
    known_bindings = {x for x,_ in data['bindings']}
    new_bindings = set()
    errors = []
    for held, context, nodes in lessons():
        if not held:
            continue
        try:
            predicted = decoder.decode(context)
            for actual, expected in zip(predicted,nodes,strict=True):
                if any(actual[axis] != expected[axis] for axis in TREE_AXES):
                    raise ValueError('Wrong tree attribute')
                if actual['kind'] not in WIDGETS:
                    continue
                key = (actual['kind'], actual['source'], actual['title'], *context[:3])
                if key not in known_bindings:
                    new_bindings.add(key)
                if any(actual[axis] != list(expected[axis]) for axis in BINDING_AXES):
                    raise ValueError('Wrong field or event binding')
                for event in expected['events']:
                    if event in EVENTS and actual['wires'][event] != wires(event):
                        raise ValueError('Wrong action parameter binding')
        except (ValueError,KeyError) as error:
            errors.append({'input':context,'error':str(error)})
    checks['program_held_out'] = {'matched':len(test)-len(errors),'total':len(test),'errors':errors}
    print(json.dumps({'unseen_annotated_trees':novel,'unseen_binding_contexts':len(new_bindings),
                      'ui_parameters':sum(v.size for k,v in arrays.items() if k!='ui.schema'),
                      'checks':checks},indent=2))
    if any(check['errors'] for check in checks.values()):
        raise SystemExit(1)
    with np.load(args.commerce,allow_pickle=False) as archive:
        np.savez_compressed(args.output,**archive,**arrays)


if __name__ == '__main__':
    main()
