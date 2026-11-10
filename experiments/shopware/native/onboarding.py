"""Import -> map -> replay historical labels -> install a merchant package.

Replay validates declared history only. It cannot discover missing promotions,
historic configurations or tax modes from order totals alone.
"""
import argparse,copy,csv,json,re,tempfile
from pathlib import Path
from common import ROOT
from packages import Repository,uid,now_ms
from service import Commerce
from vm import root_of

def map_feed(template,rows):
    package=copy.deepcopy(template);package['products']=[];package['offers']=[]
    if not re.fullmatch('[a-z][a-z0-9_]{1,40}',package['id']):raise ValueError('invalid merchant identifier')
    for row in rows:
        record={destination:row[source] for source,destination in package['field_mapping'].items()}
        sku=str(record['productNumber']);identifier=uid(package['id']+'/'+sku)
        price=int(record['unit_cents']);stock=int(record['stock']);rate=int(record['taxRate'])
        if min(price,stock,rate)<0 or max(price,stock)>=2**63:raise ValueError('invalid typed amount')
        package['products'].append({'id':identifier,'productNumber':sku,'name':str(record['name']),
            'description':row.get('description',''),'taxRate':rate,'active':True,'properties':{},'variants':[],
            'media':[{'url':row.get('image','/assets/moka.jpg'),'alt':str(record['name'])}]})
        package['offers'].append({'id':identifier,'unit_cents':price,'stock':stock,'version':1,'observed_at_ms':now_ms()})
    if len({p['productNumber'] for p in package['products']})!=len(package['products']):raise ValueError('duplicate SKU')
    return package

def replay(package,history):
    with tempfile.TemporaryDirectory() as temp:
        repo=Repository(Path(temp)/'history.sqlite');repo.install(package);service=Commerce(repo)
        products=repo.products(package['id']);sku={p['productNumber']:p['id'] for p in products};checks=[]
        for i,order in enumerate(history):
            selected=copy.deepcopy(products)
            # Historical price snapshots, not today's catalog, are the arithmetic operands.
            items={sku[l['sku']]:l['quantity'] for l in order['lines']}
            for line in order['lines']:
                p=next(p for p in selected if p['id']==sku[line['sku']]);p['_offer']['unit_cents']=line['unit_cents'];p['_offer']['stock']=line['quantity'];p['taxRate']=line['tax_rate']
            graph=service.calculate(package['id'],'history-'+str(i),items,selected,package);f=root_of(graph)['fields']
            actual={'gross':f['gross'],'net':f['net'],'tax':f['tax']};checks.append({'case':i,'actual':actual,'expected':order['expected'],'exact':actual==order['expected']})
        return {'cases':len(checks),'exact':sum(c['exact'] for c in checks),'checks':checks,'model':service.model(package).identity}

def onboard(template,rows,history,repository):
    if not history:raise ValueError('history replay labels are required before activation')
    package=map_feed(template,rows);result=replay(package,history)
    if result['cases']!=result['exact']:raise ValueError('historical replay mismatch; package not activated')
    repository.install(package);return package,result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--template',required=True);parser.add_argument('--feed',required=True);parser.add_argument('--history',required=True);args=parser.parse_args()
    template=json.loads(Path(args.template).read_text())
    with Path(args.feed).open() as f:rows=list(csv.DictReader(f))
    package,result=onboard(template,rows,json.loads(Path(args.history).read_text()),Repository())
    print(json.dumps({'merchant':package['id'],'history_replay':result},indent=2))
