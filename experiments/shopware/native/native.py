"""Specialize the existing learned dataflow model and compile it to native code.

Only the frozen generic operations are compiled. Product prices and quantities
are runtime operands. No generated Python or arbitrary model-written C is run.
"""
from __future__ import annotations
import copy,ctypes,hashlib,json,subprocess,time,platform,fcntl
from common import ROOT,BASE
from vm import Model,retrieve,root_of,digest,VOCAB_HASH

SCHEMA=json.loads((BASE/'schema.json').read_text())
KINDS={k:i+1 for i,k in enumerate(SCHEMA) if k not in ('config','event')}
FIELDS=sorted(set(f for k,s in SCHEMA.items() if k not in ('config','event') for f in s))
SLOTS={field:i+1 for i,field in enumerate(FIELDS)}
STRIDE=len(FIELDS)+1

HEADER=r'''
#include <stdint.h>
#include <stddef.h>
typedef __int128 wide;
static int64_t checked(wide v,int *err){
    if(v>=((wide)1<<63)||v<=-((wide)1<<63)){*err=1;return 0;}return (int64_t)v;
}
static int64_t add(int64_t a,int64_t b,int*e){return checked((wide)a+b,e);}
static int64_t sub(int64_t a,int64_t b,int*e){return checked((wide)a-b,e);}
static int64_t mul(int64_t a,int64_t b,int*e){return checked((wide)a*b,e);}
static int64_t md(int64_t a,int64_t b,int64_t d,int*e){
 if(d==0){*e=2;return 0;}wide p=(wide)a*b,q=d;int neg=(p<0)!=(q<0);
 if(p<0)p=-p;if(q<0)q=-q;wide v=(2*p+q)/(2*q);return checked(neg?-v:v,e);
}
static int64_t lo(int64_t a,int64_t b){return a<b?a:b;}
static int64_t hi(int64_t a,int64_t b){return a>b?a:b;}
static int64_t reduce(int64_t*x,int n,int kind,int field,int*e){
 wide total=0;for(int j=0;j<n;j++)if(x[j*STRIDE]==kind)total+=x[j*STRIDE+field];return checked(total,e);
}
'''

def emit(e,config):
    op=e[0]
    if op=='const':
        if type(e[1]) is not int or abs(e[1])>=2**63:raise ValueError('literal range')
        return str(e[1])+'LL'
    if op=='read':
        scope,field=e[1:]
        if scope=='self':return f'x[i*STRIDE+{SLOTS[field]}]'
        if scope=='root':return f'x[root*STRIDE+{SLOTS[field]}]'
        if scope=='config':return emit(['const',config[field]],config)
        if scope=='event' and field=='now':return 'now'
        raise ValueError('unsupported read')
    if op=='sum':return f'reduce(x,n,{KINDS[e[1]]},{SLOTS[e[2]]},&err)'
    a=[emit(child,config) for child in e[1:]]
    if op in ('add','sub','mul'):return f'{op}({a[0]},{a[1]},&err)'
    if op=='muldiv':return f'md({a[0]},{a[1]},{a[2]},&err)'
    if op in ('min','max'):return f'{"lo" if op=="min" else "hi"}({a[0]},{a[1]})'
    if op in ('eq','le'):return f'({a[0]} {"==" if op=="eq" else "<="} {a[1]})'
    if op=='select':return f'({a[0]}?{a[1]}:{a[2]})'
    raise ValueError('operation is not in frozen compiler vocabulary: '+str(op))

