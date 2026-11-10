import argparse,json,math,os,platform,time
from urllib.request import Request, urlopen
from common import ROOT
from service import Commerce
from packages import install_demo_packages

def stats(values):
    values=sorted(values);return {'n':len(values),'p50_ms':round(values[math.ceil(.5*len(values))-1],4),
        'p95_ms':round(values[math.ceil(.95*len(values))-1],4),'max_ms':round(max(values),4)}

def measure(fn,count=500):
    for _ in range(20):fn()
    samples=[]
    for _ in range(count):
        start=time.perf_counter_ns();fn();samples.append((time.perf_counter_ns()-start)/1e6)
    return stats(samples)

def main(http=False):
    service=Commerce(install_demo_packages());package=service.repo.package('kanne');products=service.repo.products('kanne')
    graph=service.graph('kanne','benchmark',{p['id']:1 for p in products},products,package)
    model=service.model(package);event={'kind':'price','root':'benchmark','now':1,'id':'benchmark'}
    metrics={'native_transition':measure(lambda:model.forward(graph,event,'kanne')),
        'bound_view':measure(lambda:service.bound_view('kanne','merchant'))}
    if http:
        port = int(os.environ.get("HOLODECK_PORT", "18778"))
        request = Request(
            f"http://127.0.0.1:{port}/vm/view?role=merchant",
            headers={"Authorization": "Bearer shopvm-admin-kanne"},
        )

        def read_view():
            with urlopen(request, timeout=5) as response:
                return json.load(response)

        metrics["bound_view_http"] = measure(read_view, 80)
    budgets=json.loads((ROOT/'budgets.json').read_text());gates={name:metrics[name]['p95_ms']<budget['p95_ms'] for name,budget in budgets.items() if name in metrics}
    report={'hardware':{'os':platform.system(),'architecture':platform.machine()},'clock':'perf_counter_ns, nearest-rank percentile',
        'population':'3 products, 2 merchant packages; cached compiled model, serial calls, 20 warmups; Python marshaling and validation included in native_transition',
        'metrics':metrics,'gates':gates,'large_model_calls':service.metrics['large_model_calls'],
        'unmeasured':['small-model routing/search','first generated frame','concurrent production load','million-object catalog'],
        'view_generation':json.loads((ROOT/'results/view-generation.json').read_text()) if (ROOT/'results/view-generation.json').exists() else []}
    (ROOT/'results').mkdir(exist_ok=True)
    (ROOT/'results/latency.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'metrics':metrics,'gates':gates,'large_model_calls':report['large_model_calls']},indent=2))
    if not all(gates.values()):raise SystemExit('latency budget failed')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--http',action='store_true');main(parser.parse_args().http)
