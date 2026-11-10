"""Reproducible source-only fitting, blind target evaluation, restart and baseline checks."""
from __future__ import annotations
import argparse,copy,hashlib,json,random,subprocess,sys,tempfile,time
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
from vm import HERE,Model,Store,digest,evaluate,expression_text,numeric_delta,root_of,retrieve

def host_fingerprint():
    return {name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ['vm.py','vocabulary.json','schema.json','worker.py']}

def intended_credit(graph):
    # Independent evaluation oracle for the merchant's stated semantics. Never used by train().
    value=(Decimal(root_of(graph)['fields']['goods'])*Decimal(graph['config']['credit_rate'])/100).quantize(Decimal(1),rounding=ROUND_HALF_UP)
    return min(int(value),graph['config']['credit_cap'])

def novel_examples(cases):
    # Independent literal teaching labels, reviewed against the merchant's words.
    # No LLM expression or evaluation formula is called to produce these labels.
    # Deliberately cover uncapped, capped and rounded output. Not random sampling.
    fixtures=[(10001,3,700,300),(40000,3,700,700),(19199,5,1100,960)]
    examples=[]
    for case,(goods,rate,cap,credit) in zip(cases,fixtures):
        ex=copy.deepcopy(case['events'][-1])
        for side in ['before','after']:
            root_of(ex[side])['fields']['goods']=goods
            ex[side]['config'].update(credit_rate=rate,credit_cap=cap)
        root=root_of(ex['after'])
        root['fields']['credit']=credit
        ex['source']='Independent, explicitly chosen synthetic merchant examples with literal labels; neither Shopware nor Qwen labels'
        examples.append(ex)
    return examples

def compare(model,cases,novel=False,restart=False):
    totals={'trajectories':len(cases),'exact_trajectories':0,'steps':0,'exact_steps':0,'retrieval_errors':0,
            'operand_or_computation_errors':0,'failures':[],'processes':0,'checkpoint_switches':0}
    samples=[]
    with tempfile.TemporaryDirectory(prefix='shopvm-eval-') as temporary:
        database=str(Path(temporary)/'state.sqlite')
        for case in cases:
            graph=copy.deepcopy(case['events'][0]['before']); all_correct=True; previous=None
            if restart:
                store=Store(database); store.seed(graph); store.close()
            for index,ex in enumerate(case['events']):
                event=ex['event']; before=copy.deepcopy(graph); gold=copy.deepcopy(ex['after'])
                if novel and event['kind']=='refund': root_of(gold)['fields']['credit']=intended_credit(gold)
                checkpoint=HERE/'models'/('base' if novel and index<2 else 'adapted' if novel else 'base')
                if restart:
                    request={'database':database,'checkpoint':str(checkpoint),'tenant':graph['tenant'],'event':event,'revision':index}
                    response=json.loads(subprocess.run([sys.executable,str(HERE/'worker.py')],input=json.dumps(request),text=True,capture_output=True,check=True).stdout)
                    graph=response['graph']; evidence=response['receipt']; totals['processes']+=1
                    if previous and previous!=str(checkpoint): totals['checkpoint_switches']+=1
                    previous=str(checkpoint)
                else:
                    out=model.forward(graph,event,graph['tenant']); graph=out['graph']; evidence=out
                expected_ids={o['id'] for o in before['objects'] if o['id']==event['root'] or o['parent']==event['root']}
                retrieval=set(evidence['read_ids'])==expected_ids
                if not retrieval: totals['retrieval_errors']+=1
                expected=numeric_delta(before,gold); actual=numeric_delta(before,graph)
                correct=expected==actual
                # Comparing every resulting field prevents accumulated errors from disappearing in delta-only scoring.
                correct=correct and not numeric_delta(gold,graph)
                totals['steps']+=1; totals['exact_steps']+=int(correct); all_correct &= correct
                if not correct:
                    totals['operand_or_computation_errors']+=int(retrieval)
                    totals['failures'].append({'case':case['id'],'event':event['kind'],'actual':actual,'expected':expected})
                if case is cases[0]: samples.append({'event':event,'graph':copy.deepcopy(graph),'gold':gold,'correct':correct,'writes':evidence['writes'],'program':evidence['program']})
            totals['exact_trajectories']+=int(all_correct)
    return totals,samples

