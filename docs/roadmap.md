# Roadmap: prove the model, then broaden the software

All tasks are **planned**. The published experiments provide the starting evidence. The first target is standard commerce: catalog, cart, pricing, checkout, payment, fulfilment and a partial return. The commercial decision must come from the learned model.

The general model is planned under `src/holodeck`. Existing experiments remain under `experiments/shopware`. Sofa logistics, room planning and a partner platform are not prerequisites. ERP is excluded.

The machine-readable task source is [tasks.json](tasks.json). The [architecture](architecture.md), [contracts](interfaces.md) and [learning design](learning.md) define the detailed boundaries.

## M0: Boundary and teaching contract

### H01: Locate current business decisions

**Depends on:** Existing experiments.

**Paths:** `experiments/shopware/native/service.py`, `docs/decision-inventory.json`.

Trace the real cart, checkout and return path. Attribute each commercial decision to host code or a learned graph; preserve the existing results as baseline.

### H02: Define the shared model contract

**Depends on:** H01.

**Paths:** `src/holodeck/contracts.py`, `src/holodeck/primitives.py`.

Specify DeckStep, checkpoint, effects, continuation and projection. Freeze generic primitives with exact type and rounding semantics. No tax, checkout or return primitive may hide the business rule.

### H03: Collect complete commerce teaching cases

**Depends on:** H02.

**Paths:** `experiments/shopware/oracle/`, `datasets/commerce/schema.json`.

Vary teacher configurations. Capture complete cart-to-return trajectories, rejected events and unchanged states. Give missing Shopware behaviours an explicitly independent reference contract.

### H04: Freeze independent evaluation families

**Depends on:** H03.

**Paths:** `experiments/shopware/evaluation/`, `docs/evaluation.md`.

Separate merchants and rule combinations across training and evaluation. Equalize baseline context and verification access. Resolve the trainable model, tokenizer and hardware early. Evaluate against independent outcomes rather than the candidate validator.

## M1: Shared learned business model

### H05: Connect exact objects to the representation

**Depends on:** H02.

**Paths:** `src/holodeck/encoder.py`, `src/holodeck/state.py`.

Connect text and image features to typed objects, events and configuration. Preserve exact prices, quantities and identities as referenced operands. Declare completeness of required collections.

### H06: Implement the learnable business graph

**Depends on:** H02, H05.

**Paths:** `src/holodeck/model.py`, `src/holodeck/graph.py`, `src/holodeck/heads.py`.

Extend learned operand and operator selection to guards, object changes and continuations inside one Deck step. The output must carry business conditions and effects rather than a host function name.

### H07: Build the generic commit boundary

**Depends on:** H02, H05.

**Paths:** `src/holodeck/store.py`, `src/holodeck/commit.py`.

Commit typed changes, continuation, receipt and outbox together. Check access and versions. Stable effect identity prevents duplicate effects. Return conflicts to the model without calculating a replacement business decision.

### H08: Learn cart validity and pricing

**Depends on:** H03, H04, H06, H07.

**Paths:** `src/holodeck/learning.py`, `tests/test_learned_cart.py`.

Learn eligibility, gross and net amounts, tax, configured discounts and simple shipping conditions. Test rounding boundaries, mixed lines, inactive products and invalid quantities against independent labels.

### H09: Remove host decisions from the target path

**Depends on:** H01, H08.

**Paths:** `src/holodeck/adapters/http.py`, `tests/test_decision_origin.py`.

Route existing API input through the Deck step. Retain transport and formatting only. Remove a relevant learned graph part and verify that the capability disappears without a host fallback.

## M2: Complete standard-commerce flows

### H10: Learn checkout and order formation

**Depends on:** H09.

**Paths:** `src/holodeck/model.py`, `tests/test_learned_checkout.py`.

Learn customer requirements, consent binding, joint order changes and stock effects. Exercise an empty cart, price changes, the last unit and repeated confirmation. One accepted checkout must produce one consistent order.

### H11: Learn payment continuations

**Depends on:** H10.

**Paths:** `src/holodeck/scheduler.py`, `experiments/shopware/payment_simulator.py`, `tests/test_learned_payment.py`.

Deliver authorization, capture, failure, unknown outcome and refund as events. The model determines waiting, compensation and release for fulfilment. Duplicate or late messages must not duplicate charges or orders.

### H12: Learn fulfilment and partial returns

**Depends on:** H11.

**Paths:** `tests/test_learned_return.py`, `datasets/commerce/returns/`.

Connect shipment, return handover, receipt, inspection and refund in the same model. Learn payment eligibility and resale eligibility separately. A refund alone must not restore stock.

### H13: Compare complete commerce trajectories

**Depends on:** H10, H11, H12.

**Paths:** `experiments/shopware/evaluation/`, `tests/test_commerce_trajectories.py`.

Evaluate whole journeys and failures against independent expectations. Separate retrieval, guard, calculation and continuation errors. Extend API coverage only for the contracts actually demonstrated.

### H14: Restore business processes after restart

**Depends on:** H13.

**Paths:** `src/holodeck/checkpoint.py`, `tests/test_restart.py`.

Restart after ordering, payment requests and pending inspection. Preserve model identity, agreements and completed effects. Do not silently change the semantics of an active continuation.

## M3: Projections from the same semantics

### H15: Import catalog, media and merchant data

**Depends on:** H05.

**Paths:** `src/holodeck/imports.py`, `experiments/shopware/merchants/`.

Map products, variants, media, conditions and style declaratively. Keep vector retrieval separate from authoritative facts. Replay historical expectations only with the needed historical conditions. ERP stays excluded.

### H16: Read actions and projections from the same semantics

**Depends on:** H06, H13.

**Paths:** `src/holodeck/projection.py`, `src/holodeck/heads.py`.

