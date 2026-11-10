"""Typed merchant data, separated from the shared learned model. No ERP adapter."""
from __future__ import annotations
import copy,json,sqlite3,time,uuid
from common import ROOT,BASE
from vm import digest

def uid(value):return uuid.uuid5(uuid.NAMESPACE_URL,'shopvm-native/'+value).hex
def now_ms():return time.time_ns()//1000000

class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def build_packages():
    folder=ROOT/'merchants';folder.mkdir(exist_ok=True)
    rows=[('MOKA-3','Moka Express · 3 Tassen','Espressokocher aus Aluminium. Für die kleine Kaffeepause.','moka.jpg',3490,19,40),
          ('MOKA-6','Moka Express · 6 Tassen','Mehr Kaffee für die gemeinsame Pause.','moka-pair.jpg',5490,19,25),
          ('PRESS-1','French Press · 1 Liter','Glaskanne mit Edelstahlfilter. Klarer, voller Kaffee.','press.jpg',2990,19,60)]
    for name,accent,offset in [('Kanne','#214b3f',0),('Werkraum','#284b70',710)]:
        tenant=name.lower();products=[];offers=[]
        for sku,title,description,photo,price,rate,stock in rows:
            identifier=uid(tenant+'/'+sku)
            products.append({'id':identifier,'productNumber':tenant.upper()+'-'+sku,'name':title,'description':description,'taxRate':rate,
                'active':True,'media':[{'url':'/assets/'+photo,'alt':title}],'properties':{'material':'Glas' if sku.startswith('PRESS') else 'Aluminium'},'variants':[]})
            offers.append({'id':identifier,'unit_cents':price+offset,'stock':stock,'version':1,'observed_at_ms':now_ms()})
        package={'schema_version':1,'id':tenant,'name':name,'revision':1,'currency':'EUR','locale':'de-DE',
            'config':{'credit_rate':3 if tenant=='kanne' else 8,'credit_cap':700 if tenant=='kanne' else 1300,
                      'wait_seconds':7200,'shipping_gross':0,'shipping_tax':0},
            'style':{'tokens':{'paper':'#f5f2e9','ink':'#222d29','accent':accent},'tone':'klar und persönlich'},
            'organization':{'roles':{'shopper':['catalog:read','cart:write','order:create'],'merchant':['catalog:read','catalog:write','order:read']}},
            'products':products,'offers':offers,'rules':[],
            'provenance':'synthetic merchant package with existing licensed product photographs; no real customer data; no ERP',
            'field_mapping':{'sku':'productNumber','title':'name','price_gross_cents':'unit_cents','available_stock':'stock','vat_percent':'taxRate'}}
        (folder/(tenant+'.json')).write_text(json.dumps(package,ensure_ascii=False,indent=2))

