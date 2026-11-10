# First standalone Holoshop

The first local prototype completes a purchase and merchant fulfilment on one learned checkpoint and one durable record. The deck composes the storefront, checkout, merchant workspace and order status from the current state on every projection request. The HTML shell renders components and sends the actions produced by the deck.

## Run it

From the repository root, with Python 3.11 or newer:

```sh
PYTHONPATH=src python3 -m holodeck.server --port 18779
```

Open [the local storefront](http://127.0.0.1:18779/). Select a product, proceed to checkout and confirm a test payment. The demo promotion code is `KAFFEE10`. Open **Verwaltung** with the fixture password `deck-demo`, then record shipment and delivery. The customer’s **Bestellung** shows the same status and tracking number. Use a separate `--database` path for an empty run.

The local shop uses synthetic data, gross EUR prices, one shipping policy and simulated payment outcomes. The admin supports price and stock editing, shipment and tracking, delivery, cancellation and a complete return with an inspection outcome. The single-merchant fixture and test payment adapter define the verified scope.

## What the deck learns

- The exact pricing specialist is exported from the published Shopware-trained model. The export was checked against 42 recorded Shopware pricing transitions, including 12 from the original test split.
- The learner selects rules for promotion and shipping and fits conditions and effect graphs for nine actions. The lifecycle cases come from a separate synthetic reference teacher. They are distinct from the Shopware pricing traces.
- Projection cases teach the deck when components apply. During serving, the checkpoint binds components to current records and filters controls with the same learned conditions used for execution. The first order changes the composition and available actions, including the shift from checkout to receipt and from shipment to delivery.
- The generic runtime evaluates the selected expressions exactly and commits records, receipts and outgoing work together. The server loads the checkpoint and database. Training code and Shopware are outside the serving path.

The model uses discrete selection over a bounded grammar of candidate graphs and a component library. The available graph structure and component vocabulary are explicit in the teaching code. The fit exercises 7,020 separately generated action cases under the same policy family, plus pricing and projection cases. This verifies the defined commerce slice. [Training source](../src/holodeck/teach.py) · [Checkpoint](../src/holodeck/checkpoint.json)

## Observed with Shopware off

The isolated Shopware 6.7.15.1 container was stopped before the run and its local HTTP endpoint was unreachable. The standalone deck used checkpoint `3e60170717ef`.

In the browser, a Moka Express was added to the cart and `KAFFEE10` was applied. The generated checkout showed €31.41 in goods, €4.90 shipping, €5.80 included VAT and €36.31 total. Test order **08F1B957** was paid, then shipped and marked delivered in the admin. The customer view showed **Bezahlt**, **Zugestellt** and tracking reference **DECK-TEST-002** for the same order.

Four acceptance tests use the actual HTTP handler. They cover the full flow and return, refused empty or stale checkout, declined payment with stock restored, stable retries, buyer and admin separation, a restart with pending work, checkpoint binding and disabling a learned action. They also verify admin search and paging with 513 records. The original Shopware experiment replay continues to match 168 of 168 recorded transitions.

## Rebuild and verify

```sh
python3 -m pip install -r requirements.txt
PYTHONPATH=src python3 -m holodeck.teach --output /tmp/holodeck-checkpoint.json
cmp src/holodeck/checkpoint.json /tmp/holodeck-checkpoint.json
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 experiments/shopware/replay.py
```

The fit is deterministic for the included fixture and numeric checkpoint. The serving process itself uses the Python standard library. The current proof covers the local shop and its defined policies. Further Shopware coverage and larger model training remain part of the [roadmap](roadmap.md).
