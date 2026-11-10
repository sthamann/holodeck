# How a deck learns a business

**Current evidence.** The [joint deck](joint-deck.md) fits sparse selection weights over executable cells and uses a frozen Qwen encoder for language features. Its numeric refit uses 18 Shopware journeys to choose a source informed arithmetic hypothesis, carries forward the earlier commerce and projection supervision, and tests four new shops. An [independent LoRA run](../experiments/shopware/qwen/README.md) trains Qwen itself on a narrower checkout and Admin task and documents a failure on the percentage coupon. The learning plan below separates those results from the work still needed for shared multimodal training.

The [architecture](architecture.md) gives the model responsibility for commercial decisions. The learning plan makes that responsibility observable. The first learner must determine which data a decision needs, which exact operations apply, which state changes belong together and which event the business is waiting for.

The [trace induction experiment](trace-induction.md) also shows a different kind of learning. A compiler builds new expressions from observed results, while the sparse selector weights stay fixed. Separate fits follow two teacher semantics.

## The first curriculum is a complete shop

Teach the standard commerce functions in the order a working shop needs them. Start with catalog, variants, availability, cart and pricing. Add checkout, payment and merchant order handling on the same model and records. Connect each learned decision to the model-generated controls that let a customer or merchant act on it.

Capture linked buyer and merchant traces rather than disconnected answers. A training case includes the event, the state and applicable policy, the allowed actions, durable changes and any continuation. Reference outcomes check the business decisions. Projection examples teach the model to turn those decisions into usable surfaces. The first shop is accepted only after it completes the full journey with fresh holos and the teacher offline. [First delivery and acceptance](roadmap.md#first-delivery-and-acceptance)

## Learn from executions

Start with synthetic merchants in an isolated Shopware installation. Vary tax configuration, prices, discounts, quantities, stock, customer conditions and permitted transitions. Capture both accepted and rejected requests. Keep the inputs, the relevant state before and after, the actual effects, and the whole ordered trajectory. Use separate merchants and rule combinations for development and evaluation.

Source code and tests help inventory behaviours and propose hypotheses. Executed reference outcomes determine which hypotheses survive. Record where Shopware supplies an outcome and where a separately specified test policy supplies it. A visual example labels composition and bindings. Commercial correctness needs its own evidence.

## From traces to a model graph

1. Normalize identities and extract actual changes and unchanged fields. Treat created objects as typed records with references. Preserve event order and related writes.
2. Enumerate bounded candidate expressions and guards over the frozen typed vocabulary. Infer candidate state and continuation graphs from complete trajectories. Use negative cases to eliminate graphs that produce the right totals for invalid actions.
3. Learn operand pointers, operator selections, guards and transition selections. Exact execution scores each candidate against the reference. For a small candidate set, train categorical selections against the set of candidates consistent with the reference rather than assuming one unique syntax.
4. Distil a stable selection into a readable graph and verify its execution. Counterexamples refine the candidate set. Record which learned parameters produced the selected graph.
5. Bind the action readout to the executed guard and the projection to the action readout. A rendered refund button must refer to the actual refund decision.

A model can simulate an operation by running the same graph without committing its proposal. This lets a new interface explain a price or preview a change using the same business semantics as execution.

The joint deck implements these steps for a bounded grammar and the observed state, effect and projection vocabulary. It uses supervised sparse selections and tests emitted programs against independent traces. Broader rule families and free composition over multimodal input are future work.

## Connect understanding and execution

The first Mac milestone trains a small business model against exact state and event data. The next model connects text and image features to the same typed representations and learned selections. Cached foundation-model features are useful for the initial experiment. Trainable parameters and their actual use must be recorded separately from cached features and retrieved examples.

On the H200 system, resolve the trainable foundation checkpoint and tokenizer first. Compare a frozen encoder with trained business heads to a shared adapter experiment. Start with a small supervised run over aligned intent, graph, outcome, continuation and projection examples. Choose checkpoints using held-out executable trajectories. Where LoRA is appropriate, publish the exact target modules and adapter configuration with the result. [LoRA paper](https://arxiv.org/abs/2106.09685).

The task loss can score graph and action predictions alongside execution-derived labels for effects and continuation. Exact arithmetic runs in the typed graph. A floating-point approximation of a price is never a committed output. Distinguish retrieval failure, wrong operand selection, wrong policy, wrong continuation and wrong UI binding.

## The first policy adaptation

This adaptation follows acceptance of the standard shop. The first release learns an ordinary, complete purchase and merchant journey. This experiment then changes a policy inside that working deck.

Initially the return flow waits for inspection before a refund. Teach a new policy in which a configured customer attribute and amount limit permit an earlier refund after confirmed return handover. Stock is restored after confirmed resale eligibility. The policy needs several dependent decisions and keeps monetary and physical outcomes separate.

Test threshold equality, missing attributes, changed quantities, earlier refunds, duplicate notifications and items that cannot be resold. Use new merchant values and names. Keep the host and primitive vocabulary fixed.

| Variant | What changes |
|---|---|
| Base model | No new context, graph or learned parameters |
| Base with retrieval | Adds the same examples available to the other context-conditioned variants |
| Direct rule synthesis | Produces an executable candidate with the same vocabulary and checking budget |
| Graph adaptation | Changes the learned executable structure |
| Parameter adaptation | Changes the identified weights and measures their contribution with equal context |

Model training receives credit only for the improvement attributable to its intervention. Compare whole held-out trajectories and unchanged earlier abilities. If direct rule synthesis ties, report the tie. The existing service credit experiment already observed one.

## Improve in service

When an observed failure exposes a capability gap, retain a concrete counterexample and an independent expectation. Distinguish a missing rule from a missing fact, a retrieval miss or an impossible request. The example that triggered learning enters development. A separate set demonstrates whether the new checkpoint improves.

Save the learned structure, provenance, checked scope and activation history. Restore the running system and confirm the real consumer uses the new checkpoint. Test the previous checkpoint as well. Preserve accepted agreements and pin active continuations until a checked migration exists.

The ablation test is essential. Deactivate the relevant learned structure and verify that the matching commercial ability disappears. If a hidden host handler still performs the task, the experiment has located more business code to move into the model.

## One specialist from training to activation

![A tested service specialist is attached to a running order and restored after restart.](../assets/illustrations/joint-deck-learning.svg)

The published service specialist was fitted from 52 observed commands over eight synthetic orders in two teaching shops. It learns which payment and delivery state changes are accepted, refused or accepted without a write. The service language readout uses frozen Qwen features. The numeric joint checkpoint keeps its existing commerce and projection cells.

On the independent holdout, the numeric deck alone completes 0 of 16 service journeys. With the specialist attached, it matches all 16. All 44 expected writes and all 36 attempts without a write agree with the reference. The earlier 24 journeys still emit the same 144 programs. [Recorded comparison](../experiments/shopware/evidence/joint-deck/summary.json)

### What changes on the live order

The browser creates order `FC890D92` in Birkenkai and marks it paid. It then makes the same service request on each version. Both views show the same order, line items and open delivery. After the explicit activation, the view offers full and partial refund status changes. A premature return remains unavailable. The activation itself can be retried and returns the original receipt.

| Before activation | After activation |
| --- | --- |
| [![Birkenkai paid order without refund controls.](../assets/experiments/joint-deck/birkenkai-before-activation.png)](../assets/experiments/joint-deck/birkenkai-before-activation.png) | [![Same paid order now offering full and partial refund status controls.](../assets/experiments/joint-deck/birkenkai-after-activation.png)](../assets/experiments/joint-deck/birkenkai-after-activation.png) |

The test continues through shipment, return and refund status changes. An offer generated by the old version is rejected. After stopping and restarting the server on the same SQLite file, the same four orders resume with their final state, workflow record and activated checkpoint. These are simulated status changes. Training runs offline, and activation is explicit in the local test interface. [Activation and restart report](../experiments/shopware/evidence/joint-deck/activation-report.json)

### Reproduce the activation

Use the Apple Silicon environment and pinned Qwen snapshot from the [joint deck setup](joint-deck.md#replay-the-published-checkpoints). Run from the repository root with Shopware offline.

```sh
bench=experiments/shopware/benchmark
proof=experiments/shopware/evidence/joint-deck
out=$(mktemp -d)
base=/path/to/the/pinned/Qwen/snapshot

python "$bench/joint_shop.py" import --source "$proof/traces/service-holdout.jsonl" \
  --catalog "$out/shops.json" --case Birkenkai:0 --case Sandfracht:0 \
  --case Lindenlager:0 --case Werksand:0 --teacher-port 55729
python "$bench/joint_shop.py" serve --base "$base" --checkpoint "$proof/checkpoint" \
  --language-weights "$proof/language/admin.npz" --catalog "$out/shops.json" \
  --specialist "$proof/specialist" --service-weights "$proof/language/service.npz" \
  --db "$out/shop.sqlite" --audit "$out/audit.jsonl" --port 55736 --teacher-port 55729
```

In another terminal, run the browser check with Playwright installed. Use a fresh output path.

```sh
node experiments/shopware/benchmark/adaptation_browser.cjs http://127.0.0.1:55736 \
  experiments/shopware/evidence/joint-deck/traces/service-holdout.jsonl /tmp/holodeck-activation-browser
```

To repeat the offline fit with the bundled training partition, run the following separately. The fitted grammar and sparse weights match the published files byte for byte.

```sh
python "$bench/adaptation.py" "$proof/checkpoint" "$proof/traces/service-train.jsonl" "$out/refit" --teacher-port 55729
cmp "$out/refit/sparse.npz" "$proof/specialist/sparse.npz"
cmp "$out/refit/grammar.json" "$proof/specialist/grammar.json"
```

The report gives the shop slug, order root and expected checkpoint for each case. To verify restart, stop the server, run the same serve command with the same database, resume the root through `/api/session` and request its Admin projection. The response should retain the order and final revision. The projection entry in `audit.jsonl` identifies the checkpoint that served it.

## A later B2B transfer experiment

A purchase order amendment after partial shipment is a planned probe for transfer. Start with 40 accepted units and 24 already shipped. Ask to revise the total to 30. If the revision is accepted, six units remain to be fulfilled. The learned graph must preserve the completed shipment, apply the applicable contract terms and wait for approval when required.

Compare complete trajectories under two companies’ configurations, including approval refused and fulfilment arriving while a revision is open. Use independent expected records and action sets. Keep the runtime fixed and track which differences come from company data and which require learned semantics.

The work is sequenced in the [roadmap](roadmap.md). The [existing Shopware evidence](../experiments/shopware/README.md) establishes a narrower starting point.
