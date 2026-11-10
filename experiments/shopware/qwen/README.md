# Qwen adapter test against an independently imported shop

This is a separate experiment from the [current joint deck](../../../docs/joint-deck.md). The joint deck uses frozen Qwen features for Admin requests and trained sparse weights for commerce and projection.

The experiment compares the official Qwen3.5-9B-Base snapshot with the same snapshot plus a LoRA adapter. Both arms use the same request template, greedy decoding, structural JSON prefix, typed program interpreter, component renderer, and SQLite ledger. An invalid or incomplete checkout plan fails closed. This is a local test shop with simulated payments.

## Recorded result

With the typed runtime, the base model produced no executable checkout in either of the two imported-shop cases and failed the independent Admin test. The 96-step adapter passed the fixed coupon checkout and the independent Admin test. Its generated fixed coupon expression also matched 177 of 177 archived Shopware holdout totals when replayed unchanged. That replay tests the emitted program, not fresh model generation.

The browser run of the 96-step adapter completed the fixed coupon order for €80.70 and shipped that same order from a generated workspace. The percentage case exposed a unit confusion. The initial adapter treated a 23 percent coupon as 23 cents. The current arithmetic interpreter rejects that program because its operands have different units. The subsequent 48-step run with separate pricing generation preserved the working admin but failed both checkout checks, so the demo uses the first adapter.

## Teaching data

`prepare.py` uses only the training partition of the existing Shopware 6.7.15.1 capture. It fits the shortest consistent expressions in a generic arithmetic pool against the recorded cart totals and promotion lines. These executable expressions supervise Qwen. Checkout UI components are annotated. Admin component columns and the shipment binding are grounded in the native Shopware browser capture, with alternate request wording and layout targets added as supervision.

There are 2,328 training and 129 validation prompt/completion pairs in the first run. The optional pricing pass adds task-specific arithmetic examples from the same source pool. Prepared pairs and optimizer draws are distinct. With a batch size of two, 96 steps draw 192 examples. The 48-step pricing pass draws another 96 examples.

`import_shop.py` creates a separate Shopware sales channel with synthetic records and reads its product IDs, names, prices, stock, shipping and coupon configuration back through Shopware. It records two new reference journeys separately. The runtime receives the merchant file only. The comparison script also reads the separate reference outcomes as assertions. The catalog's images are local placeholders.

## Execution boundary

For the tested checkout, Qwen generates discount arithmetic in the typed expression language and the actual UI component list and action bindings. For the requested admin, Qwen generates the table, filters, sort and row action. The existing Holodeck deck supplies undiscounted amounts, eligibility, test payment, stock and order effects, and the status transition. The generic ledger checks permissions, state versions and idempotency. The checkout is tested against independent Shopware totals before the benchmark submits it. Expression checks establish units and accounting invariants and the reference traces establish exact pricing on the measured cases.

The isolated Admin comparison uses the same normalized, teacher-derived orders in both arms. A separate test takes an actual order from the generated checkout to the requested Admin workspace and uses the generated shipping action. The benchmark tests an unseen merchant and new request wording within the same observed business task families.

## Reproduce locally

Use Apple Silicon, `mlx-lm==0.32.2` and the pinned official Qwen revision `68c46c4b3498877f3ef123c856ecfde50c39f404`. Provide a local snapshot path in `base` and use a dedicated directory for the model output, datasets and local records.

```sh
lab=.holodeck/qwen-lab
PYTHONPATH=src python experiments/shopware/qwen/prepare.py "$lab/data"
mlx_lm.lora --config experiments/shopware/qwen/fit.yaml --model "$base" \
  --data "$lab/data" --adapter-path "$lab/adapter"
```

Set `HOLODECK_TEACHER_URL` to an isolated loopback instance before importing. Stop that instance after creating the merchant file and reference outcomes. The next commands should run with the teacher unreachable.

```sh
PYTHONPATH=src python experiments/shopware/qwen/import_shop.py "$lab/imported"
# Stop the isolated Shopware instance here.
PYTHONPATH=src python experiments/shopware/qwen/evaluate.py --model "$base" \
  --shop "$lab/imported" --output "$lab/base-typed.json"
PYTHONPATH=src python experiments/shopware/qwen/evaluate.py --model "$base" \
  --adapter "$lab/adapter" --shop "$lab/imported" --output "$lab/adapter-typed.json"
```

To open the local model-generated shop, run:

```sh
PYTHONPATH=src python experiments/shopware/qwen/serve.py --model "$base" \
  --adapter "$lab/adapter" --merchant "$lab/imported/merchant.json" \
  --database "$lab/demo.sqlite" --port 18783
```

Open [the shop](http://127.0.0.1:18783/) or [Admin](http://127.0.0.1:18783/?task=admin). The test Admin passphrase is `deck-demo`. The coupon codes and imported values are in the generated merchant file.


For the separate pricing follow-up, prepare data with `--refine-pricing`. Resume from the first adapter using `--resume-adapter-file` and train for 48 steps in another adapter directory. Use `--separate-pricing` in both evaluation arms. This was an independent comparison with the same reserved merchant and no new training labels from it.
