# Shopware architecture comparison

Start with the [current joint deck](../../../docs/joint-deck.md) for the bundled replay and browser demo. This log follows the experiments in order, including the results that failed and the resulting changes to the architecture. The [numeric compatibility section](#numeric-compatibility-in-the-joint-deck) records the latest comparison.

This local benchmark compares a frozen Qwen retrieval baseline, a shared trained controller, and a shared controller with a learned two-of-four expert router. It reuses the existing Shopware recorder, trace-based arithmetic compiler, integer VM and SQLite transaction store.

## Scope and source

The reference was Shopware Community Edition 6.7.15.1. All products, buyers and sales channels are synthetic. The recorded observations come from the native Shopware Store API and Admin API. Payment, shipment, return and refund are observed status transitions. No funds move.

- Four training sales channels provided 32 complete journeys. Another 1,638 archived Shopware training journeys identified candidate arithmetic expressions and rounding placement.
- The first holdout contains 16 journeys in Lichtlager and Teekontor, with different catalogs, shipping rates and promotion values. Some of the order and delivery events change order.
- The second holdout contains 16 additional journeys in Steinbogen and Kabelkontor. They add early completion and refund attempts, new delivery paths including partial shipment and reopening, and quantities above available stock. The checkpoints were frozen before recording this holdout.
- Native Shopware browser observations from the existing workspace and scale captures ground the annotated UI choices. Checkout layout and action mapping are annotations. The benchmark verifies projection, field binding and action execution in the common runtime. It contains no new browser or visual quality measurement.

The controller learns to select and assemble known expression fragments per target field. The expression choices come from training supervision. It does not synthesize arbitrary new expressions or component trees in this round. The Qwen text backbone is frozen in every arm. Merchant values are resolved by the VM against the current records. The decoder sees categorical state, presence of typed fields and natural language for UI requests. The coupon configuration for the submitted code is supplied as input. Multi-line carts, general rule composition, commercial B2B, autonomous flows, concurrent load and continual training are outside this run.

## Observed on the Mac with Shopware stopped

| Variant | Initial journeys | Initial effects | Harder journeys | Harder effects | Initial checkout binding |
| --- | ---: | ---: | ---: | ---: | ---: |
| A. Frozen Qwen with retrieval and up to three executable candidates | 16/16 | 192/192 | 0/16 | 116/240 | 16/16 |
| B. Shared trained controller | 16/16 | 192/192 | 0/16 | 124/240 | 8/16 |
| C. Shared trained controller and sparse router | 16/16 | 192/192 | 0/16 | 144/240 | 16/16 |
| B. Same architecture without trained weights | 0/16 | 0/192 | 0/16 | 0/240 | 0/16 |
| C. Same weights, expert identities rotated after routing | 0/16 | 12/192 | 0/16 | 12/240 | 0/16 |

For each arm, a rollout uses its own durable output as the next state. Business events are issued directly if the projection is incorrect and counted independently from the UI result. All three comparison arms failed the new storefront and merchant UI requests. Each surface uses one reserved request wording across shops in this run.

The fixed-prestate diagnostic additionally runs the same action over an identical observed prestate in all arms. It isolates the effect of the intervention from accumulated rollout errors. A fresh benchmark report contains this result per event along with the emitted programs, expected and actual fields, and complete journey outcomes.

On the initial fixed prestates, A, B and C each match 192/192 decisions. B without training matches 0/192. C with expert identities rotated matches 44/192. On the harder prestates A matches 160/240, B matches 156/240 and C matches 172/240. These scores are separate from complete rollouts. The two trained controllers have 38,650 and 51,678 parameters respectively, in addition to the same frozen Qwen text model.

The harder run demonstrates an expressive limit. A request for 19 units against stock of 17 was capped at 17 by Shopware. The controllers selected a learned copy expression, which placed 19 units in the cart. The resulting checkout reached negative stock in the isolated comparison database. The benchmark records these incorrect effects rather than repairing them with a hidden host rule. The router improved step coverage on this held-out population but none of the variants completed a harder journey correctly.

## Reproduce

Use the official Qwen3.5-9B-Base snapshot `68c46c4b3498877f3ef123c856ecfde50c39f404` and the Mac MLX environment from the [Qwen experiment](../qwen/README.md). Use a fresh output directory for each run. The capture script only accepts an isolated loopback teacher through the existing recorder.

```sh
lab=.holodeck/benchmark
mkdir -p "$lab"
# Set HOLODECK_TEACHER_URL to the isolated Shopware teacher.
python experiments/shopware/benchmark/capture.py "$lab/teacher.jsonl" --count 8
# Stop Shopware before training and evaluation. Substitute its actual port.
PYTHONPATH=src python experiments/shopware/benchmark/run.py \
  "$lab/teacher.jsonl" "$base" "$lab/first" --teacher-port 55688

# Start the isolated teacher again and update HOLODECK_TEACHER_URL for its new port.
python experiments/shopware/benchmark/capture.py "$lab/challenge.jsonl" --count 8 --challenge
# Stop Shopware again. Check both teacher ports and reuse the first weights.
PYTHONPATH=src python experiments/shopware/benchmark/run.py \
  "$lab/teacher.jsonl" "$base" "$lab/harder" --teacher-port 55688 --teacher-port 64351 \
  --weights-from "$lab/first" --challenge "$lab/challenge.jsonl"
```

The report and per-arm SQLite files are written into the requested output directory. Training and holdout identities, exact programs, model weight hashes and capture hashes are reported. The fixture contains synthetic business data and the output stays local under the ignored `.holodeck/` directory.

## Compositional decoder

The second round compares the original fixed-expression head with decoders that choose each output field, then decode the operator, pointer and literal tokens of its expression. The prefix grammar only checks tree structure. It does not select business rules. A learned per-field score receives evidence about the corresponding argument independently of the other arguments. The projection head predicts column membership, sort field, action and row binding separately.

The paired controls use the same 52 Shopware training journeys across six channels and the same text features. The quantity expression is identified offline by eight captured training requests above stock. The assessment holds out a catalog write that combines name and stock while leaving price untouched. Training contains the individual writes and other combinations. The three-column UI compositions are absent from supervision. The components and actions are grounded in the native Shopware traces, with the column requests and layout targets annotated. The UI test uses fixed request wordings.

For the composition trial, collect the two separate datasets before testing with Shopware stopped. Update the loopback teacher URL for each recording.

```sh
lab=.holodeck/benchmark
python experiments/shopware/benchmark/capture.py "$lab/composition.jsonl" --composition --count 10
python experiments/shopware/benchmark/capture.py "$lab/confirmation-fresh.jsonl" --composition-confirmation --count 8
# Stop the teacher and supply the actual former port.
PYTHONPATH=src python experiments/shopware/benchmark/compare_composition.py \
  "$lab/teacher.jsonl" "$lab/composition.jsonl" "$base" "$lab/composed" \
  --confirmation "$lab/confirmation-fresh.jsonl" --teacher-port 55691 --steps 800
```

Pass `--chrome` with the Chrome executable path to run the model-emitted row bindings in a real browser. This uses only scopes with a correct prefix from the imported initial state. The browser posts the rendered row identity to the exact VM, and the independent assertion compares the durable effect. It verifies interactions, not design quality. Reports separate whole journeys, individual steps, fixed prestates, and exact UI plans with a successful bound action.

### Confirmation on Bernsteinhandel and Stadtgarten

The saved checkpoints were evaluated with Shopware stopped. The training data excludes both shops and the combined name-and-stock write. All four comparison arms completed 0/16 full journeys.

| Decoder | Exact effects | New combined catalog write | Storefront binding | Checkout binding | Merchant binding |
| --- | ---: | ---: | ---: | ---: | ---: |
| Frozen Qwen retrieval | 156/224 | 0/16 | 0/16 | 0/16 | 0/16 |
| Fixed expressions | 64/224 | 0/16 | 0/16 | 0/16 | 0/16 |
| Compositional shared head | 84/224 | 16/16 | 16/16 | 8/16 | 0/16 |
| Compositional routed head | 72/224 | 16/16 | 0/16 | 0/16 | 0/16 |

Rotating the learned expert identities reduces the combined write to 0/16. The new catalog program combines two familiar copy instructions and keeps the prior price. It does not establish discovery of a new arithmetic law. The heads can produce a well-formed tree with inconsistent business semantics. The shared head produces incorrect discounts or tax amounts in some cases and selects an incorrect payment transition in others. This explains why the new field and UI composition did not yield complete parity.

Chrome exercised two separate prefixes from imported starting states at Bernsteinhandel. In the first it clicked the generated cart binding and stored a 16-unit cart with the exact observed total. In the second it clicked the generated checkout binding and stored an order for €960.97. Both effects matched the Shopware references. No matching merchant view and action qualified for a browser run.

## Shared dataflow and coupled state effects

The arithmetic comparison interns identical expressions in a topologically ordered graph. The executor computes each node once. Output bindings name the shared nodes directly, so the discounted gross and net amounts can use the same discount and tax calculations. Backward-only references, one writer per output and the existing atomic store constrain the runtime. The graph dictionary is compiled from training labels and the learned heads select regions. This round does not learn an unrestricted graph generator.

The scoped controls use the state of the object addressed by a status event. The paired variants use the same lessons, including Shopware status probes and the recorded inventory effects of cancellation and reopening. The coupled variant learns a small categorical head for coherent numeric or transition effects. The state head has a finite set of learned input signatures and declines unseen signatures. Its configuration selectors are chosen by whether a subset of recorded facts uniquely distinguishes the training targets. The existing compositional head still handles catalog bindings and projection. Rotating the logit columns in the learned head leaves the graph dictionary and runtime identical.

Collect `--dataflow` for two new training channels and two held-out channels. Collect `--dataflow-confirmation` independently. Stop Shopware before each evaluation. In the commands below, use the actual former loopback ports.

```sh
lab=.holodeck/benchmark
python experiments/shopware/benchmark/capture.py "$lab/dataflow-holdout.jsonl" --dataflow --count 8
# Stop the teacher.
PYTHONPATH=src python experiments/shopware/benchmark/compare_composition.py \
  "$lab/teacher.jsonl" "$lab/composition.jsonl" "$base" "$lab/dataflow" \
  --dataflow-capture "$lab/dataflow-holdout.jsonl" --teacher-port 55692 --steps 850

# Start the isolated teacher and update HOLODECK_TEACHER_URL.
python experiments/shopware/benchmark/capture.py "$lab/dataflow-confirm.jsonl" --dataflow-confirmation --count 8
# Stop the teacher again.
PYTHONPATH=src python experiments/shopware/benchmark/compare_composition.py \
  "$lab/teacher.jsonl" "$lab/composition.jsonl" "$base" "$lab/dataflow-confirmed" \
  --dataflow-capture "$lab/dataflow-holdout.jsonl" --dataflow-confirmation "$lab/dataflow-confirm.jsonl" \
  --weights-from "$lab/dataflow" --coupled --teacher-port 55692 --teacher-port 55693
```

### Observed confirmation on Werftkontor and Moosladen

Shopware was stopped. Both shops were absent from the 68 training journeys. There were 60 supplemental Shopware transition samples. No model received new merchant weights.

| Variant | Exact effects | Whole journeys |
| --- | ---: | ---: |
| Frozen Qwen retrieval | 82/256 | 0/16 |
| Fixed expressions | 72/256 | 0/16 |
| Compositional shared head | 152/256 | 0/16 |
| Scoped expression trees | 200/256 | 0/16 |
| Scoped shared dataflow | 148/256 | 0/16 |
| Routed dataflow | 144/256 | 0/16 |
| Coupled effect head and compositional bindings | 256/256 | 16/16 |
| Same coupled model with rotated logit weights | 16/256 | 0/16 |

The routed dataflow matched 16/16 cart pricing and promotion pairs but still made incorrect transitions. The coupled head jointly selects the observed effect. It learned 19 effect classes across 83 input signatures. The confirmation uses fresh amounts, identities and merchant records. Its individual transition signatures are represented in the training sources. The held-out catalog write combines name and stock while preserving price.

The coupled arm also returned the same 16 checkout receipts after reopening SQLite and repeating the request. Chrome ran one 16-event journey in each shop with the same durable root from storefront through checkout and Admin shipping. The two generated checkouts saved €209.81 and €191.18. Both runs reached the Shopware reference return and refund statuses. These are simulated commerce effects over synthetic records. The browser suite tests the generated view bindings using fixed request prompts and a generic table renderer.

## Few-shot transfer of a new promotion rule

Shopware 6.7.15.1 supplies two native Rule Builder variants. The learner receives observed cart facts and the actual eligibility outcome. It receives neither the Rule Builder tree nor the target thresholds. The first ten carts provide a common set of candidate comparison constants. Both policies share a grammar of typed comparisons and two-term conjunctions or disjunctions. The online head updates a vector of log evidence weights as each example arrives.

The initial evidence left 121 minimal conjunction candidates and two minimal disjunction candidates. An active sampler proposed seven additional carts based on disagreement, without reading any held-out cart. Shopware supplied the real outcomes. The full replay uses 17 observations per rule and resolves each rule to one minimal consistent program. The conjunction requires at least €120 in goods and four units. The disjunction accepts either condition. The head is saved and loaded in a separate process. The prior joint effects checkpoint still computes discounts, tax, order and stock effects through the existing VM.

| Test on two blind merchants | Exact carts | Exact orders |
| --- | ---: | ---: |
| Conjunction with previous unconditional behavior | 14/36 | 2/8 |
| Conjunction with learned weights | 36/36 | 8/8 |
| Conjunction with disjunction weights | 17/36 | 2/8 |
| Disjunction with previous unconditional behavior | 33/36 | 8/8 |
| Disjunction with learned weights | 36/36 | 8/8 |
| Disjunction with conjunction weights | 17/36 | 2/8 |
| Symbolic search on the same data and grammar | 72/72 | 16/16 |

Glasatelier uses a 23 percent promotion and Waldkasse an absolute €12.65 promotion. Both have separate catalogs and shipping prices. Multi-line cases start from the imported Shopware cart facts. The 16 one-line checkout cases begin with the imported product and replay the durable order effect. No teacher was accessible during the training replay or the blind evaluation. The learner replayed captured observations and saved its checkpoint in about one second per rule on the Mac. This measures local updating and saving, excluding live reference capture. The separate hot worker check activates and rolls back preloaded snapshots while retaining identical checkout receipts for requests committed under earlier versions.

The symbolic baseline ties the learned head. The result establishes checkpoint-based adaptation and transfer within this grammar. Qwen and the original transaction effects are frozen. Arbitrary new rule classes and production online learning were not measured.

To reproduce, use a disposable loopback Shopware teacher and set `HOLODECK_TEACHER_URL`. Split the resulting rows into separate training and holdout files by their `split` field. Use the same live teacher for the active queries. Stop it before training and evaluation.

```sh
lab=.holodeck/benchmark
python experiments/shopware/benchmark/capture_learning.py "$lab/policy-observations.jsonl"
PYTHONPATH=src python experiments/shopware/benchmark/capture_counterexamples.py \
  "$lab/policy-lessons.jsonl" "$lab/policy-active.jsonl"
# Stop Shopware and substitute its former port.
PYTHONPATH=src python experiments/shopware/benchmark/learn_policy.py train \
  "$lab/policy-lessons.jsonl" "$lab/policy-active.jsonl" "$lab/policy-weights" --teacher-port 55694
PYTHONPATH=src python experiments/shopware/benchmark/learn_policy.py evaluate \
  "$lab/policy-weights" "$lab/policy-holdout.jsonl" "$lab/dataflow-confirmed" \
  "$lab/policy-transfer" --teacher-port 55694
PYTHONPATH=src python experiments/shopware/benchmark/policy_hotcheck.py \
  "$lab/policy-weights" "$lab/policy-holdout.jsonl" "$lab/dataflow-confirmed" \
  "$lab/policy-hot" --teacher-port 55694
```

## Learn a durable Flow Builder workflow

Native Shopware 6.7.15.1 flows add order tags from paid and shipped state-enter events. The reference installs two effects. One marks the order after both events have occurred in either order. The other marks it after the first of either event. The Shopware reference stores private witness tags so a later reopen, cancellation, or failure does not erase earlier evidence. The learner receives ordered event names and the observed output tag only. Current order states are reserved for the current-state comparison arm.

The learner enumerates event witnesses, optional reset events, and conjunction, disjunction, or ordered composition. It deduplicates equivalent hypotheses as minimal deterministic automata. A log evidence vector scores the resulting automata. The selected workflow is lowered to a dataflow program over per-order memory and an effect marker. The VM and SQLite store use the same atomic commit and idempotency as the previous experiments. The marker and its first-emission delta are stored in the workflow benchmark, separate from Shopware.

The initial 12 sequences in Zeitlabor left seven minimal conjunction candidates. The experiment asked Shopware about three additional legal sequences chosen by candidate disagreement, using only the teaching shop. A failed payment or cancelled delivery after earlier positive evidence distinguished reset candidates from persistent memory. The final 15 sequences contain 63 events. The grammar contains 6,064 distinct automata. The learned conjunction has four reachable states and the disjunction has two.

### Observed on Nordkai and Farnlogistik with Shopware stopped

| Effect | Variant | Exact sequences | Exact events |
| --- | --- | ---: | ---: |
| Both historical events | Learned weights | 28/28 | 132/132 |
| Both historical events | Current-state conditions | 20/28 | 112/132 |
| Both historical events | Opposite learned weights | 0/28 | 48/132 |
| Both historical events | Exact symbolic induction | 28/28 | 132/132 |
| Either historical event | Learned weights | 28/28 | 132/132 |
| Either historical event | Current-state conditions | 28/28 | 132/132 |
| Either historical event | Opposite learned weights | 0/28 | 48/132 |
| Either historical event | Exact symbolic induction | 28/28 | 132/132 |

Twenty-six of the 28 held-out sequences differ in event pattern from the teaching sequences. The main evaluator interleaves orders and reopens SQLite at every event. The learned arms return identical receipts when each event is replayed. They emit 16 conjunction and 28 disjunction effects, each exactly once per qualifying order.

The stricter restore test runs twelve successive worker processes. Two orders use the same local ID in different tenants and receive different event orders. A pending order stays pinned to its checkpoint while a new order uses the other learned checkpoint. A mismatched checkpoint is rejected without mutating the pending order. Previously committed receipts are replayed after the workflows finish.

The symbolic control solves this bounded workflow as well as the weight update. The result establishes learned historical state, transfer and durable execution within the stated grammar. The test feeds recorded native state-enter events to the workflow runtime. It does not generate the upstream events in the deck or exercise external messages or production webhooks.

### Reproduce

Use a disposable loopback teacher, set `HOLODECK_TEACHER_URL`, and split the recorded output into two JSONL files using the `split` field. Keep the teacher running for the active queries. Then stop it and run the three offline stages.

```sh
lab=.holodeck/benchmark
python experiments/shopware/benchmark/capture_flows.py "$lab/flow-historical.jsonl" --historical
PYTHONPATH=src python experiments/shopware/benchmark/capture_flow_counterexamples.py \
  "$lab/flow-historical-train.jsonl" "$lab/flow-active.jsonl"
cat "$lab/flow-historical-train.jsonl" "$lab/flow-active.jsonl" > "$lab/flow-training-source.jsonl"
# Stop Shopware and supply its actual former port.
PYTHONPATH=src python experiments/shopware/benchmark/flow_transfer.py train \
  "$lab/flow-training-source.jsonl" "$lab/flow-weights" --teacher-port 55695
PYTHONPATH=src python experiments/shopware/benchmark/flow_transfer.py evaluate \
  "$lab/flow-weights" "$lab/flow-historical-holdout.jsonl" "$lab/flow-transfer" --teacher-port 55695
PYTHONPATH=src python experiments/shopware/benchmark/flow_transfer.py restart \
  "$lab/flow-weights" "$lab/flow-historical-holdout.jsonl" "$lab/flow-restart" --teacher-port 55695
```

## Join the learned checkout and workflow

The joined runtime takes normal catalog, cart, checkout and order commands. It executes the learned commerce effect and compares the before and after status of each existing object. A changed field yields a typed state-enter event. Object creation initializes its states without inventing state-enter events. A subscribed event is then evaluated by the learned workflow checkpoint. The same SQLite transaction saves the commerce state, workflow memory, effect record and receipt. The event adapter maps typed fields to names. It contains no readiness predicate.

The frozen commerce checkpoint gains 30 observed inputs for payment reopen, payment failure and delivery cancellation. They come from the archived native Shopware training probes. The extension preserves the selected program for all 83 original inputs. The UI and historical workflow checkpoints are restored from their existing artifacts. The new Shopware captures supply comparison results only.

### Observed on Küstenwerk and Wiesenlager

Shopware was stopped and its loopback port refused connections. Both new shops ran catalog, cart, promotion, checkout, payment and delivery transitions through the joined runtime. Each arm performed 140 commerce steps. The learned commerce kernel emitted 76 state-enter events matching the native reference in each arm.

| Workflow on the same commerce path | Complete journeys | Matching workflow steps | Durable effect records |
| --- | ---: | ---: | ---: |
| Historical conjunction | 16/16 | 140/140 | 12 |
| Historical disjunction | 16/16 | 140/140 | 16 |
| Disjunction checkpoint scored against conjunction | 0/16 | 88/140 | 16 |
| Internal delivery suppressed, scored against conjunction | 4/16 | 112/140 | 0 |

The main replay closes SQLite and opens it again between requests. Each arm returns 140 identical receipts for duplicates. The browser drives generated storefront, checkout and Admin views on the same order in each shop. The checkouts save €544.97 and €506.20. The generated Admin action changes the delivery from open to shipped while the current payment is open. Both orders have earlier paid evidence. Each shipment produces exactly one durable workflow effect.

Before the real shipping click, a SQLite trigger aborts the insert of the receipt after the graph update. The harness verifies that the shipping state, workflow memory and effect record all roll back. It then removes the fault, executes the same command via the generated UI and independently reads the resulting SQLite state. External delivery of the effect is outside this run. Payment and merchant data are synthetic.

### Reproduce

Run the preceding dataflow and workflow experiments to obtain their checkpoints. Use a disposable loopback Shopware teacher for capture, then stop it before evaluation.

```sh
lab=.holodeck/benchmark
python experiments/shopware/benchmark/capture_joined.py "$lab/joined-holdout.jsonl"
# Stop the teacher and use its actual former port.
PYTHONPATH=src python experiments/shopware/benchmark/joined_benchmark.py \
  "$lab" "$base" "$lab/joined-holdout.jsonl" "$lab/joined-result" \
  --teacher-port 55696 --chrome "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

## Deliver the learned workflow effect

The delivery benchmark starts from the actual committed effects in the joined commerce and workflow replay. The source SQLite ledgers are copied for the test. A delivery journal projects committed effects into jobs, with one identity per tenant and effect. Workers claim jobs with durable leases. The receiver runs as a separate HTTP process against its own SQLite database. It saves an order mark and the corresponding receipt in one transaction. Retrying the same identity and payload returns that receipt. A different payload with the same identity is rejected. Tenant credentials and routes belong to the local fixture.

With Shopware stopped, the primary replay delivered all 12 committed marks from Küstenwerk and Wiesenlager. The recipient committed two additional marks from the earlier Chrome-driven shipping actions in a separate receiver database, because those two cases replay the same order fixtures.

| Injected failure or control | Observed result |
| --- | --- |
| Receiver commits and closes the connection without replying | Sender retains the effect and retries. |
| Receiver is unavailable on the next attempt | Effect stays pending. A restarted receiver returns the original durable receipt. The first mark takes three sender attempts and one applied effect. |
| Sender exits after receiving a valid receipt, before recording it locally | The claim survives as a lease. Another worker skips it until expiry and then obtains the same receipt. The second mark takes two sender attempts and one applied effect. |
| Late acknowledgement with an old lease | Rejected. |
| Two workers drain the remaining effects concurrently | Every pending effect receives its matching durable receipt. |
| Wrong tenant credentials or changed content under the same effect ID | HTTP 403 and 409 respectively. Recipient effects remain intact. |

The main recipient stores 12 distinct marks and recognizes two retried requests. An additional request is rejected for conflicting content. The browser recipient stores two marks. Before and after ledger hashes match for the commerce state and original transaction receipts. The test uses real loopback HTTP and separate worker processes. Its exactly-once effect depends on the receiver's idempotent transaction. The replay uses a controlled clock for lease and backoff boundaries.

### Reproduce

Create the joined benchmark artifacts first. Stop Shopware and use its actual former port. The runner starts and stops the loopback recipient and worker processes.

```sh
lab=.holodeck/benchmark
PYTHONPATH=src python experiments/shopware/benchmark/outbox_benchmark.py \
  "$lab/joined-result" "$lab/joined-holdout.jsonl" "$lab/outbox-final" \
  --teacher-port 55696
```

## Learn mixed-rate, multi-item commerce

The collection VM executes bounded maps, keyed lookups, grouping, sums and exact rational arithmetic with explicit cent rounding. Candidate programs determine where to cap quantity, round line tax, allocate shipping and promotions across tax bands, and reduce stock at checkout. The observed Shopware traces select the program weights. A fixed amount cap is selected from the earlier single-rate Shopware archive and reused with the new collections. The discounted calculation shares the learned allocation rounding rule across shipping and promotion. Neither quote inputs nor checkout commands contain native calculated totals.

The two training channels have 36 carts with two or three lines. The first holdout has 36 carts with four to six lines, 7% and 19% rates, and different products, shipping costs and discounts. Each checkout stores the order lines and updates stock by product identity. The previous learned commerce and flow checkpoints handle payment and shipping and emit the order marker. Shopware is stopped during learning replay and evaluation.

| First holdout arm | Complete native-matching journeys |
| --- | ---: |
| Saved collection weights | 36/36 |
| Same weights with line input order reversed | 36/36 |
| Intervention on the weights to split shipping equally across tax bands | 0/36 |

All exact comparisons include per-line quantity and tax, discount and shipping allocation, order totals, stock and workflow state. Checkout receipts are replayed after reopening SQLite. The collection program uses exact arithmetic and known candidate operations. This experiment measures a defined gross-price checkout profile, using Store and Admin API captures rather than a new browser run.

### Reproduce

Use the preceding joined and historical workflow checkpoint artifacts. Record the source rows in an isolated loopback Shopware teacher and split the JSONL by its `split` property. The subsequent commands require the teacher stopped and use its former port.

```sh
lab=.holodeck/benchmark
python experiments/shopware/benchmark/capture_mixed.py "$lab/mixed-observations.jsonl"
# Stop the teacher before learning and replay.
PYTHONPATH=src python experiments/shopware/benchmark/mixed_transfer.py train \
  "$lab/mixed-train.jsonl" "$lab/mixed-weights" --teacher-port 55697
PYTHONPATH=src python experiments/shopware/benchmark/mixed_transfer.py evaluate \
  "$lab/mixed-weights" "$lab" "$lab/mixed-holdout.jsonl" "$lab/mixed-transfer" --teacher-port 55697
```

### Confirmation with an unseen third tax rate

The same saved checkpoint was restored in Uferkauf and Heidewerk. Their native Shopware fixtures add a third tax band, using synthetic 5% and 13% rates respectively. Each basket has four to six purchased products and a separate, unpurchased catalog product. The test does not model the legal applicability of these rates.

| Confirmation arm | Complete native-matching journeys |
| --- | ---: |
| Same saved collection weights | 18/18 |
| Same weights with line input order reversed | 18/18 |
| Equal-share shipping intervention | 0/18 |

All unused catalog products retain their original stock. The stored order and the prior learned historical flow are verified in the same execution. The newly added rates are input data, independent of the checkpoint. No new projection or browser run is part of this test.

To reproduce the confirmation, record a separate holdout with `capture_mixed.py --new-rates --count 9`, stop the teacher again, and pass its recorded file to `mixed_transfer.py evaluate` with the saved checkpoint.

## Run the multi-item shops in a browser

The browser prototype uses the same mixed-rate collection, commerce and workflow checkpoints. Import extracts the catalog IDs, prices, tax rates, stock and merchant settings from one recorded case per shop. It writes an input-only merchant package. The serving process never reads the reference quotes, orders or workflow outcomes. The browser verifier reads those separately and compares them to the live output.

A small projection head is fitted on a frozen Qwen3.5-9B-Base representation, scope features and lexical features. Captured Shopware admin interactions and storefront actions ground the columns and event vocabulary. Annotated requests provide the target components and bindings. The model selects a component, its typed read source, grid columns, controls and commands from that vocabulary. A separate learned readout adds a tax breakdown from an intent clause. The renderer supplies the component implementations, language labels and shop styling. It binds a selected order to the emitted command and sends it to the same transaction store as checkout.

### Browser result with Shopware stopped

| Shop | Purchased lines | Tax rates | Promotion | Exact checkout total | Durable workflow marks |
| --- | ---: | --- | --- | ---: | ---: |
| Uferkauf | 6 | 5%, 7%, 19% | Percentage | €397.08 | 1 |
| Heidewerk | 5 | 7%, 13%, 19% | Fixed amount | €651.13 | 1 |

Chrome drives quantities, rejects an invalid coupon, applies the configured coupon, checks out, reloads and opens the same order in the admin grid. It exercises sorting controls, search, status filtering and order selection. The browser commands record simulated payment, reopen the payment and ship the order. Both runs match the reference quote, line and order taxes, stock by ID, state transitions and workflow marker. The teacher container is stopped and no browser requests leave the local app.

Two paired prompts add a tax panel while preserving the rest of the generated admin view. Across six admin prompts, the panel decision matches five expectations. The phrasing “Welche Umsatzsteuer ist bei den einzelnen Sätzen angefallen?” misses the panel. The same frozen Qwen backbone with the projection weights zeroed emits no components for all six prompts. This isolates the projection head within the supplied grammar. The stock and transaction checks use the earlier independent commerce checkpoints.

### Reproduce

Use the artifacts from the preceding collection and joined experiments and the local MLX environment. The import below creates a demo package for Uferkauf case 1 and Heidewerk case 5. Display names and test coupon codes are fixture labels. Payments are simulated and each browser session starts from an independent catalog snapshot.

```sh
lab=.holodeck/benchmark
out="$lab/mixed-ui-result"
py=.holodeck/qwen-lab/venv/bin/python
base="$(cat .holodeck/qwen-lab/base-path)"
app=experiments/shopware/benchmark/mixed_shop.py
mkdir -p "$out"
"$py" "$app" import --lab "$lab" --catalog "$out/shops.json"
"$py" "$app" fit --lab "$lab" --base "$base" --weights "$out/projector.npz"
"$py" "$app" serve --lab "$lab" --base "$base" --weights "$out/projector.npz" \
  --catalog "$out/shops.json" --db "$out/shop.sqlite" --audit "$out/audit.jsonl"
```

Open `http://127.0.0.1:55701/?shop=uferkauf` or `http://127.0.0.1:55701/?shop=heidewerk`. For a reproducible verification run, start the server with a fresh database. The verifier expects Node and Playwright. Set `HOLODECK_CHROME` to the Chrome executable if Playwright's bundled Chromium is unavailable.

```sh
node experiments/shopware/benchmark/mixed_browser.cjs \
  http://127.0.0.1:55701 .holodeck/benchmark/mixed-novel.jsonl .holodeck/benchmark/mixed-ui-result
```

## Compose an order workspace from a merchant request

The optional admin head replaces the fixed set of admin fields and controls with independent learned decisions. The input is a merchant request. The output selects columns, row selection, sort and search controls, status filters, detail panels and event bindings. The grid and its order identity come from the typed grammar. When a projected panel needs a selected order, the grammar exposes the row selector. The transaction store checks the projection ID, permitted action, bound order and expected revision before applying an event.

The head fits 14 facets using 138 labeled requests. Native Shopware admin captures ground the available columns, interactions and shipment binding. The language examples and polarity pairs are annotations. The checkpoint learns coefficients over Qwen representations and character fragments. A separate fit using only the character fragments serves as a comparison. Polarity pairs teach the head to suppress writes for a negated clause. The browser still requires a click to commit an action.

Chrome tested three different views in Uferkauf and Heidewerk against the same underlying order records.

| Request | Result |
| --- | --- |
| Payment operations | Sortable amount and payment columns, order number search, payment filter and the two payment actions |
| Tax breakdown | Order selection and tax per rate, with no write actions |
| Shipping operations | Delivery column, delivery filter, order lines, shipment action and workflow status |

A second order in each shop makes ascending and descending sort observable. The browser clicks apply only to the selected order. It verifies that the other order retains its original revision and statuses. The endpoint rejects a command absent from the current projection, a command redirected to the second order, and a command using a superseded projection. The selected order's checkout and payment and delivery transitions match the original Shopware traces, including one durable workflow marker. The second order is a local control on an independent catalog snapshot. Payment is simulated.

On 14 separate requests for supported facets, the learned head with Qwen matches 11 complete outputs and the lexical fit matches 10. Qwen adds the tax panel for the previously missed wording about individual rates. Remaining misses include a delivery filter expressed indirectly and extraneous columns or panels. A probe for net amounts per item, outside the current UI schema, produced a gross column and item lines. This version still needs a way to report unsupported requests.

```sh
lab=.holodeck/benchmark
out="$lab/admin-composition"
py=.holodeck/qwen-lab/venv/bin/python
base="$(cat .holodeck/qwen-lab/base-path)"
app=experiments/shopware/benchmark/mixed_shop.py
mkdir -p "$out"
"$py" "$app" fit-admin --lab "$lab" --base "$base" --admin-weights "$out/admin.npz"
"$py" "$app" serve --lab "$lab" --base "$base" --weights "$lab/mixed-ui-result/projector.npz" \
  --admin-weights "$out/admin.npz" --catalog "$lab/mixed-ui-result/shops.json" --db "$out/shop.sqlite"
```

With the server running against a fresh database, repeat the browser and language checks.

```sh
HOLODECK_COMPOSE_ADMIN=1 node experiments/shopware/benchmark/mixed_browser.cjs \
  http://127.0.0.1:55701 "$lab/mixed-novel.jsonl" "$out"
"$py" experiments/shopware/benchmark/check_admin_projection.py --base "$base" \
  --weights "$out/admin.npz" --output "$out/admin-language-report.json"
```

## One program decoder for commerce and workflow

The next architecture experiment removes the event-specific dispatch from the mixed commerce runner. Each external request and each observed state transition enters the same decoder. Its output names the cells, binds their operands and declares the writes. The VM evaluates exact expressions and the existing store commits the data, workflow effect and receipt together. A field-to-event schema emits state notifications. Checkout, coupon mode and payment action selection are decisions of the decoder.

The teaching pipeline translates previously trace-checked expressions and workflow fragments into program cells, and learns which shipping expression fits the new fixed-rate examples. Both fixed rates used for training must be explained by one operand-reading expression. The candidate with a configured rate matches all 36 fixed-rate quote observations. Two literal-rate alternatives each miss 18, and proportional allocation misses all 36. Prior fragments are supervision and part of the grammar. The experiment does not train arbitrary primitives or import PHP into the runtime.

Four synthetic Shopware sales channels provide 36 new journeys. They produce 324 supervision events and 57 distinct input signatures. The grammar contains 34 cells with 82 alternatives including inactive cells. Shipping tax mode and promotion mode are varied independently except for fixed shipping tax combined with a percentage coupon, which is absent from the entire training set. Four further channels are captured after freezing the shared and routed weights. These use new shipping prices, coupon values, SKUs and tax rates.

Whole-program retrieval, a categorical linear readout and two Qwen-based decoders share the grammar and transaction runtime. The shared decoder has 45,450 parameters on top of a frozen Qwen3.5-9B-Base. The routed decoder has 58,478 and activates two of four experts. Training uses 500 optimizer steps per neural head. Its deployment checkpoint contains the feature vocabulary, cell grammar and weights, with retrieval exemplars in a separate comparison artifact.

### What the first comparison exposed

| Variant | Known combinations | Reserved combination |
| --- | ---: | ---: |
| Whole-program retrieval | 40/40 | 0/8 |
| Categorical linear readout | 16/40 | 0/8 |
| Shared decoder | 40/40 | 0/8 |
| Routed decoder | 40/40 | 0/8 |
| Routed decoder with rotated expert identities | 0/40 | 0/8 |

Both learned neural decoders get the initial cart right on all 48 journeys. On the reserved combination they choose the wrong discount expression or shipping expression after the coupon. The checkout then commits the resulting incorrect quote in the experimental ledger. The original holdout becomes diagnostic data after inspecting the error. It is never added to training.

The next decoder learns sparse conditions for each cell from the original supervision. The unordered version detects an ambiguous choice for the shipping cell on the reserved combination. The ordered version learns a ranked decision list for the active cell, so a predicate with wider support can resolve overlaps with more narrowly supported predicates. It fits 129 sparse gates using 95 feature connections. It needs only its weights, input vocabulary and the same cell grammar at runtime. No Qwen model is loaded for this variant.

### Independent confirmation after the architecture change

A second cohort uses four different sales channels and 48 freshly captured journeys. The original 36 training journeys remain the only examples fitted by the ordered decoder. The four channels have different catalog rates, shipping prices and discount values from the earlier diagnosis. Recording resumed after a Shopware setup error. The verifier requires all 12 cases for each of the four channels and rejects any overlap with the first cohort.

| Variant | Known combinations | Reserved combination |
| --- | ---: | ---: |
| Whole-program retrieval | 39/40 | 0/8 |
| Categorical linear readout | 16/40 | 0/8 |
| Shared Qwen-based decoder | 39/40 | 0/8 |
| Routed Qwen-based decoder | 39/40 | 0/8 |
| Routed decoder with rotated expert identities | 0/40 | 0/8 |
| Unordered sparse decoder | 39/40 | 0/8 |
| Ordered sparse decoder | 39/40 | 8/8 |
| Ordered decoder with one shipping output altered | 23/40 | 0/8 |

All eight successful reserved cases emit a coupon program absent from the entire teaching set. Changing one learned output index redirects the fixed shipping gate to a legal proportional expression from the same grammar. All 24 fixed-shipping journeys fail after this intervention. The 24 automatic-shipping verdicts are identical before and after it. The ordered arm returns identical JSON checkout receipts after 48 database reopens and duplicate submissions.

The common known-case mismatch starts on an item with 14 units at €61.99 and a 12% rate. Shopware records €92.98 tax. The inherited rational expression yields €92.99 at the exact half-cent boundary. It propagates to the net amount and tax total of the order. The benchmark reports the failure as it happened.

The transfer is a result about composing known expression cells with learned applicability. The run covers this checkout and workflow profile. Product mutations, return processing, jointly trained projection and multimodal input require separate evidence.

### Reproduce

Use the existing collection and workflow training artifacts. Set the recorder to the isolated Shopware instance and stop it before fitting or evaluating. Capture each confirmation phase only after freezing the relevant checkpoint.

```sh
lab=.holodeck/benchmark
out="$lab/program-deck"
py=.holodeck/qwen-lab/venv/bin/python
base="$(cat .holodeck/qwen-lab/base-path)"
recorder=experiments/shopware/benchmark/capture_program_deck.py
bench=experiments/shopware/benchmark/program_deck_bench.py
sparse=experiments/shopware/benchmark/program_deck_sparse.py
port=55719
mkdir -p "$out"
# Record the training shops and stop the teacher before fitting.
"$py" "$recorder" train "$out/train.jsonl" --count 9
"$py" "$bench" fit "$out/train.jsonl" "$base" "$out/frozen" --lab "$lab" --teacher-port "$port" --cases-per-shop 9
"$py" experiments/shopware/benchmark/program_deck_checkpoint.py "$out/frozen" "$out/runtime"
# Record the first holdout after the neural weights are frozen, then stop the teacher.
"$py" "$recorder" holdout "$out/holdout.jsonl" --count 12
"$py" "$bench" evaluate "$out/holdout.jsonl" "$base" "$out/offline-result" \
  --lab "$lab" --checkpoint "$out/frozen" --runtime "$out/runtime" --teacher-port "$port"
"$py" "$sparse" "$out/frozen" "$out/sparse" --teacher-port "$port"
"$py" "$sparse" "$out/frozen" "$out/ordered" --decision-list --teacher-port "$port"
# Record the independent confirmation and stop the teacher again.
"$py" "$recorder" confirm "$out/sealed.jsonl" --count 12
"$py" "$bench" evaluate "$out/sealed.jsonl" "$base" "$out/sealed-result" \
  --lab "$lab" --checkpoint "$out/frozen" --runtime "$out/runtime" \
  --sparse "$out/sparse" --ordered "$out/ordered" --disjoint-from "$out/holdout.jsonl" \
  --teacher-port "$port" --cases-per-shop 12
```

## One checkpoint in the browser and transaction store

The joint prototype puts 34 commerce and workflow cells and 26 projection cells in the same sparse checkpoint. It fits 57 commerce signatures and 117 annotated projection signatures. The UI teaching labels reuse the native browser observations and expand combinations of annotated controls. An existing frozen Qwen encoder and language readout translate free Admin requests into intent features. This experiment does not jointly update the Qwen backbone. The renderer implements the fixed component vocabulary.

The live shop loads the joint checkpoint instead of the earlier commerce and projection dispatch. It decodes a read-only component program from the intent and the selected object's context. Each proposed control must also have an executable, effectful program for that object. Preview evaluates the same program on a copy of the state. The server binds the offer to the root, revision and program hash. The transaction store checks the revision and the runtime rederives the program before committing it. Finalized order documents and their source drafts are sealed by the storage contract. Payment and delivery state remain separate mutable fields.

Four synthetic shops are recorded in native Shopware after the joint checkpoint is frozen. The direct run passes all 24 journeys, including all four with the previously withheld combination of fixed shipping tax and percentage coupon. Chrome independently completes one journey per shop through Storefront, checkout and Admin with Shopware stopped. The four browser cases all have different merchant settings. The browser sees an available payment control switch as the order state changes. The generated shipping view combines a delivery filter, line items, tax breakdown and workflow status. Tests reject stale offers and actions sent for another order, and independently verify the saved stock, order and workflow mark.

The controlled arm suppresses the learned output that writes the shipped delivery state. It keeps the language readout, UI weights, renderer, data and command code identical. In all four shops checkout and payment retain their original effects. The shipping view changes only by losing its shipping button. A direct request for the unavailable action is refused. The normal arm saves one workflow mark in each shop. The control leaves the four deliveries open with no mark.

The earlier 12% rounding discrepancy remains a regression boundary. These new shops do not establish parity for all currency settings, product mutations, returns, commercial B2B or live learning. The browser runs use simulated payment and a test session per order.

### Reproduce

```sh
lab=.holodeck/benchmark
out="$lab/joint-deck"
py=.holodeck/qwen-lab/venv/bin/python
base="$(cat .holodeck/qwen-lab/base-path)"
app=experiments/shopware/benchmark/joint_shop.py
mkdir -p "$out"
"$py" experiments/shopware/benchmark/joint_lessons.py "$lab/program-deck/frozen" "$out/frozen"
# Start the isolated teacher and set HOLODECK_TEACHER_URL before recording.
"$py" experiments/shopware/benchmark/capture_program_deck.py joint "$out/native.jsonl" --count 6
# Stop the teacher. Import writes inputs only.
"$py" "$app" import --source "$out/native.jsonl" --catalog "$out/shops.json" \
  --case Falkensteg:5 --case Kupferkai:5 --case Lindenbogen:5 --case Sandwerk:5
"$py" "$app" serve --base "$base" --checkpoint "$out/frozen" \
  --language-weights "$lab/admin-composition/admin-v3.npz" \
  --catalog "$out/shops.json" --db "$out/shop.sqlite" --audit "$out/shop.jsonl"
```

Run `node experiments/shopware/benchmark/joint_browser.cjs http://127.0.0.1:55720 "$out/native.jsonl" "$out"` with a fresh database. For the comparison start the same server on port 55721 with a separate database and `--intervention`, then run the browser command for that port with `HOLODECK_DELIVERY_CONTROL=1`.

## Add an after-sales specialist while orders are open

The [learning guide](../../../docs/learning.md#one-specialist-from-training-to-activation) uses the published numeric checkpoint and includes an independent activation and restart report. The procedure below preserves the earlier capture and fit sequence.

The frozen joint checkpoint is kept byte for byte. A sparse specialist is fitted on 52 Shopware observations of four state commands across eight orders in two teaching channels. It learns which delivery and payment transitions are accepted, refused or accepted without a write. The routing vocabulary comes from the observed command and state relation. Unobserved state categories refuse rather than extrapolate a payment or delivery mutation. New internal notifications preserve the existing workflow evidence. This experiment covers only the states and effects observed in the scoped Shopware edition.

The composite checkpoint adds learned action cells to the existing Admin projection. An additional text readout on the same frozen Qwen features recognizes requests for those actions. The existing transaction checkpoint and language outputs continue to serve their old behavior. The live server can hold both versions. A local test activation stores the new checkpoint identity in the order graph in a version checked transaction. It leaves the order and its workflow continuation intact. The browser must obtain a new offer after activation.

The independent confirmation has 16 journeys across four shops recorded after the transaction specialist was frozen. The original checkpoint completes none of the new journeys. The specialist matches all 16. It commits all 44 expected changes and leaves the record unchanged for all 36 other attempts. Shopware rejects 32 of those attempts and acknowledges four without a state change. The existing 24 checkout and workflow journeys still match exactly and emit the same 144 programs.

Four Chrome runs place the order and record payment using the old version, activate the new version on that order, and then use the newly generated service controls. The runs reject a premature return and an old offer. They complete the scoped partial and full state transitions while preserving stock and exactly one workflow mark per order. The activation request itself is replayed with the same receipt. After a server restart, all four orders resume on the activated version. The experiment uses isolated local authorization and simulated status changes. It does not transfer money, credit individual return lines, book a restock or train on live customer data.

### Reproduce

```sh
lab=.holodeck/benchmark
out="$lab/adapt"
py=.holodeck/qwen-lab/venv/bin/python
base="$(cat .holodeck/qwen-lab/base-path)"
app=experiments/shopware/benchmark/joint_shop.py
mkdir -p "$out"
# Set HOLODECK_TEACHER_URL to the isolated teacher before capturing.
"$py" experiments/shopware/benchmark/capture_adaptation.py train "$out/train.jsonl"
# Stop the teacher before fitting.
"$py" experiments/shopware/benchmark/adaptation.py "$lab/joint-deck/frozen" "$out/train.jsonl" "$out/frozen"
"$py" experiments/shopware/benchmark/service_intent.py "$base" "$out/language.npz"
# Restart the teacher for confirmation and stop it afterwards.
"$py" experiments/shopware/benchmark/capture_adaptation.py holdout "$out/holdout.jsonl"
"$py" experiments/shopware/benchmark/check_adaptation.py "$lab/joint-deck/frozen" \
  "$out/frozen" "$out/holdout.jsonl" "$lab/joint-deck/native.jsonl" "$out/result"
"$py" "$app" import --source "$out/holdout.jsonl" --catalog "$out/shops.json" \
  --case Birkenkai:0 --case Sandfracht:0 --case Lindenlager:0 --case Werksand:0 --teacher-port 55724
"$py" "$app" serve --base "$base" --checkpoint "$lab/joint-deck/frozen" \
  --language-weights "$lab/admin-composition/admin-v3.npz" --specialist "$out/frozen" \
  --service-weights "$out/language.npz" --catalog "$out/shops.json" --port 55725 \
  --teacher-port 55724 --db "$out/browser.sqlite" --audit "$out/browser.jsonl"
```

With that server running on a fresh database, use `node experiments/shopware/benchmark/adaptation_browser.cjs http://127.0.0.1:55725 "$out/holdout.jsonl" "$out"`. Keep the old version as the default during the activation test. The optional `--active-specialist` flag starts new sessions on the combined checkpoint.

### Isolate the rounding discrepancy

The native calculator reproduces the Waldkai boundary. Shopware converts the unit tax through a string before multiplying by the quantity. With PHP precision 14, the exact half-cent becomes 92.9849999999998 before final rounding and yields €92.98. Raising precision to 17 yields €92.99 in the same Shopware calculator.

The independent line oracle covers 999 cases including 161 exact half-cent boundaries. Changing PHP precision changes 12 native results. Pure rational rounding matches 987 and significant-digit decimal rounding matches 998. The compatibility candidate that also reproduces the PHP 8.2 pre-round matches all 999 in this isolated profile. The original joint checkpoint remains reproducible with its previous arithmetic. The next experiment validates the numeric extension on full orders.

Generate cases with `python experiments/shopware/benchmark/check_rounding.py fixtures "$out/rounding-fixtures.json"`. Run `rounding_oracle.php` inside the initialized Shopware 6.7.15.1 test container with the fixture file on stdin. It emits JSONL. Compare with `python experiments/shopware/benchmark/check_rounding.py compare "$out/rounding-report.json" --source "$out/rounding-grid.jsonl"`.

### Numeric compatibility in the joint deck

The new checkpoint combines the existing commerce, workflow and projection cells with a versioned scalar arithmetic vocabulary. Generic operations reproduce the bounded PHP 8.2 arithmetic used in the reference edition. The decoded cells specify when to round, which value and interval to read, and how to allocate taxes and discounts. The interpreter executes those cells and the commit path still stores integer cents. The service loads the weights, grammar and numeric operations. The teaching archive, compiler and Shopware stay outside the request path.

The compiler considers eight source informed arithmetic variants against 36 observed quotes across 18 native journeys. All four variants using 14 significant digits match. Each variant using 17 misses eight quotes. The observations do not distinguish the four winning variants. The compiler keeps the shipping stage learned in the earlier experiment and uses the cart total as the denominator specified by the Shopware delivery calculator. The sparse model is refitted with the revised expression grammar and existing transaction and projection supervision. The experiment measures selection among these expressions. It does not establish discovery of their formulas from traces or an update to the Qwen backbone. [Native tax allocation](https://github.com/shopware/shopware/blob/v6.7.15.1/src/Core/Checkout/Cart/Tax/PercentageTaxRuleBuilder.php) · [Native cash rounding](https://github.com/shopware/shopware/blob/v6.7.15.1/src/Core/Checkout/Cart/Price/CashRounding.php)

The checkpoint was frozen before recording 48 journeys across four fresh Shopware channels. All shops have new products, an extra line sharing a tax band and independent shipping settings. The four profiles use item and total increments of 1/1, 1/5, 5/5 and 1/10 cents. The 10 cent total increment was absent from the fit population. Testing with the teacher stopped compares cart, coupon, saved order and stock, state changes, workflow effects and the receipt returned after reopening the database.

| Cohort | Previous joint deck | Numeric joint deck |
| --- | ---: | ---: |
| Four fresh shops | 10/48 | 48/48 |
| Earlier sealed orders including Waldkai | 47/48 | 48/48 |
| Earlier joint checkout and Admin journeys | 24/24 | 24/24 |

Holding the numeric grammar and runtime constant while changing one shipping cell output in the weights reduces exact journeys to 24/48. All automatic shipping cases still match. All fixed shipping cases diverge. This intervention isolates the learned choice of shipping expression. The precision choice is instead measured in compilation.

Chrome drives four fixed coupon and four percentage coupon journeys from Storefront to checkout and through payment, reopening and shipping in generated Admin views. All eight match the reference. The percentage journeys use the combined numeric and service deck. On a separate service cohort, that combination still matches 16/16 journeys, including all 44 expected mutations and all 36 attempts that must leave the record unchanged. No new language weights are needed for these checks.

The measured scope is the gross price profile with two decimal places, positive post-discount goods totals, one delivery and one coupon. Zero totals, net pricing, stacked promotions, other currencies and commercial B2B semantics require their own cases. Local apply time includes decoder, VM and SQLite for small single user graphs. It excludes rendering, network latency and load.

```sh
lab=.holodeck/benchmark
out="$lab/money"
py=.holodeck/qwen-lab/venv/bin/python
bench=experiments/shopware/benchmark
port=55729
mkdir -p "$out"
# Start an isolated Shopware 6.7.15.1 teacher with PHP 8.2 precision 14.
export HOLODECK_TEACHER_URL=http://127.0.0.1:$port
"$py" "$bench/capture_money.py" train "$out/train.jsonl"
# Stop the teacher before fitting.
"$py" "$bench/money_checkpoint.py" fit "$out/train.jsonl" "$out/frozen" \
  --baseline "$lab/joint-deck/frozen" --commerce "$lab/program-deck/frozen/retrieval.json" \
  --teacher-port "$port"
# Restart the isolated teacher, record new channels, then stop it again.
"$py" "$bench/capture_money.py" holdout "$out/holdout.jsonl" --count 12
"$py" "$bench/money_checkpoint.py" evaluate "$out/holdout.jsonl" "$out/result" \
  --baseline "$lab/joint-deck/frozen" --checkpoint "$out/frozen" --teacher-port "$port" \
  --regression "$lab/program-deck/sealed-complete.jsonl" --regression "$lab/joint-deck/native.jsonl"
"$py" "$bench/joint_shop.py" import --source "$out/holdout.jsonl" --catalog "$out/shops.json" \
  --case Flusskonto:5 --case Nordtaler:5 --case Kupfermuehle:5 --case Hafenrechnung:5 \
  --teacher-port "$port"
"$py" "$bench/joint_shop.py" serve --base "$(cat .holodeck/qwen-lab/base-path)" \
  --checkpoint "$out/frozen" --language-weights "$lab/admin-composition/admin-v3.npz" \
  --catalog "$out/shops.json" --db "$out/shop.sqlite" --port 55730 --teacher-port "$port"
```

Use `joint_browser.cjs` with the new server and reference file as above. Set `HOLODECK_CASE=7` and import case 7 for each shop to run the percentage coupon cohort.
