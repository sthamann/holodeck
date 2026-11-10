import concurrent.futures,copy,json,tempfile,unittest
from pathlib import Path
from common import ROOT,BASE
from packages import Repository
from service import Commerce,APIError
from native import NativeModel
from vm import Model

CUSTOMER={'firstName':'Mira','lastName':'Test','email':'mira@example.test','guest':True,'billingAddress':{'street':'Testweg'}}

class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.repo=Repository(Path(self.temp.name)/'test.sqlite')
        for name in ['kanne','werkraum']:self.repo.install(json.loads((ROOT/'merchants'/f'{name}.json').read_text()))
        self.service=Commerce(self.repo);self.id=self.repo.products('kanne')[0]['id']
    def tearDown(self):self.temp.cleanup()
    def prepared(self,quantity=1):
        cart,token=self.service.cart('kanne',None,{'items':[{'id':self.id,'quantity':quantity}]},'add')
        self.service.register('kanne',token,CUSTOMER);return cart,token
    def test_fresh_offer_is_bound_and_old_quote_cannot_checkout(self):
        cart,token=self.prepared();self.repo.update('kanne',self.id,{'unit_cents':1234})
        with self.assertRaises(APIError) as error:self.service.checkout('kanne',token,{})
        self.assertEqual(error.exception.code,'SHOPVM__REQUOTE_REQUIRED');self.assertEqual(len(self.service.orders('kanne')),0)
        updated,_=self.service.cart('kanne',token);self.assertEqual(updated['price']['totalPrice'],12.34)
        order,_=self.service.checkout('kanne',token,{})
        self.assertEqual(order['amountTotal'],12.34);self.assertEqual(order['extensions']['shopvm']['boundOffers'][0]['version'],2)
    def test_last_unit_is_committed_once_under_concurrency(self):
        self.repo.update('kanne',self.id,{'stock':1});a=self.prepared()[1];b=self.prepared()[1]
        def attempt(token):
            try:return self.service.checkout('kanne',token,{})[0]['id']
            except APIError:return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[a,b]))
        self.assertEqual(sum(x is not None for x in results),1);self.assertEqual(len(self.service.orders('kanne')),1)
        self.assertEqual(next(p for p in self.repo.products('kanne') if p['id']==self.id)['_offer']['stock'],0)
    def test_checkout_retry_and_next_cart_are_different(self):
        _,token=self.prepared();first,_=self.service.checkout('kanne',token,{})
        restored=Commerce(Repository(self.repo.path));retry,_=restored.checkout('kanne',token,{})
        self.assertEqual(first['id'],retry['id']);empty,_=restored.cart('kanne',token);self.assertEqual(empty['lineItems'],[])
        restored.cart('kanne',token,{'items':[{'id':self.id,'quantity':1}]},'add');second,_=restored.checkout('kanne',token,{})
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(len(restored.orders('kanne')),2)
    def test_tenant_isolation(self):
        _,token=self.prepared()
        with self.assertRaises(ValueError):self.service.cart('werkraum',token)
        self.assertEqual(self.service.orders('werkraum'),[])
        with self.assertRaises(ValueError):self.repo.update('werkraum',self.id,{'stock':1})
    def test_prices_change_bindings_without_retraining_or_recompile(self):
        before=self.service.bound_view('kanne','shopper');key=before['runtime']['nativeKey']
        self.repo.update('kanne',self.id,{'unit_cents':4321});after=self.service.bound_view('kanne','shopper')
        self.assertNotEqual(before['version'],after['version']);self.assertEqual(key,after['runtime']['nativeKey'])
        self.assertEqual(next(p for p in after['bindings']['products'] if p['id']==self.id)['calculatedPrice']['unitPrice'],43.21)
        self.assertEqual(self.service.metrics['large_model_calls'],0)
    def test_same_shared_model_specializes_for_two_merchants(self):
        a=self.service.model(self.repo.package('kanne'));b=self.service.model(self.repo.package('werkraum'))
        self.assertEqual(a.identity,b.identity);self.assertNotEqual(a.key,b.key)
        self.assertNotEqual(self.service.bound_view('kanne','shopper')['merchant']['style'],self.service.bound_view('werkraum','shopper')['merchant']['style'])
    def test_native_interpreter_rounding_and_overflow(self):
        package=self.repo.package('kanne');products=self.repo.products('kanne');native=self.service.model(package);reference=Model(BASE/'models/base')
        for price in [1,5,19,37,99,119,3490,99999]:
            for quantity in [1,3,11]:
                p=copy.deepcopy(products[0]);p['_offer']['unit_cents']=price
                graph=self.service.graph('kanne','test',{self.id:quantity},[p],package);event={'kind':'price','root':'test','now':1,'id':'test'}
                self.assertEqual(native.forward(graph,event,'kanne')['graph'],reference.forward(graph,event,'kanne')['graph'])
        products[0]['_offer']['unit_cents']=2**62
        graph=self.service.graph('kanne','test',{self.id:3},products,package)
        with self.assertRaises(ValueError):native.forward(graph,{'kind':'price','root':'test','now':1,'id':'overflow'},'kanne')

    def test_mixed_tax_percentages_match_shopware_original_fixture(self):
        fixture=json.loads((ROOT/'learning_sources/tax-rules.json').read_text())
        package=self.repo.package('kanne');products=self.repo.products('kanne')[:2]
        products[0]['taxRate']=19;products[0]['_offer']['unit_cents']=1190
        products[1]['taxRate']=7;products[1]['_offer']['unit_cents']=2140
        for total,reference in [(3330,fixture['mixed']),(0,fixture['zero'])]:
            if total==0:
                for p in products:p['_offer']['unit_cents']=0
            graph=self.service.calculate('kanne','mixed',{p['id']:1 for p in products},products,package)
            response=self.service.cart_response(graph,products,'mixed','test')
            actual=sorted(response['price']['taxRules'],key=lambda x:x['taxRate']);expected=sorted(reference,key=lambda x:x['taxRate'])
            self.assertEqual(len(actual),len(expected))
            for a,b in zip(actual,expected):self.assertEqual(a['taxRate'],b['taxRate']);self.assertAlmostEqual(a['percentage'],b['percentage'],places=12)
    def test_unsupported_search_does_not_silently_approximate(self):
        with self.assertRaises(APIError) as error:self.service.product_rows('kanne',{'aggregations':[{'type':'sum'}]})
        self.assertEqual(error.exception.status,501)

    def test_unsupported_shipping_does_not_save_cart(self):
        package=self.repo.package('kanne');package['config']['shipping_gross']=499
        with self.repo.connect() as db:db.execute('UPDATE packages SET value=? WHERE tenant=?',(json.dumps(package),'kanne'))
        with self.assertRaises(APIError) as error:self.prepared()
        self.assertEqual(error.exception.status,501)
        with self.repo.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM contexts').fetchone()[0],0)
    def test_view_isolation_sort_and_orders(self):
        before=self.service.bound_view('kanne','merchant');self.prepared();_,token=self.prepared();self.service.checkout('kanne',token,{})
        after=self.service.bound_view('kanne','merchant');self.assertNotEqual(before['version'],after['version'])
        self.assertEqual(len(after['bindings']['orders']),1);self.assertEqual(self.service.bound_view('werkraum','merchant')['bindings']['orders'],[])
        a=self.service.bound_view('kanne','merchant',direction='ASC')['bindings']['products'];b=self.service.bound_view('kanne','merchant',direction='DESC')['bindings']['products']
        self.assertEqual([p['id'] for p in a],list(reversed([p['id'] for p in b])))

    def test_onboarding_maps_new_feed_and_refuses_wrong_history(self):
        from onboarding import onboard
        template=json.loads((ROOT/'merchants/kanne.json').read_text());template.update(id='tischwerk',name='Tischwerk')
        rows=[{'sku':'TW-1','title':'Neue Kanne','price_gross_cents':'1190','available_stock':'10','vat_percent':'19'}]
        history=[{'lines':[{'sku':'TW-1','quantity':2,'unit_cents':1190,'tax_rate':19}], 'expected':{'gross':2380,'tax':380,'net':2000}}]
        package,result=onboard(template,rows,history,self.repo);self.assertEqual(result['exact'],1)
        self.assertEqual(Commerce(self.repo).bound_view('tischwerk','shopper')['bindings']['products'][0]['name'],'Neue Kanne')
        template['id']='bad_history';history[0]['expected']['gross']=2400
        with self.assertRaises(ValueError):onboard(template,rows,history,self.repo)
        self.assertNotIn('bad_history',self.repo.tenants())

if __name__=='__main__':unittest.main(verbosity=2)