class Repository:
    def __init__(self,path=None):
        self.path=str(path or ROOT/'state/merchant.sqlite');__import__('pathlib').Path(self.path).parent.mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS packages(tenant TEXT PRIMARY KEY,revision INTEGER NOT NULL,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS products(tenant TEXT,id TEXT,sku TEXT,value TEXT,price INTEGER CHECK(price>=0),stock INTEGER CHECK(stock>=0),version INTEGER,observed INTEGER,PRIMARY KEY(tenant,id),UNIQUE(tenant,sku));
            CREATE TABLE IF NOT EXISTS contexts(token TEXT PRIMARY KEY,tenant TEXT,customer TEXT,cart TEXT,revision INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS orders(tenant TEXT,id TEXT,token TEXT,value TEXT,graph TEXT,PRIMARY KEY(tenant,id));
            CREATE TABLE IF NOT EXISTS receipts(tenant TEXT,key TEXT,request TEXT,value TEXT,PRIMARY KEY(tenant,key));
            CREATE TABLE IF NOT EXISTS changes(sequence INTEGER PRIMARY KEY AUTOINCREMENT,tenant TEXT,entity TEXT,id TEXT,version INTEGER,created INTEGER);
            ''')
            # Preserve existing orders while allowing later carts in one customer context.
            schema=db.execute("SELECT sql FROM sqlite_master WHERE name='orders'").fetchone()[0]
            if 'UNIQUE(tenant,token)' in schema:
                db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE orders_migrated(tenant TEXT,id TEXT,token TEXT,value TEXT,graph TEXT,PRIMARY KEY(tenant,id));
                INSERT INTO orders_migrated SELECT * FROM orders;
                DROP TABLE orders;
                ALTER TABLE orders_migrated RENAME TO orders;
                COMMIT;''')

    def connect(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None,factory=Connection);db.row_factory=sqlite3.Row;db.execute('PRAGMA journal_mode=WAL');return db

    def tenants(self):
        with self.connect() as db:return [r[0] for r in db.execute('SELECT tenant FROM packages ORDER BY tenant')]

    def install(self,package,replace=False):
        if package.get('schema_version')!=1:raise ValueError('unknown package schema')
        products={p['id']:p for p in package['products']}
        for offer in package['offers']:
            if offer['id'] not in products:raise ValueError('offer without product')
            if any(type(offer[k]) is not int or offer[k]<0 for k in ['unit_cents','stock','version']):raise ValueError('invalid typed offer')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exists=db.execute('SELECT 1 FROM packages WHERE tenant=?',(package['id'],)).fetchone()
            if exists and not replace:db.rollback();return False
            if exists:raise ValueError('explicit migration required for replacing an installed package')
            record=copy.deepcopy(package);record.pop('offers')
            db.execute('INSERT INTO packages VALUES (?,?,?)',(package['id'],package['revision'],json.dumps(record)))
            for offer in package['offers']:
                p=products[offer['id']]
                db.execute('INSERT INTO products VALUES (?,?,?,?,?,?,?,?)',(package['id'],p['id'],p['productNumber'],json.dumps(p),offer['unit_cents'],offer['stock'],offer['version'],offer['observed_at_ms']))
            db.commit();return True

    def package(self,tenant,db=None):
        if db is None:
            with self.connect() as con:return self.package(tenant,con)
        row=db.execute('SELECT value FROM packages WHERE tenant=?',(tenant,)).fetchone()
        if row is None:raise ValueError('unknown merchant')
        return json.loads(row[0])

    def products(self,tenant,db=None):
        if db is None:
            with self.connect() as con:return self.products(tenant,con)
        return [dict(json.loads(r['value']),_offer={'unit_cents':r['price'],'stock':r['stock'],'version':r['version'],'observed_at_ms':r['observed']}) for r in db.execute('SELECT * FROM products WHERE tenant=? ORDER BY sku',(tenant,))]

    def update(self,tenant,identifier,change,expected_version=None):
        allowed={'unit_cents','stock','name','description'}
        if set(change)-allowed:raise ValueError('unsupported change')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM products WHERE tenant=? AND id=?',(tenant,identifier)).fetchone()
            if row is None:raise ValueError('unknown product')
            if expected_version is not None and row['version']!=expected_version:raise ValueError('version conflict')
            value=json.loads(row['value']);price=change.get('unit_cents',row['price']);stock=change.get('stock',row['stock'])
            if type(price) is not int or type(stock) is not int or min(price,stock)<0:raise ValueError('invalid price or stock')
            for field in ['name','description']:
                if field in change:value[field]=str(change[field])
            version=row['version']+1;stamp=now_ms()
            db.execute('UPDATE products SET value=?,price=?,stock=?,version=?,observed=? WHERE tenant=? AND id=?',(json.dumps(value),price,stock,version,stamp,tenant,identifier))
            db.execute('INSERT INTO changes(tenant,entity,id,version,created) VALUES (?,?,?,?,?)',(tenant,'product',identifier,version,stamp))
            db.commit();return {'id':identifier,'version':version,'observed_at_ms':stamp}

    def ensure_context(self,tenant,token=None,db=None):
        if db is None:
            with self.connect() as con:return self.ensure_context(tenant,token,con)
        if token:
            row=db.execute('SELECT * FROM contexts WHERE token=?',(token,)).fetchone()
            if row is None or row['tenant']!=tenant:raise ValueError('unknown context or merchant mismatch')
            return dict(row)
        token=uuid.uuid4().hex
        db.execute('INSERT INTO contexts(token,tenant,customer,cart) VALUES (?,?,?,?)',(token,tenant,None,json.dumps({'items':{},'quote':None,'generation':uuid.uuid4().hex})))
        return dict(db.execute('SELECT * FROM contexts WHERE token=?',(token,)).fetchone())

    def catalog_version(self,tenant):
        with self.connect() as db:
            return db.execute('SELECT coalesce(max(sequence),0) FROM changes WHERE tenant=?',(tenant,)).fetchone()[0]

def install_demo_packages():
    repository = Repository()
    for path in sorted((ROOT / "merchants").glob("*.json")):
        repository.install(json.loads(path.read_text()))
    return repository


if __name__ == "__main__":
    install_demo_packages()