Bind the action and projection outputs to the executed business graph. Each commercial control references the same guard and model version used when its event is executed.

### H17: Provide usable generic components

**Depends on:** H16.

**Paths:** `ui/components/`, `ui/bindings/`.

Provide cards, grids, forms, totals and history with typed bindings. Preserve sorting, filtering, focus and drafts. Components must not contain a second pricing or return policy.

### H18: Connect shopper and merchant views

**Depends on:** H15, H16, H17.

**Paths:** `ui/`, `tests/test_projection_contracts.py`.

Operate catalog, cart, checkout, orders and returns on the same records. Filter authorized data server-side. Reopen familiar views with current bindings; stale views must not bypass access checks.

### H19: Keep the model involved as facts change

**Depends on:** H14, H18.

**Paths:** `src/holodeck/dependencies.py`, `tests/test_changed_facts.py`.

Derive dependencies from executed reads. Recompute affected open decisions after price, quantity or state changes. Obtain required new consent while preserving unaffected drafts and completed agreements.

## M4: Connected learning and causal evidence

### H20: Build aligned understanding and execution data

**Depends on:** H04, H13, H19.

**Paths:** `training/export.py`, `training/schema.json`.

Link intents, states, decisions, graphs, continuations and action bindings in each case. Validate generated candidates by independent execution. Attractive UI is not a commercial correctness label.

### H21: Train the connected model path

**Depends on:** H20.

**Paths:** `training/train.py`, `training/model_manifest.json`, `src/holodeck/heads.py`.

Connect the already learned business core to encoder and projection training. Distinguish frozen features, learned selections and optional LoRA weights. Show that the consumer loads the trained artifact.

### H22: Test fusion with causal interventions

**Depends on:** H21.

**Paths:** `tests/test_model_ablation.py`, `experiments/shopware/evaluation/`.

Disable relevant guards, operands or continuations and observe the expected loss. Compare base, retrieval and training with equal knowledge and verification access. Common packaging is not evidence of fusion.

### H23: Compile and restore learned subgraphs

**Depends on:** H21, H22.

**Paths:** `src/holodeck/compile.py`, `src/holodeck/checkpoint.py`, `tests/test_model_equivalence.py`.

Specialize only stable parts and preserve dynamic selections. Compare acceptance, refusal, effects, continuations and action masks between normal and compiled execution. Restore the exact learned semantics.

## M5: New policy and merchant transfer

### H24: Learn a new early-refund policy

**Depends on:** H20, H22.

**Paths:** `training/policy_adaptation.py`, `tests/test_policy_adaptation.py`.

Learn early refund after confirmed return handover for a configured customer attribute and amount limit. Test boundaries, changed items and partial quantities. Keep restock bound to confirmed resale eligibility.

### H25: Transfer without changing the host

**Depends on:** H23, H24.

**Paths:** `experiments/shopware/evaluation/transfer.py`, `experiments/shopware/merchants/`.

Use one runtime artifact for two merchants. Change only declared data, configuration and model artifacts. Vary products, names and thresholds. Give direct rule generation the same context and checks.

### H26: Turn capability gaps into learning cases

**Depends on:** H19, H25.

**Paths:** `src/holodeck/gaps.py`, `src/holodeck/learning.py`.

Distinguish missing facts, retrieval errors, impossible requests and missing rules. Preserve a concrete counterexample. Open a new learning round with separate development and independent evaluation cases.

### H27: Activate and roll back an improvement

**Depends on:** H14, H25, H26.

**Paths:** `src/holodeck/activation.py`, `tests/test_learning_cycle.py`.

Activate a checked model for new runs, restart and show its actual effect in merchant B. Preserve existing capabilities. Restore the previous artifact. Connect detection, learning, persistence and use.

## M6: Reproducible commerce deck

### H28: Measure speed with semantic provenance

**Depends on:** H19, H23, H27.

**Paths:** `benchmarks/commerce.py`, `docs/performance.md`.

Measure computation, commit, saved views and new projection separately. Count interactions without a large model while retaining Deck semantics. Distinguish target budgets from measured latency.

### H29: Reproduce startup and API comparison

**Depends on:** H15, H18, H25.

**Paths:** `experiments/shopware/README.md`, `scripts/demo.py`, `tests/test_api_contracts.py`.

Provide a clean local database, synthetic merchants, media and test payments. Route Store and Admin comparisons through the same model. A fresh checkout must work without private local files.

### H30: Accept the actual commerce-deck claim

**Depends on:** H13, H14, H19, H22, H23, H27, H28, H29.

**Paths:** `experiments/shopware/evidence/`, `docs/status.md`, `README.md`.

Demonstrate purchase, payment, partial return, inspection, new policy and merchant transfer. Record decision origin, ablation, fixed host, restore and independent outcomes. Keep unproved claims explicit.

## What each stage establishes

These milestones test selected parts of the architecture. The eight target properties have their own mechanisms and evidence requirements. [Properties and how to test them](architecture.md#properties-and-how-to-test-them)

M1 already requires a learned commercial decision. Fusion is not postponed until after a conventional application has been built. M2 completes the standard process. M3 projects that same semantics. M4 tests the connection between understanding and execution. M5 changes the policy and transfers it with an unchanged host. M6 demonstrates the complete result.

The adaptation policy changes when a refund becomes eligible while keeping inventory tied to inspection. It therefore tests multiple dependent decisions, not another isolated formula.

Broader Shopware parity, B2B organizations and approvals, embedded holos, external partners and new business models follow after this evidence. They must meet the same boundary and transfer tests.

A purchase order amendment after partial shipment is a candidate for the later B2B transfer experiment. [Experiment design](learning.md#a-later-b2b-transfer-experiment)
