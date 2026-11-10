"""Offline equivalence check against a stated, human-readable numerical contract.

This proves the selected refund-credit expression, not natural-language intent,
the refund precondition, Shopware equivalence or correctness of every program.
No solver is imported by the executing VM.
"""
import json
import z3
from vm import HERE,Model,expression_text

def verify(model):
    goods,rate,cap=z3.Ints('goods rate cap')
    reads={('self','goods'):goods,('root','goods'):goods,('config','credit_rate'):rate,('config','credit_cap'):cap}
    def translate(e):
        if e[0]=='const': return z3.IntVal(e[1])
        if e[0]=='read': return reads[tuple(e[1:])]
        a=[translate(x) for x in e[1:]]
        if e[0]=='add':return a[0]+a[1]
        if e[0]=='sub':return a[0]-a[1]
        if e[0]=='mul':return a[0]*a[1]
        if e[0]=='min':return z3.If(a[0]<=a[1],a[0],a[1])
        if e[0]=='max':return z3.If(a[0]>=a[1],a[0],a[1])
        if e[0]=='muldiv':
            # This proof accepts only a positive literal denominator.
            if e[3][0]!='const' or e[3][1]<=0: raise ValueError('unsupported proof divisor')
            p=a[0]*a[1]; d=a[2]
            return z3.If(p<0,-((2*(-p)+d)/(2*d)),(2*p+d)/(2*d))
        raise ValueError('unsupported proof operation')
    candidates=[p for p in model.program({'kind':'refund'}) if p['kind']=='root' and p['field']=='credit']
    if len(candidates)!=1:raise ValueError('ambiguous output writer')
    selected=candidates[0]['expr']; output=translate(selected)
    expected=z3.If((goods*rate+50)/100<cap,(goods*rate+50)/100,cap)
    solver=z3.Solver();solver.set(timeout=10000)
    solver.add(goods>=0,goods<=100000000,rate>=0,rate<=100,cap>=0,cap<=100000000,output!=expected)
    status=solver.check()
    return {'solver':'Z3 '+z3.get_version_string(),'counterexample_query':str(status),'equivalent_on_domain':status==z3.unsat,
        'domain':{'goods_cents':[0,100000000],'rate_percent':[0,100],'cap_cents':[0,100000000]},
        'contract':'min(floor((goods * rate + 50) / 100), cap), after an authorized complete refund',
        'selected_expression':expression_text(selected),'model':model.identity,
        'does_not_prove':['the natural-language interpretation','event authorization or refund preconditions','arbitrary business rules','complete Shopware equivalence']}

if __name__=='__main__':
    result=verify(Model(HERE/'models/adapted'))
    (HERE/'results/rule-equivalence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
