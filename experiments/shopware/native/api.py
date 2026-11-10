from __future__ import annotations
import json,mimetypes,re,time,traceback,os
from collections import Counter
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from common import ROOT,BASE
from service import Commerce,APIError,cents
from packages import install_demo_packages

PORT=int(os.environ.get('HOLODECK_PORT','18778'))
SERVICE=Commerce(install_demo_packages());REQUESTS=Counter()

def jsonapi(rows,entity,total=None):
    return {'data':[{'id':row['id'],'type':entity,'attributes':{k:v for k,v in row.items() if k!='id'},'links':{},'relationships':{}} for row in rows],
            'meta':{'total':len(rows) if total is None else total},'links':{},'aggregations':[]}

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def send(self,status,value,token=None,content_type='application/json'):
        body=json.dumps(value,ensure_ascii=False).encode() if content_type=='application/json' else value
        self.send_response(status);self.send_header('Content-Type',content_type+('; charset=utf-8' if content_type.startswith('text/') or content_type=='application/json' else ''))
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-ShopVM-Large-Model-Calls','0');self.send_header('Server-Timing',f'app;dur={(time.perf_counter()-self.started)*1000:.3f}')
        if token:self.send_header('sw-context-token',token)
        self.end_headers();self.wfile.write(body)

    def tenant(self,admin=False):
        key=self.headers.get('Authorization','').removeprefix('Bearer ') if admin else self.headers.get('sw-access-key','')
        expected={('shopvm-admin-' if admin else 'shopvm-')+t:t for t in SERVICE.repo.tenants()}
        if key not in expected:raise APIError(401,'FRAMEWORK__INVALID_ACCESS_TOKEN','Lokaler Testzugang fehlt oder ist ungültig.')
        return expected[key]

    def handle_request(self):
        self.started=time.perf_counter();path=urlsplit(self.path).path;query=parse_qs(urlsplit(self.path).query);method=self.command
        REQUESTS[(method,path)]+=1
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>1000000:raise APIError(413,'SHOPVM__BODY_TOO_LARGE','Anfrage zu groß.')
            body=json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body,dict):raise APIError(400,'SHOPVM__BODY_INVALID','JSON Objekt erwartet.')
            origin=self.headers.get('Origin')
            if method not in ('GET','HEAD') and origin and origin not in (f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}'):
                raise APIError(403,'SHOPVM__ORIGIN_DENIED','Fremder Ursprung.')
            if method=='GET' and path=='/':return self.send(200,(ROOT/'public/index.html').read_bytes(),content_type='text/html')
            if method=='GET' and path.startswith('/assets/'):
                name=path.removeprefix('/assets/')
                if name not in ['moka.jpg','moka-pair.jpg','press.jpg']:raise APIError(404,'NOT_FOUND','Bild nicht gefunden.')
                asset=ROOT/'public/assets'/name
                return self.send(200,asset.read_bytes(),content_type='image/jpeg')
            if method=='POST' and path=='/api/oauth/token':
                username=body.get('username');tenant=next((t for t in SERVICE.repo.tenants() if username=='admin@'+t+'.test'),None)
                if tenant is None or body.get('password')!='local-test':raise APIError(401,'FRAMEWORK__INVALID_CREDENTIALS','Ungültiger lokaler Testzugang.')
                return self.send(200,{'access_token':'shopvm-admin-'+tenant,'token_type':'Bearer','expires_in':3600})
            if path=='/vm/report' and method=='GET':
                file=ROOT/'results/report.json'
                return self.send(200,json.loads(file.read_text()) if file.exists() else {'status':'measurement-pending'})
            if path=='/vm/coverage' and method=='GET':return self.send(200,json.loads((ROOT/'inventory/coverage.json').read_text()))
            if path=='/vm/metrics' and method=='GET':return self.send(200,{**SERVICE.metrics,'requests':sum(REQUESTS.values()),'route_requests':{m+' '+p:n for (m,p),n in REQUESTS.items()}})
            if path=='/vm/merchants' and method=='GET':return self.send(200,[{'id':t,'name':SERVICE.repo.package(t)['name']} for t in SERVICE.repo.tenants()])
            if path=='/vm/view' and method=='GET':
                tenant=self.tenant();role=query.get('role',['shopper'])[0]
                if role not in ('shopper','merchant'):raise APIError(400,'SHOPVM__ROLE_INVALID','Unbekannte Ansicht.')
                if role=='merchant' and self.tenant(admin=True)!=tenant:raise APIError(403,'SHOPVM__MERCHANT_MISMATCH','Händlerzuordnung stimmt nicht.')
                return self.send(200,SERVICE.bound_view(tenant,role,query.get('q',[''])[0],query.get('sort',['name'])[0],query.get('direction',['ASC'])[0]))
            if path.startswith('/store-api/'):
                tenant=self.tenant();token=self.headers.get('sw-context-token')
                if path=='/store-api/context' and method=='GET':
                    ctx=SERVICE.repo.ensure_context(tenant,token);package=SERVICE.repo.package(tenant)
                    return self.send(200,{'token':ctx['token'],'customer':json.loads(ctx['customer']) if ctx['customer'] else None,
                        'currency':{'isoCode':package['currency'],'id':'b7d2554b0ce847cd82f3ac9bd1c0dfca'},'taxState':'gross','apiAlias':'sales_channel_context'},ctx['token'])
                if path=='/store-api/product' and method in ('GET','POST'):
                    rows,total=SERVICE.product_rows(tenant,body);return self.send(200,{'elements':rows,'total':total,'aggregations':[],'apiAlias':'product_listing'})
                match=re.fullmatch('/store-api/product/([a-f0-9]{32})',path)
                if match and method in ('GET','POST'):
                    rows,_=SERVICE.product_rows(tenant,{'ids':[match[1]]})
                    if not rows:raise APIError(404,'CONTENT__PRODUCT_NOT_FOUND','Produkt nicht gefunden.')
                    return self.send(200,{'product':rows[0],'configurator':[],'apiAlias':'product_detail'})
                if path=='/store-api/checkout/cart' and method in ('GET','DELETE'):
                    value,token=SERVICE.cart(tenant,token,operation='clear' if method=='DELETE' else 'get');return self.send(200,value,token)
                if path=='/store-api/checkout/cart/line-item' and method in ('POST','PATCH','DELETE'):
                    value,token=SERVICE.cart(tenant,token,body,{'POST':'add','PATCH':'update','DELETE':'remove'}[method]);return self.send(200,value,token)
                if path=='/store-api/account/register' and method=='POST':
                    value,token=SERVICE.register(tenant,token,body);return self.send(200,value,token)
                if path=='/store-api/checkout/order' and method=='POST':
                    value,token=SERVICE.checkout(tenant,token,body);return self.send(200,value,token)
                if path=='/store-api/order' and method=='POST':
                    ctx=SERVICE.repo.ensure_context(tenant,token)
                    if not ctx['customer']:raise APIError(403,'CHECKOUT__CUSTOMER_NOT_LOGGED_IN','Kundenzugang fehlt.')
                    customer=json.loads(ctx['customer'])
                    rows=[r for r in SERVICE.orders(tenant,body.get('ids')) if r['orderCustomer']['id']==customer['id']]
                    return self.send(200,{'orders':{'elements':rows,'total':len(rows),'apiAlias':'order_collection'},'apiAlias':'order_route_response'},ctx['token'])
            elif path.startswith('/api/'):
                tenant=self.tenant(admin=True)
                if path=='/api/search/product' and method=='POST':
                    rows,total=SERVICE.product_rows(tenant,body);return self.send(200,jsonapi(rows,'product',total))
                if path=='/api/search/order' and method=='POST':return self.send(200,jsonapi(SERVICE.orders(tenant,body.get('ids')),'order'))
                match=re.fullmatch('/api/product/([a-f0-9]{32})',path)
                if match and method=='PATCH':
                    change={k:body[k] for k in ['stock','name','description'] if k in body}
                    if set(body)-{'id','price','stock','name','description'}:raise APIError(501,'SHOPVM__WRITE_UNSUPPORTED','Dieses Schreibfeld ist nicht unterstützt.')
                    if 'price' in body:
                        if len(body['price'])!=1:raise APIError(501,'SHOPVM__CURRENCY_UNSUPPORTED','Nur EUR ist in dieser Scheibe unterstützt.')
                        change['unit_cents']=cents(body['price'][0]['gross'])
                    SERVICE.repo.update(tenant,match[1],change);return self.send(204,b'',content_type='application/octet-stream')
                match=re.fullmatch('/api/(product|order)/([a-f0-9]{32})',path)
                if match and method=='GET':
                    rows=SERVICE.product_rows(tenant,{'ids':[match[2]]})[0] if match[1]=='product' else SERVICE.orders(tenant,[match[2]])
                    if not rows:raise APIError(404,'FRAMEWORK__RESOURCE_NOT_FOUND','Datensatz nicht gefunden.')
                    value=jsonapi(rows,match[1]);value['data']=value['data'][0];return self.send(200,value)
            raise APIError(501,'SHOPVM__NOT_ABSORBED','Diese Operation ist inventarisiert, aber noch nicht nachgebildet. Es gibt keinen Proxy zum Referenzshop.')
        except APIError as e:return self.send(e.status,{'errors':[{'status':str(e.status),'code':e.code,'detail':e.detail}]})
        except (ValueError,KeyError,TypeError) as e:return self.send(400,{'errors':[{'status':'400','code':'SHOPVM__INVALID_INPUT','detail':str(e)}]})
        except Exception:
            traceback.print_exc();return self.send(500,{'errors':[{'status':'500','code':'SHOPVM__INTERNAL_ERROR','detail':'Lokale Verarbeitung fehlgeschlagen.'}]})

    do_GET=handle_request;do_POST=handle_request;do_PATCH=handle_request;do_DELETE=handle_request

if __name__=='__main__':
    print(f'ShopVM native: http://127.0.0.1:{PORT}', flush=True)
    ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
