# Roadmap for the first complete commerce deck

The first product milestone is a working standard shop. A customer shops in a **generated Holo Storefront**, completes a purchase in **generated Holo Checkout** and sees the order progress after the merchant handles it in **generated Holo Admin**. The shop runs on one deployed deck, including its learned commerce specialists, and one set of durable business records.

The milestones describe the broader development plan. The first standalone shop now demonstrates the local purchase and merchant flow with fresh holos and an offline teacher. [Runnable prototype and evidence](standalone-shop.md) · [Earlier experiments](../experiments/shopware/README.md)

## First delivery and acceptance

| Area | What the first shop must do |
|---|---|
| **Holo Storefront** | Browse and search a product catalog, show product details and variants, expose availability and let the customer build or change a cart. |
| **Holo Checkout** | Apply the merchant’s pricing, tax, shipping and discount rules, collect required customer data and consent, handle test payment events and create one durable order. |
| **Holo Admin** | Maintain the catalog, find and open the order, handle fulfilment and tracking, and carry out supported cancellation and return actions. The shopper can see the resulting order status. |

The first reference shop uses synthetic merchant data and a test payment adapter. Shopware supplies teaching traces and comparison results. The deployed shop serves the full journey with Shopware stopped. The deck owns catalog and cart decisions, pricing, order creation, payment handling, fulfilment and standard after-sales decisions. Generic storage, access checks, exact primitives, atomic commit and the component renderer provide the execution machinery.

The acceptance run starts with a clean database and empty projection storage. The deck composes each surface at request time with real bindings and actions, and the same learned graph governs the action when invoked. The run traces each commercial decision to its model version and graph, checks independently specified results, and covers ordinary refusals such as unavailable stock and failed payment. An archived or reopened holo can reuse the generated composition after this path has been demonstrated.

The general model is planned under `src/holodeck`. Existing experiments remain under `experiments/shopware`. The machine-readable task source is [tasks.json](tasks.json). See the [architecture](architecture.md), [contracts](interfaces.md) and [learning design](learning.md).

## M0: Boundary and teaching contract

### H01: Locate current business decisions

**Depends on:** Existing experiments.

**Paths:** `experiments/shopware/native/service.py`, `docs/decision-inventory.json`.

Trace the current native demo from a catalog request through cart, checkout and the merchant order view. Identify which decisions come from the model and which are still supplied by host code.

### H02: Define the shared model contract

**Depends on:** H01.

**Paths:** `src/holodeck/contracts.py`, `src/holodeck/primitives.py`.

Specify DeckStep, checkpoint, effects, continuation and projection. Freeze generic primitives with exact type and rounding semantics. No tax, checkout or return primitive may hide the business rule.

### H03: Collect complete commerce teaching cases

**Depends on:** H02.

**Paths:** `experiments/shopware/oracle/`, `datasets/commerce/schema.json`.

Use Shopware as an offline teacher. Capture complete shopper and merchant journeys with authorized actions, effects, refusals and unchanged records across configurations. Define independently checked expectations for behaviours outside the available reference.

### H04: Freeze independent evaluation families

**Depends on:** H03.

**Paths:** `experiments/shopware/evaluation/`, `docs/evaluation.md`.

Separate merchants and rule combinations across training and evaluation. Equalize baseline context and verification access. Resolve the trainable model, tokenizer and hardware early. Evaluate against independent outcomes rather than the candidate validator.

## M1: Learn and project the storefront

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

### H08: Learn catalog, cart and pricing behaviour

**Depends on:** H03, H04, H06, H07.

**Paths:** `src/holodeck/learning.py`, `tests/test_learned_cart.py`.

Learn product and variant eligibility, quantity and stock rules, gross and net amounts, tax, configured discounts and ordinary shipping choices. Execute the learned graph against independent labels for accepted and rejected actions.

### H09: Run the generated storefront through the learned core

**Depends on:** H01, H08, H15, H16, H17.

**Paths:** `src/holodeck/adapters/http.py`, `tests/test_decision_origin.py`.

Open a storefront holo from an empty projection store and buyable catalog context. Connect catalog and cart interactions to the learned Deck step. Test that disabling the relevant learned graph removes the associated capability.

### H15: Import catalog, media and merchant data

**Depends on:** H05.

**Paths:** `src/holodeck/imports.py`, `experiments/shopware/merchants/`.

Map products, variants, media, conditions and style declaratively. Keep vector retrieval separate from authoritative facts. Replay historical expectations only with the needed historical conditions. ERP stays excluded.

### H16: Generate working projections from the model

**Depends on:** H06, H08.

**Paths:** `src/holodeck/projection.py`, `src/holodeck/heads.py`.

Produce a projection at request time from the model output, role and current business context. Emit a component tree, data bindings and permitted actions tied to the executed business graph. Verify cold generation with no stored storefront, checkout or admin page.