class NativeModel:
    def __init__(self,config,checkpoint=BASE/'models/adapted'):
        start=time.perf_counter();self.model=Model(checkpoint);self.config=copy.deepcopy(config)
        self.plans={event:self.model.program({'kind':event}) for event in self.model.meta['plans']}
        self.events={name:i for i,name in enumerate(self.plans)}
        # Compiler source hash makes a changed backend invalidate native cache entries.
        source_hash=hashlib.sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()
        self.key=digest({'model':self.model.identity,'config':config,'vocabulary':VOCAB_HASH,'compiler':source_hash,'schema':SCHEMA})
        self.identity=self.model.identity; cache=ROOT/'state/native';cache.mkdir(parents=True,exist_ok=True)
        self.source=cache/(self.key+'.c');self.library=cache/(self.key+('.dylib' if platform.system()=='Darwin' else '.so'))
        with (cache/(self.key+'.lock')).open('a') as lock:
          fcntl.flock(lock,fcntl.LOCK_EX)
          self.compiled_now=not self.library.exists()
          if self.compiled_now:
            code=f'#define STRIDE {STRIDE}\n'+HEADER+'\nint run(int64_t*x,int n,int root,int64_t now,int event){int err=0;switch(event){\n'
            for event,plan in self.plans.items():
                code+=f'case {self.events[event]}:\n'
                for step in plan:
                    code+=f'for(int i=0;i<n;i++)if(x[i*STRIDE]=={KINDS[step["kind"]]}){{x[i*STRIDE+{SLOTS[step["field"]]}]={emit(step["expr"],config)};if(err)return err;}}\n'
                code+='break;\n'
            code+='default:return 3;}return err;}\n'
            self.source.write_text(code)
            temporary=self.library.with_suffix('.tmp'+self.library.suffix)
            flags=['-dynamiclib'] if platform.system()=='Darwin' else ['-shared','-fPIC']
            subprocess.run(['clang','-O3',*flags,'-std=c11',str(self.source),'-o',str(temporary)],check=True,capture_output=True,text=True)
            temporary.replace(self.library)
        self.lib=ctypes.CDLL(str(self.library));self.fn=self.lib.run
        self.fn.argtypes=[ctypes.POINTER(ctypes.c_int64),ctypes.c_int,ctypes.c_int,ctypes.c_int64,ctypes.c_int]
        self.fn.restype=ctypes.c_int;self.ready_ms=(time.perf_counter()-start)*1000

    def forward(self,graph,event,tenant):
        if graph['config']!=self.config:raise ValueError('merchant configuration changed: compile a new specialization')
        objects=retrieve(graph,event,tenant)
        if event['kind'] not in self.events:raise ValueError('unlearned event')
        raw=[];root_index=None
        for i,obj in enumerate(objects):
            if obj['id']==event['root']:root_index=i
            raw.append(KINDS[obj['kind']])
            for field in FIELDS:
                value=obj['fields'].get(field,0)
                if type(value) is not int or abs(value)>=2**63:raise ValueError('input integer range/type')
                raw.append(value)
        now=event['now']
        if type(now) is not int or abs(now)>=2**63:raise ValueError('event integer range/type')
        array=(ctypes.c_int64*len(raw))(*raw)
        status=self.fn(array,len(objects),root_index,now,self.events[event['kind']])
        if status:raise ValueError('native arithmetic error '+str(status))
        changed={};writes=[]
        for i,obj in enumerate(objects):
            obj=copy.deepcopy(obj)
            for field,old in obj['fields'].items():
                value=int(array[i*STRIDE+SLOTS[field]])
                if value!=old:writes.append({'id':obj['id'],'field':field,'old':old,'value':value})
                obj['fields'][field]=value
            changed[obj['id']]=obj
        result=copy.deepcopy(graph);result['objects']=[changed.get(o['id'],o) for o in result['objects']]
        return {'graph':result,'writes':writes,'program':self.plans[event['kind']],'model':self.identity,
                'native_key':self.key,'read_ids':[o['id'] for o in objects]}

if __name__=='__main__':
    cases=json.loads((BASE/'data/shopware.json').read_text());matched=0
    for case in cases:
        compiled=NativeModel(case['config']);graph=case['events'][0]['before']
        for event in case['events']:
            a=compiled.forward(graph,event['event'],case['shop'])
            b=compiled.model.forward(graph,event['event'],case['shop'])
            assert a['graph']==b['graph'];graph=a['graph'];matched+=1
    print(json.dumps({'interpreter_native_equal':matched,'cases':len(cases)}))
