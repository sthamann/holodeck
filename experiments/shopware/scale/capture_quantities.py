"""Record quantity increases, decreases and closeout stock limits."""
import argparse
import json
from pathlib import Path

from capture import Oracle, cart_snapshot, seed_product


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    oracle = Oracle(shipping=625)
    pid = seed_product(oracle,19,'Quantity')
    with args.output.open('w') as out:
        for stock in (3,7,15):
            oracle.admin('/product/'+pid,{'stock':stock},'PATCH')
            for case,(initial,requested) in enumerate(((1,2),(2,1),(1,stock),(stock,1),
                    (1,stock+1),(2,stock+7),(2,stock-1),(stock-1,1))):
                session = {}
                def call(quantity, method):
                    return cart_snapshot(oracle.store(session,'/checkout/cart/line-item',{'items':[
                        {'id':pid,'referencedId':pid,'type':'product','quantity':quantity,
                         'stackable':True,'removable':True}]},method))
                before = call(initial,'POST')['lines'][0]['quantity']
                after = call(requested,'PATCH')
                out.write(json.dumps({'split':'test' if case in (5,6) else 'train','stock':stock,
                    'before':before,'requested':requested,'after':after['lines'][0]['quantity'],
                    'errors':after['errors']},separators=(',',':'))+'\n')
                out.flush()


if __name__=='__main__':
    main()
