"""Generate constrained, reusable UI programs once using the local Qwen model."""
import copy,json,time,urllib.request
from common import ROOT

SCHEMA={'type':'object','additionalProperties':False,'required':['title','intro','sections'],
 'properties':{'title':{'type':'string'},'intro':{'type':'string'},'sections':{'type':'array','minItems':1,'maxItems':3,
 'items':{'type':'object','additionalProperties':False,'required':['component','title','binding','columns'],
 'properties':{'component':{'enum':['product-grid','inventory-table','order-list']},'title':{'type':'string'},
 'binding':{'enum':['products','orders']},'columns':{'type':'array','items':{'enum':['name','productNumber','price','stock']}}}}}}}

def validate(value,role):
    if set(value)!={'title','intro','sections'}:raise ValueError('invalid view root')
    expected={'shopper':{'product-grid'},'merchant':{'inventory-table','order-list'}}[role]
    if {s['component'] for s in value['sections']}!=expected:raise ValueError('missing or unexpected components')
    for section in value['sections']:
        if section['binding']!=('orders' if section['component']=='order-list' else 'products'):raise ValueError('invalid binding')
        if section['component']=='inventory-table' and set(section['columns'])!={'name','productNumber','price','stock'}:raise ValueError('required columns missing')
    return value

def main():
    (ROOT/'views').mkdir(exist_ok=True);records=[]
    (ROOT/'results').mkdir(exist_ok=True)
    for role in ['shopper','merchant']:
        prompt=('Erzeuge eine deutsche Oberfläche für zwei Shops, Kanne und Werkraum, beide verkaufen Espressokocher und Kaffeekannen. '
            'Diese Ansicht wird für beide Marken unverändert verwendet. Erwähne niemals einen Markennamen im title oder intro. '
            'Der Titel beschreibt die Aufgabe: Einkaufen oder Sortiment verwalten. Titel höchstens fünf Wörter. '
            'Die einzige Produktinformation: drei Kaffeekannen, zwei aus Aluminium und eine aus Glas. Keine Informationen über Herstellung, Qualität oder Herkunft liegen vor. '
            'Behaupte insbesondere nie handgefertigt, nachhaltig oder andere unbelegte Eigenschaften. '
            'Die Ansicht wird als Komponentenbaum gespeichert. Produkte, Preise, Bestand und Bestellungen kommen ausschließlich aus Datenbindungen. '
            'Schreibe keine Preise oder Leistungsversprechen in Texte. Keine Technikbegriffe, keine erfundenen Waren oder Aktionen. '
            'Titel und intro kurz, natürlich und nützlich. Keine Bindestriche als Stilmittel. '
            + ('Für Käufer: genau eine product-grid Sektion mit binding products und columns []. Fokus Freude an gutem Kaffee.' if role=='shopper' else
               'Für Händler: eine inventory-table Sektion mit binding products und columns name, productNumber, price, stock; danach eine order-list Sektion mit binding orders und columns []. Fokus Sortiment pflegen und Bestellungen überblicken.'))
        schema=copy.deepcopy(SCHEMA);sections=schema['properties']['sections'];sections['minItems']=sections['maxItems']=1 if role=='shopper' else 2
        sections['items']['properties']['component']['enum']=['product-grid'] if role=='shopper' else ['inventory-table','order-list']
        payload={'model':'qwen3.6:35b','messages':[{'role':'user','content':prompt}],
                 'format':schema,'stream':False,'think':False,'options':{'temperature':0.2,'num_predict':1200}}
        start=time.perf_counter();request=urllib.request.Request('http://127.0.0.1:11434/api/chat',json.dumps(payload).encode(),{'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=240) as response:raw=json.load(response)
        elapsed=(time.perf_counter()-start)*1000
        (ROOT/'results'/f'view-raw-{role}.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
        view=validate(json.loads(raw['message']['content']),role)
        (ROOT/'views'/f'{role}.json').write_text(json.dumps(view,ensure_ascii=False,indent=2))
        records.append({'role':role,'model':raw.get('model'),'prompt':prompt,'schema':SCHEMA,'elapsed_ms':elapsed,
            'output_tokens':raw.get('eval_count'),'view':view,'claim':'model generated composition and copy; fixed safe renderer supplies interactions; no model on sort or edit'})
        print(json.dumps({'role':role,'elapsed_ms':elapsed,'title':view['title']},ensure_ascii=False),flush=True)
    (ROOT/'results/view-generation.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
