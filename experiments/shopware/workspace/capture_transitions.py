"""Probe live Shopware state transitions on isolated fixture orders."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from uuid import uuid4

from capture import Oracle

ENTITIES = ('order','order_transaction','order_delivery')


def seed(oracle):
    pid = uuid4().hex
    sku = 'HOLO-' + pid[:7].upper()
    oracle.sync('product',[{'id':pid,'productNumber':sku,'name':'Holodeck Referenzteil '+sku,
        'active':True,'isCloseout':True,'stock':90,'taxId':oracle.tax[19],
        'price':[{'currencyId':oracle.channel['currencyId'],'gross':79.90,'net':67.14,'linked':True}],
        'visibilities':[{'salesChannelId':oracle.channel['id'],'visibility':30}]}])
    session = {}
    oracle.register(session)
    oracle.store(session,'/checkout/cart/line-item',{'items':[
        {'id':pid,'referencedId':pid,'type':'product','quantity':1,'stackable':True,'removable':True}]})
    order = oracle.store(session,'/checkout/order',{})
    detail = oracle.order(order['id'])
    record = oracle.search('order',{'ids':[order['id']]})[0]
    return {'order':detail['ids'],'number':record['orderNumber'],'product':pid,'sku':sku}


def inventory(oracle):
    by_name = {r['technicalName']:r['id'] for r in oracle.search('state-machine',{'limit':100})}
    states = oracle.search('state-machine-state',{'limit':200})
    transitions = oracle.search('state-machine-transition',{'limit':200})
    result = {}
    for entity in ENTITIES:
        machine = by_name[entity+'.state']
        group = [r for r in states if r['stateMachineId']==machine]
        ids = {r['id'] for r in group}
        actions = sorted({r['actionName'] for r in transitions if r['fromStateId'] in ids})
        result[entity] = ([(r['id'],r['technicalName']) for r in group],actions)
    return result


def current(oracle, entity, oid):
    return oracle.search(entity.replace('_','-'),{'ids':[oid]})[0]['stateId']


def reset_fixture(container, entity, oid, sid):
    # State fixture setup only. The observed action always uses the real Admin API.
    if entity not in ENTITIES or any(len(x)!=32 or any(c not in '0123456789abcdef' for c in x) for x in (oid,sid)):
        raise ValueError('Invalid fixture identity')
    env = {**os.environ,'DOCKER_API_VERSION':'1.43'}
    command = ['docker','exec','-u','root',container,'mysql','-h','127.0.0.1','-uroot','-proot','-N','shopware',
               '-e',f'UPDATE `{entity}` SET state_id=UNHEX("{sid}") WHERE id=UNHEX("{oid}")']
    subprocess.run(command,env=env,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)


def probe(oracle, model, split, seed_path, container):
    ids = seed(oracle)
    seed_path.write_text(json.dumps(ids))
    observations = []
    for entity,(states,actions) in model.items():
        oid = ids['order'][entity]
        for sid,before in states:
            for action in actions:
                if current(oracle,entity,oid)!=sid:
                    reset_fixture(container,entity,oid,sid)
                if current(oracle,entity,oid)!=sid:
                    raise ValueError(f'Failed to set up {entity} {before}')
                try:
                    oracle.admin(f'/_action/{entity}/{oid}/state/{action}',{})
                    status = 200
                except HTTPError as error:
                    status = error.code
                    error.read()
                    if status not in (400,409):
                        raise
                after = oracle.states[current(oracle,entity,oid)]
                observations.append({'split':split,'event':entity+'.'+action,'before':before,'after':after,'status':status})
    return observations


CHAIN_REQUESTS = (
    ('order.complete','order_transaction.fail','order_transaction.reopen','order_transaction.paid',
     'order.process','order_delivery.ship_partially','order_delivery.ship','order.complete'),
    ('order_transaction.paid','order.process','order_delivery.ship','order_delivery.reopen',
     'order_delivery.ship','order.complete','order_delivery.retour','order_transaction.refund'),
    ('order_delivery.retour','order_transaction.refund','order_transaction.fail','order_transaction.fail',
     'order_transaction.paid','order.process','order_delivery.ship','order.complete'),
    ('order_transaction.paid','order_delivery.ship_partially','order_delivery.reopen',
     'order_delivery.ship','order.process','order.complete','order_delivery.retour','order_transaction.refund'),
)


def chain(oracle, requested):
    fixture = seed(oracle)
    order = fixture['order']['order']
    rows = []
    for event in requested:
        entity,action = event.split('.',1)
        before = oracle.order(order)
        try:
            oracle.admin(f'/_action/{entity}/{fixture["order"][entity]}/state/{action}',{})
            status = 200
        except HTTPError as error:
            status = error.code
            error.read()
            if status not in (400,409):
                raise
        after = oracle.order(order)
        select = lambda record: [record[k] for k in ('stage','payment','delivery')]
        rows.append({'event':event,'status':status,'before':select(before),'after':select(after)})
    return {'totals':before['totals'],'events':rows}



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fixture',type=Path)
    parser.add_argument('--container',default='holodeck-ui-teacher-capture')
    parser.add_argument('--seed-only',action='store_true')
    parser.add_argument('--chains-only',action='store_true')
    args = parser.parse_args()
    oracle = Oracle(shipping=625)
    if args.seed_only:
        if args.fixture is None:
            parser.error('--fixture is required for seeding')
        args.fixture.write_text(json.dumps(seed(oracle)))
        return
    if args.chains_only:
        with ThreadPoolExecutor(max_workers=2) as pool, args.output.open('w') as target:
            for row in pool.map(lambda requested:chain(oracle,requested),CHAIN_REQUESTS):
                target.write(json.dumps(row,separators=(',',':'))+'\n')
        return
    scope = inventory(oracle)
    print({entity:(len(states),len(actions)) for entity,(states,actions) in scope.items()},flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool, args.output.open('w') as target:
        for rows in pool.map(lambda split:probe(oracle,scope,split,args.output.with_suffix('.'+split+'.fixture.json'),args.container),
                             ('train','test')):
            target.writelines(json.dumps(row,separators=(',',':'))+'\n' for row in rows)
            target.flush()
            print(rows[0]['split'],len(rows),flush=True)


if __name__=='__main__':
    main()