### H17: Render generic components and route model actions

**Depends on:** H16.

**Paths:** `ui/components/`, `ui/bindings/`.

Render typed cards, grids, forms, totals and histories. Route permitted actions to the same model entry point. Component implementations supply interaction mechanics and formatting.

## M2: Complete checkout and merchant operations

### H10: Learn checkout and order formation

**Depends on:** H09.

**Paths:** `src/holodeck/model.py`, `tests/test_learned_checkout.py`.

Learn customer and address requirements, shipping selection, price consent, joint order creation and stock effects. Generate the checkout projection from the current decisions. Exercise an empty cart, price changes, the last unit and repeated confirmation.

### H11: Learn payment continuations

**Depends on:** H10.

**Paths:** `src/holodeck/scheduler.py`, `experiments/shopware/payment_simulator.py`, `tests/test_learned_payment.py`.

Deliver test payment authorization, capture, failure, unknown outcome and refund as events. The model determines waiting, compensation and release for fulfilment. Verify that duplicate or late events do not duplicate charges or orders.

### H12: Learn standard order handling in the admin

**Depends on:** H11.

**Paths:** `tests/test_learned_return.py`, `datasets/commerce/returns/`.

Learn fulfilment, cancellation and a standard return with the associated inventory and payment effects. Produce the permitted merchant actions and the corresponding customer order status from the same decisions.

### H13: Compare the complete shopper and merchant journey

**Depends on:** H10, H11, H12, H18.

**Paths:** `experiments/shopware/evaluation/`, `tests/test_commerce_trajectories.py`.

Run the complete journey through storefront, checkout and admin. Compare record effects, displayed actions and refusals with independent expectations. Attribute each commercial decision to the active model artifact and an executed graph.

### H14: Restore business processes after restart

**Depends on:** H13.

**Paths:** `src/holodeck/checkpoint.py`, `tests/test_restart.py`.

Restart after ordering, payment requests and pending inspection. Preserve model identity, agreements and completed effects. Do not silently change the semantics of an active continuation.

### H18: Connect the generated storefront, checkout and admin

**Depends on:** H10, H11, H12, H15, H16, H17.

**Paths:** `ui/`, `tests/test_projection_contracts.py`.

Generate shopper, checkout and merchant order views from the same model checkpoint and durable records. Operate the order from the admin and observe its new state from the shopper view. Apply server-side actor scope to data and action access.

## M3: Accept the first complete shop

### H19: Keep the model involved as facts change

**Depends on:** H14, H18.

**Paths:** `src/holodeck/dependencies.py`, `tests/test_changed_facts.py`.

Derive dependencies from executed reads. Recompute affected open decisions after price, quantity or state changes. Obtain required new consent while preserving unaffected drafts and completed agreements.

### H29: Start the first shop with its own model and records

**Depends on:** H14, H15, H18.

**Paths:** `experiments/shopware/README.md`, `scripts/demo.py`, `tests/test_api_contracts.py`.

Provide a clean local startup with a loadable deck, synthetic merchant data, media and test payment events. Start with empty projection storage and an offline Shopware teacher. Replay the full buyer and merchant journey through the deployed model.

### H30: Accept the complete standard shop

**Depends on:** H09, H13, H14, H19, H29.

**Paths:** `experiments/shopware/evidence/`, `docs/status.md`, `README.md`.

Demonstrate a complete purchase and merchant handling through newly generated holos. Run with Shopware stopped and use the learned deck as the source of all commercial decisions on the accepted path. Verify record consistency, correct permissions, replay and decision origin before accepting the milestone.

## M4: Deepen joint model training

### H20: Build aligned understanding and execution data

**Depends on:** H04, H30.

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

## M5: Learn new policies and transfer them

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

## M6: Optimize the accepted commerce deck

### H28: Measure speed with semantic provenance

**Depends on:** H19, H23, H27.

**Paths:** `benchmarks/commerce.py`, `docs/performance.md`.

Measure computation, commit, saved views and new projection separately. Count interactions without a large model while retaining Deck semantics. Distinguish target budgets from measured latency.

## What each stage establishes

M1 puts the first learned decisions into a storefront that is generated and usable. M2 follows the purchase through checkout, payment and merchant handling. M3 accepts the first complete shop with fresh holos and an offline teacher. M4, M5 and M6 deepen joint training, test new policy learning and transfer, and measure the speed of the accepted runtime.

The eight target properties have their own mechanisms and evidence requirements. [Properties and how to test them](architecture.md#properties-and-how-to-test-them)

A new early refund policy and a purchase order amendment after partial shipment are later experiments in adaptation and transfer. [Experiment design](learning.md#a-later-b2b-transfer-experiment)
