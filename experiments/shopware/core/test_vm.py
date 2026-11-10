import copy,json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from vm import HERE,Model,Store,evaluate,root_of

class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case=next(c for c in json.loads((HERE/'data/shopware.json').read_text()) if c['split']=='test')
        cls.graph=cls.case['events'][0]['before']; cls.event=cls.case['events'][0]['event']
        cls.model=Model(HERE/'models/adapted')

    def test_integer_rounding_including_large_values_and_signs(self):
        examples=[(1,1,2,1),(-1,1,2,-1),(1,1,-2,-1),(140751,20,120,23459),
                  (2**62-1,2**62-1,2**62-1,2**62-1)]
        for a,b,c,gold in examples:
            expression=['muldiv',['const',a],['const',b],['const',c]]
            self.assertEqual(evaluate(expression,root_of(self.graph),self.graph,self.event),gold)

    def test_atomic_rollback_after_partial_model_execution(self):
        broken=Model(HERE/'models/adapted')
        original=broken.program
        broken.program=lambda event:original(event)+[{'kind':'root','field':'gross','expr':['muldiv',['const',1],['const',1],['const',0]],'head':'test'}]
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(str(Path(tmp)/'db.sqlite')); store.seed(self.graph)
            with self.assertRaises(ValueError):store.apply(self.graph['tenant'],self.event,broken,0)
            self.assertEqual(store.read(self.graph['tenant'],self.graph['root']),self.graph)
            self.assertEqual(store.db.execute('SELECT count(*) FROM receipts').fetchone()[0],0)
            store.close()

    def test_no_target_scope_fallback(self):
        with self.assertRaises(ValueError):self.model.forward(self.graph,{**self.event,'root':'missing'},self.graph['tenant'])
        with self.assertRaises(ValueError):self.model.forward(self.graph,self.event,'wrong-tenant')

    def test_unlearned_events_fail_without_effects(self):
        with self.assertRaises(ValueError):self.model.forward(self.graph,{**self.event,'kind':'never-seen'},self.graph['tenant'])

    def test_same_business_event_is_not_reapplied_after_model_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(str(Path(tmp)/'db.sqlite')); store.seed(self.graph)
            old=Model(HERE/'models/base')
            first=store.apply(self.graph['tenant'],self.event,old,0)
            replay=store.apply(self.graph['tenant'],self.event,self.model,1)
            self.assertEqual(first,replay)
            self.assertEqual(replay['model'],old.identity)
            self.assertEqual(store.read(self.graph['tenant'],self.graph['root'])['revision'],1)
            store.close()

    def test_adaptation_keeps_all_previous_weights(self):
        base=Model(HERE/'models/base')
        for key,value in base.weights.items():
            self.assertTrue((value==self.model.weights[key]).all())
            self.assertEqual(base.meta['heads'][key],self.model.meta['heads'][key])

    def test_runtime_works_without_teacher_or_training_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            for name in ['vm.py','vocabulary.json','worker.py']:
                shutil.copy2(HERE/name,folder/name)
            for suffix in ['.npz','.json']:
                shutil.copy2((HERE/'models/adapted').with_suffix(suffix),(folder/'model').with_suffix(suffix))
            database=str(folder/'db.sqlite');store=Store(database);store.seed(self.graph);store.close()
            request={'database':database,'checkpoint':str(folder/'model'),'tenant':self.graph['tenant'],'event':self.event,'revision':0}
            environment={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
            response=json.loads(subprocess.run([sys.executable,str(folder/'worker.py')],cwd=folder,env=environment,input=json.dumps(request),text=True,capture_output=True,check=True).stdout)
            gold=self.case['events'][0]['after']
            self.assertEqual(response['graph']['objects'],gold['objects'])

if __name__=='__main__':unittest.main()
