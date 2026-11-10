# Standalone Holoshop with trained weights

This records the first standalone trained shop. See the [joint deck](joint-deck.md) for the current Shopware offline experiment and bundled replay.

This page records the commerce checkpoint experiment. The current server adds a learned UI generator to the same weights. [Run the two shop prototype](neural-projection.md)

The local shop completes a purchase through storefront, checkout and merchant admin with the Shopware teacher switched off. It loads a small neural checkpoint and a merchant data file. The previous experiment used exported rule graphs. The current server reads the weights at runtime to decide transitions and select exact integer operations.

## Run the shop

```sh
python3 -m pip install -r requirements.txt
PYTHONPATH=src python3 -m holodeck.server --port 18779
```

Open [the local storefront](http://127.0.0.1:18779/), add a product and complete a test payment. Open [the merchant admin](http://127.0.0.1:18779/?task=admin) with the fixture password `deck-demo`. Start processing the order, save a tracking number, record shipment and close the order. The customer view reads the same durable order. Use a separate `--database` path for a clean run or `--merchant` to provide another catalog under the tested pricing profile.

## What the weights carry

- The transition network learns from Shopware responses which actions succeed and how the recorded state changes. This covers cart mutations, creating an order, payment, shipment, tracking, order processing, completion and the observed return and refund transitions. The input includes the event and the observed state of the affected record. The outputs are scored at runtime from trained weights.
- The arithmetic heads learn from observed values by scoring a shared set of operations over typed input registers. The same vocabulary is available to each field. It includes copying a value, integer arithmetic, comparison by minimum and maximum, and rounded ratios. There are no event specific templates for the arithmetic or hand entered Shopware state transition rules. At runtime the model selects operands and operations, then evaluates them in cents or whole units.
- The transport and storage code maps event names to typed records, enforces access and input types, checks consent against the current quote, prevents negative stock at commit and handles idempotency and SQLite transactions. Product data and shipping configuration live in [the merchant fixture](../src/holodeck/merchant.json). A local payment adapter simulates approval.
- The storefront, checkout and admin are composed per request from a fixed component library. Their layout, labels and form fields are authored. The current state and the network determine which controls appear. The same weights decide whether those actions can execute.

## Evidence

The isolated Shopware 6.7.15.1 teacher recorded **1,120 API observations across 42 synthetic journeys**. It supplied the labels for the checkout and admin lifecycle, including accepted and refused requests. We fit on 28 journeys and held back 14. The supervised replay matches **422 categorical outputs and 398 distinct arithmetic cases** on that held out set. The state and event pairs are from the observed vocabulary. The test varies quantities, prices, initial inventory and the ordering of admin actions.

Nine held out journeys covering 255 actions are also replayed end to end through the transaction runtime. Every step starts from the result of the preceding model action. Two complete journeys use €6.25 shipping, a configuration absent from the training set. The checkpoint contains **11,100 trained parameters**. With the network weights zeroed, the shipment control disappears and the transaction is refused. Zeroing the tax head makes the quote fail closed.

In the browser with Shopware stopped, test order **AD651C8A** contained one Moka Express at **€42.89**, including **€7.99** shipping and **€6.85** tax. The same order showed paid, shipped and completed on the merchant and customer surfaces, with tracking number **MODELL-0042**. The order reduced the local stock from eight to seven. The Shopware port was unreachable and the container remained stopped.

The measured price profile is a gross EUR shop with a uniform 7% or 19% rate, flat shipping and products that cannot oversell. The 20% traces are included as lifecycle training and an arithmetic counterexample. The current tax head misses two of 63 distinct line tax cases at 20%, so the runtime rejects that profile. Payment and refund are simulated. Promotions, catalog editing and a general UI generator are outside this neural experiment.

## Reproduce the fit and replay

```sh
python3 -m pip install -r requirements-training.txt
PYTHONPATH=src python3 experiments/shopware/neural/train.py \
  --trace experiments/shopware/neural/shopware.jsonl \
  --output /tmp/holodeck-weights.npz
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The committed weights have SHA256 `130364bead308a8a9f5b5c1e07c101177820c7f0b3effc635d98b09bc98c52d2`. The included [trace file](../experiments/shopware/neural/shopware.jsonl) contains normalized snapshots with synthetic names and record identifiers stripped. The [collector](../experiments/shopware/neural/capture.py) uses the isolated Store API and Admin API and expects the `ShopVM Kanne` reference sales channel. Training and serving can both run with the teacher offline. The [prior rule graph prototype](https://github.com/sthamann/holodeck/blob/32a21d3e4099855e7ee6c73568771153eb8efd92/docs/standalone-shop.md) records the earlier experiment separately.
