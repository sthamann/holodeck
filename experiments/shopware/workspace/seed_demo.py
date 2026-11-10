"""Seed the third merchant using the same transactions as the serving deck."""
import argparse
from pathlib import Path
from uuid import uuid4

from holodeck.neural_shop import NeuralDeck
from holodeck.runtime import Ledger, digest
from holodeck.server import HERE, test_payment

ADMIN = {'id':'seed','role':'admin'}
DEMO = (
    ('Nordwerk Café','NL-B04',2,'paid','open'),
    ('Hafenservice','NL-A01',1,'paid','open'),
    ('Kranhaus Küche','NL-D12',1,'failed','open'),
    ('Lichtwerk Hotel','NL-C08',1,'paid','shipped'),
    ('Kiesel Kantine','NL-D12',1,'paid','shipped_partially'),
)


def seed(deck, ledger):
    if ledger.read()['orders']:
        raise ValueError('Start with an empty database.')
    result = []
    for index,(name,sku,quantity,payment,delivery) in enumerate(DEMO,1):
        buyer = {'id':f'seed-buyer-{index}','role':'buyer'}
        ledger.apply('cart.add',buyer,{'sku':sku,'quantity':quantity},uuid4().hex)
        state = ledger.read()
        quote = deck.quote(state['catalog'],state['carts'][buyer['id']]['items'])
        created = ledger.apply('checkout.place',buyer,{'name':name,'email':f'seed-{index}@example.test',
            'address':f'Testweg {index}, 48624 Schöppingen','consent':True,'quote_key':digest(quote)},uuid4().hex)
        oid = created['result']['order_id']
        if payment=='failed':
            ledger.settle(lambda work:('order_transaction.fail',{'order_id':work['order_id']}))
        else:
            ledger.settle(test_payment)
        if delivery!='open':
            event = 'order_delivery.ship' if delivery=='shipped' else 'order_delivery.ship_partially'
            ledger.apply(event,ADMIN,{'order_id':oid},uuid4().hex)
        result.append(oid)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--checkpoint',type=Path,default=HERE/'workspace.npz')
    args = p.parse_args()
    args.database.parent.mkdir(parents=True,exist_ok=True)
    deck = NeuralDeck(args.checkpoint,HERE/'nordlager.json')
    ledger = Ledger(args.database,deck,deck.initial)
    for oid in seed(deck,ledger):
        o = ledger.read()['orders'][oid]
        print(oid[:8],o['name'],o['quote']['gross'],o['payment'],o['delivery'])


if __name__=='__main__':
    main()
