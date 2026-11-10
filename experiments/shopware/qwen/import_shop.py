"""Import a new Shopware sales channel and record independent oracle outcomes."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scale'))
from capture import Oracle, cart_snapshot, cents, prices, promotion, relation, step


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('output',type=Path)
    args=cli.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    rate=19
    oracle=Oracle(shipping=825)
    name='Rösterei Hafenlicht'
    oracle.admin('/sales-channel/'+oracle.channel['id'],{'name':name},'PATCH')
    seed=uuid4().hex[:7].upper()
    products=[]
    for i,(title,price,stock) in enumerate((('Edelstahlfilter Nord',5375,14),
                                          ('Aromakaraffe Hafen',2850,9),
                                          ('Espressoset Düne',7920,6))):
        pid=uuid4().hex
        products.append({'id':pid,'productNumber':f'HL-{seed}-{i+1}','name':title,'active':True,
            'isCloseout':True,'stock':stock,'taxId':oracle.tax[rate],
            'price':prices(oracle,rate,price),
            'visibilities':[{'salesChannelId':oracle.channel['id'],'visibility':30}]})
    oracle.sync('product',products)
    ids=[p['id'] for p in products]
    codes={seed+'P':('percentage',23),seed+'A':('absolute',675)}
    for code,(kind,value) in codes.items():
        promotion(oracle,code,kind,value)
    found=oracle.search('product',{'ids':ids})
    source=oracle.search('sales-channel',{'ids':[oracle.channel['id']]})[0]
    catalog={p['productNumber']:{'sku':p['productNumber'],'name':p['name'],'price':cents(p['price'][0]['gross']),
        'stock':p['stock'],'rate':rate,'active':p['active'],'variant':'','description':'','image':'press.jpg'}
        for p in found}
    promotions={}
    for code in codes:
        row=oracle.search('promotion',{'filter':[{'type':'equals','field':'code','value':code}],
                                      'associations':{'discounts':{}}})[0]
        discount=row['discounts'][0]
        mode=discount['type']
        promotions[code]={'active':row['active'],'mode':mode,
                          'value':int(discount['value']) if mode=='percentage' else cents(discount['value'])}
    package={'brand':{'name':source['name'],'tone':'dense','theme':'werkraum'},'rate':rate,
             'shipping':oracle.shipping,'promotions':promotions,'catalog':catalog}
    (args.output/'merchant.json').write_text(json.dumps(package,ensure_ascii=False,indent=2)+'\n')
    reference=[]
    for i,code in enumerate(codes):
        session={}
        oracle.register(session)
        requested=((0,2),(1,1)) if i==0 else ((2,1),)
        items=[{'id':ids[ix],'referencedId':ids[ix],'type':'product','quantity':quantity,
                'stackable':True,'removable':True} for ix,quantity in requested]
        before=cart_snapshot(oracle.store(session,'/checkout/cart/line-item',{'items':items}))
        after=cart_snapshot(oracle.store(session,'/checkout/cart/line-item',{'items':[
            {'type':'promotion','referencedId':code}]}))
        order=oracle.store(session,'/checkout/order',{})
        payment=relation(oracle,order,'order_transaction')
        delivery=relation(oracle,order,'order_delivery')
        paid,_=step(oracle,'order_transaction',payment['id'],payment['stateId'],'paid')
        ship,_=step(oracle,'order_delivery',delivery['id'],delivery['stateId'],'ship')
        reference.append({'items':{products[ix]['productNumber']:n for ix,n in requested},'code':code,
            'before':before,'after':after,'order':{'gross':cents(order['amountTotal']),
            'net':cents(order['amountNet']),'payment':paid,'shipping':ship}})
    (args.output/'reference.json').write_text(json.dumps(reference,ensure_ascii=False,indent=2)+'\n')
    print(source['name'],len(catalog),'imported products',len(promotions),'imported policies')
    print('Independent Shopware order totals:',[r['order']['gross'] for r in reference])


if __name__=='__main__':
    main()