def property_checks(model,case):
    graph=copy.deepcopy(case['events'][0]['before']); event=case['events'][0]['event']
    expected=model.forward(graph,event,graph['tenant'])['graph']
    shuffled=copy.deepcopy(graph); random.Random(37).shuffle(shuffled['objects'])
    # Distractor belongs to another root and must never be retrieved or changed.
    distractor=copy.deepcopy(next(o for o in graph['objects'] if o['kind']=='item'))
    distractor.update(id='unrelated-item',parent='another-root'); distractor['fields']['unit']=99999999
    shuffled['objects'].append(distractor)
    result=model.forward(shuffled,event,graph['tenant'])['graph']
    kept=[o for o in result['objects'] if o['id']!='unrelated-item']
    checks={'permutation_and_distractor':sorted(kept,key=lambda x:x['id'])==sorted(expected['objects'],key=lambda x:x['id']),
            'distractor_unchanged':next(o for o in result['objects'] if o['id']=='unrelated-item')==distractor}
    try: model.forward(graph,event,'unrelated-tenant'); checks['tenant_isolation']=False
    except ValueError: checks['tenant_isolation']=True
    with tempfile.TemporaryDirectory() as tmp:
        store=Store(str(Path(tmp)/'state.sqlite')); store.seed(graph)
        receipt=store.apply(graph['tenant'],event,model,0)
        checks['duplicate_is_idempotent']=receipt==store.apply(graph['tenant'],event,model,0)
        changed={**event,'now':event['now']+1}
        try: store.apply(graph['tenant'],changed,model,1); checks['key_reuse_rejected']=False
        except ValueError: checks['key_reuse_rejected']=True
        try: store.apply(graph['tenant'],{**event,'id':'new-event'},model,0); checks['stale_revision_rejected']=False
        except ValueError: checks['stale_revision_rejected']=True
        checks['rejected_events_leave_state']=store.read(graph['tenant'],event['root'])['revision']==1
        store.close()
    return checks

