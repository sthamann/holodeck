"""Record fresh two-line basket checks outside the main training set."""
import argparse
import json
from pathlib import Path
import random
from uuid import uuid4

from capture import Oracle, cart_snapshot, prices, promotion, seed_product


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rounding',action='store_true',help='Capture cent-scale rounding boundaries')
    args = parser.parse_args()
    oracle = Oracle(shipping=799)
    prefix = 'PAIR'+uuid4().hex[:7].upper()
    policies = (('percentage',100),('absolute',1750)) if args.rounding else (('percentage',23),('absolute',1750))
    for mode,value in policies:
        promotion(oracle,prefix+mode.upper(),mode,value)
    with args.output.open('w') as stream:
        for rate in (7,19):
            ids = [seed_product(oracle,rate,i) for i in range(2)]
            for n in range(8 if args.rounding else 24):
                rng = random.Random(9102+rate*10+n)
                unit = [rng.randint(400,11500) for _ in ids]
                qty = [rng.randint(1,4) for _ in ids]
                if args.rounding:
                    unit, qty = [(3,3),(3,7),(1,2),(5,1)][n//2], [1,1]
                for pid,price in zip(ids,unit,strict=True):
                    oracle.admin('/product/'+pid,{'price':prices(oracle,rate,price)},'PATCH')
                session = {}
                cart = oracle.store(session,'/checkout/cart/line-item',{'items':[
                    {'id':pid,'referencedId':pid,'type':'product','quantity':q,'stackable':True,'removable':True}
                    for pid,q in zip(ids,qty,strict=True)]})
                before = cart_snapshot(cart)
                mode,value = policies[0 if n%2 else 1]
                cart = oracle.store(session,'/checkout/cart/line-item',{'items':[
                    {'type':'promotion','referencedId':prefix+mode.upper()}]})
                stream.write(json.dumps({'case':n,'rate':rate,'units':unit,'quantities':qty,'shipping':oracle.shipping,
                    'mode':mode,'value':value,'before':before,'after':cart_snapshot(cart)},separators=(',',':'))+'\n')
                stream.flush()


if __name__=='__main__':
    main()
