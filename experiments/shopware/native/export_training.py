"""Export real teacher transitions with frozen programs and generated UI examples.

The existing held-out merchant remains evaluation-only. UI projection records
are composition examples, not labels for commercial correctness.
"""
import json
from collections import Counter
from common import ROOT,BASE
from native import NativeModel
from service import Commerce
from packages import install_demo_packages

def main():
    folder=ROOT/'training';folder.mkdir(exist_ok=True);rows=[];counts=Counter()
    cases=json.loads((BASE/'data/shopware.json').read_text())
    for case in cases:
        model=NativeModel(case['config'],checkpoint=BASE/'models/base')
        for step in case['events']:
            result=model.forward(step['before'],step['event'],step['before']['tenant'])
            if result['graph']!=step['after']:raise ValueError('native output differs from teacher: '+case['id'])
            rows.append({'case':case['id'],'split':case['split'],'source':'existing Shopware oracle corpus; not newly collected',
                'state':step['before'],'event':step['event'],'configuration':case['config'],'target_state':step['after'],
                'program':result['program'],'native_writes':result['writes'],'checkpoint':model.identity})
            counts[case['split']]+=1
    for split in counts:
        with (folder/(split+'.jsonl')).open('w') as f:
            for row in rows:
                if row['split']==split:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    service=Commerce(install_demo_packages());ui=[]
    for merchant in service.repo.tenants():
        for role in ['shopper','merchant']:
            value=service.bound_view(merchant,role);ui.append({'task':'ui_projection','merchant':merchant,'role':role,
                'style':value['merchant']['style'],'bindings':value['bindings'],'target_component_tree':value['view'],
                'source':'locally generated Qwen composition; copy reviewed; live synthetic local records','commercial_correctness_label':False})
    (folder/'ui-projections.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in ui))
    report={'behavior_records':dict(counts),'ui_records':len(ui),'native_exact':len(rows),'native_total':len(rows),
        'warning':'Evaluation records must never enter training. UI examples do not prove arbitrary layout generation or behavior parity.'}
    (ROOT/'results').mkdir(exist_ok=True)
    (ROOT/'results/training-export.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':main()