def rule_generalization(model,old,expression,case):
    rng=random.Random(81293); result={'cases':1000,'learned_correct':0,'base_correct':0,'llm_rule_correct':0,'failures':[]}
    for i in range(result['cases']):
        ex=copy.deepcopy(case['events'][-1]); graph=ex['before']
        root_of(graph)['fields']['goods']=rng.randint(0,1000000)
        graph['config'].update(credit_rate=rng.randint(0,60),credit_cap=rng.randint(0,20000))
        expected=intended_credit(graph)
        actual=root_of(model.forward(graph,ex['event'],graph['tenant'])['graph'])['fields']['credit']
        baseline=evaluate(expression,root_of(graph),graph,ex['event'])
        before=root_of(old.forward(graph,ex['event'],graph['tenant'])['graph'])['fields']['credit']
        result['learned_correct']+=int(actual==expected); result['base_correct']+=int(before==expected); result['llm_rule_correct']+=int(baseline==expected)
        if actual!=expected and len(result['failures'])<5: result['failures'].append({'goods':root_of(graph)['fields']['goods'],'config':graph['config'],'expected':expected,'actual':actual})
    return result

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--skip-training',action='store_true'); args=parser.parse_args()
    (HERE/'results').mkdir(exist_ok=True)
    if not args.skip_training:
        from learn import train
    before=host_fingerprint(); cases=json.loads((HERE/'data/shopware.json').read_text())
    source=[c for c in cases if c['split']=='train']; target=[c for c in cases if c['split']=='test']
    if len(source)<3: raise ValueError('need at least three source configurations')
    language=json.loads((HERE/'data/language-rule.json').read_text()); expression=language['expression']
    if not args.skip_training: train([e for c in source for e in c['events']],HERE/'models/base')
    old=Model(HERE/'models/base'); examples=novel_examples(source)
    gaps=[]
    for ex in examples:
        predicted=old.forward(ex['before'],ex['event'],ex['before']['tenant'])['graph']
        gaps+=numeric_delta(predicted,ex['after'])
    (HERE/'data/merchant-examples.json').write_text(json.dumps(examples,ensure_ascii=False,indent=2))
    if not args.skip_training:
        if not gaps: raise RuntimeError('No demonstrated capability gap; refusing meaningless growth')
        train(examples,HERE/'models/adapted',base=HERE/'models/base')
    learned=Model(HERE/'models/adapted')
    native,sample=compare(old,target)
    old_new,_=compare(old,target,novel=True)
    adapted,new_sample=compare(learned,target,novel=True)
    restart,_=compare(learned,target,novel=True,restart=True)
    ablated,_=compare(Model(HERE/'models/adapted',ablate=True),target,novel=True)
    baseline=rule_generalization(learned,old,expression,target[0])
    checks=property_checks(learned,target[0]); after=host_fingerprint()
    previous_heads_preserved=all((old.weights[k]==learned.weights[k]).all() and old.meta['heads'][k]==learned.meta['heads'][k] for k in old.weights)
    from verify_rule import verify
    equivalence=verify(learned)
    results={'reference':json.loads((HERE/'data/oracle-manifest.json').read_text()),'source_trajectories':len(source),'target_shop':'Werkraum',
        'native_shadow':native,'before_new_rule':old_new,'after_new_rule':adapted,'restart_and_checkpoint_swap':restart,
        'weight_ablation':ablated,'new_rule_generalization':baseline,'kernel_checks':checks,'host_unchanged':before==after,
        'host_before':before,'host_after':after,'gap_detection':{'disagreements':len(gaps),'fields':sorted({x[1] for x in gaps}),'trigger':'oracle feedback, not autonomous semantic awareness'},
        'language':language,'new_rule_examples':len(examples),'new_heads':len(learned.meta['heads'])-len(old.meta['heads']),
        'new_rule_label_source':'three independent literal merchant fixtures; Qwen expression used only in baseline evaluation',
        'program_identifiability':{'base_heads':old.meta['training'][0]['records'],'new_head':learned.meta['training'][-1]['records'],
            'meaning':'Counts include equivalent expressions and observationally equivalent but different hypotheses. Not a uniqueness proof.'},
        'selected_rule_equivalence':equivalence,
        'base_weights':sum(w.size for w in old.weights.values()),'adapted_weights':sum(w.size for w in learned.weights.values()),'previous_heads_preserved':bool(previous_heads_preserved),
        'learned_rule':[dict(x,readable=expression_text(x['expr'])) for x in learned.program({'kind':'refund'})],
        'samples':{'native':sample,'adapted':new_sample},
        'limits':['Restricted program grammar, no Qwen weight training','Shadow simulation only; no merchant acceptance or binding authority','Full-refund status only; no PSP settlement or partial refund amount learning','Only projected DAL fields compared; full raw DAL logged separately','Shipping calculation supplied as input facts','No learned semantic retrieval; exact scoped lookup tested','No arbitrary rule induction or illegal event-order guarantee','Three examples do not uniquely identify every possible rule','Rule-engine baseline may tie or beat learned model; no superiority assumed']}
    (HERE/'results').mkdir(exist_ok=True)
    (HERE/'results/report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    (HERE/'data/disagreements.jsonl').write_text(''.join(json.dumps(f)+'\n' for f in native['failures']))
    summary={k:v for k,v in results.items() if k not in ['samples','language','host_before','host_after','reference','limits','learned_rule']}
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
