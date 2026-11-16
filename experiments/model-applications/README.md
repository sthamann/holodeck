# Definition to model experiments

These protocols test new application definitions. Existing applications can supply realistic invariants and failure traces. A fixture authored by the implementer is development evidence. Qualification requires independently supplied mechanisms and a sealed scorer.

## B1 · Contextual retention and state

Run from the repository root.

```sh
python3 experiments/model-applications/context_quotient.py
```

The finite reference has an authority change, two policy versions, an asynchronous effect, a latent label and two physical layout tags. At version one the label is unobservable. Activation exposes it. Open work keeps the version at which it was requested. The two versions emit the same effect names in different orders. Changing the layout tag is a semantic no-op; the lossy move also replaces the pinned revision.

The program computes stable partitions and independently checks every pair with a shortest-witness search over the product graph. Expected output is [recorded here](context_quotient.json).

| Check | Observed development result |
| --- | --- |
| Reachable reference states and quotient classes | 16 and 4 under current contexts, 40 and 20 when activation is admitted |
| Independent state-pair checks | 900 |
| Additional retention for states already reachable at version one | One fixed bit over the current equivalence class, for this migration family |
| Authority distinguished despite identical screen contents | Current actionable controls differ |
| Pinned continuation | One finish event separates the order of effects |
| Physical state move | Faithful layout tag has no witness; losing the revision pin is exposed by a finish event |

This fixture uses a modeled layout change and logical Holo observations. Its purpose is to establish discriminating oracles before wiring a real storage adapter or a renderer.

Extend with generated variant trees of effect dependencies and independently authored migration pairs. Compare global partition refinement, paired symbolic counterexamples and a compositional checker with a boundary summary. Count actual reachable states, distinguishing depths, proof size, time and memory. Perturb each proposed locality assumption separately. Follow with a proof assistant version of the retention lemma and the finite replacement theorem, then test symbolic types, identity freshness and concurrency.

## B2 · Compile a definition, surface missing intent

Give the same typed definition and a separately authored obligations list to a constructive compiler, a recurrent semantic model with an extraction checker, and a coding agent controlled by the same reference budget. Build a small effectful language with records, identity, queries, bounded arithmetic, conditionals, a clock input, delayed continuations, atomic effects and retries. Lower to a deterministic evaluator and a model module. An independent checker reads the obligations and compiled module. It does not call the compiler's own evaluator.

Use three evidence conditions for each reference: a complete formal contract, omitted policy with two compatible implementations, and contradictory obligations. Count qualified exact programs, incorrectly accepted programs, missing obligations raised with a shortest useful witness, and cost to get a complete verified artifact. Run a constructive language front end first; evaluate natural-language authoring later on the same sealed obligations as a distinct experiment. Freeze definition, policy authority, numeric range, reference hashes and resource envelope. A typed replay, an interpreter proof, and a model deployment test are different evidence.

## Executable vertical development run

Two authored definitions compile to an integer affine and threshold checkpoint. The serving process reads that checkpoint and executes it without loading the definition or reference implementation. A separately written reference computes the expected decisions, writes and ordered effects. Reachability is generated from that reference across all supported actors, inputs and activation steps. This covered **24,200 transitions in 1,740 states**. A changed checkpoint policy is rejected at its first divergence. A guard with an integer where the compiler requires a Boolean also fails.

The runtime runs the same content-addressed checkpoint representation on an append-only journal and SQLite with relational tables. It moves an open continuation in both directions, with activation before and after each move. An original rule permits plans reserving 1, 2 or 3 units. Switching one policy parameter removes the 3-unit option from the generated Holo, makes the direct API refuse it, and changes the agent's maximum choice and committed effect to 2 units. An existing 3-unit hold finishes under its original revision and produces the original ordered effects. The same Host and renderer also serve a second definition about recording and adjudicating samples.

Each of the four scenarios runs through the forward graph directly and through a separately marshaled exact tool boundary. All agree on results, affordances and effects. The browser applies actual streamed NDJSON patches and activates a control to commit a write. The deterministic replay tests process termination before and after commit, an incomplete trailing journal frame, compare-and-swap between competing clients, stale and cross-actor controls, idempotency key reuse, and duplicate delivery to a durable, idempotent mock receiver.

```sh
python3 experiments/model-applications/vertical/replay.py
python3 experiments/model-applications/vertical/replay.py --browser /path/to/chrome
```

[Recorded evidence](vertical/evidence.json) · [Definitions](vertical/contracts.json) · [Compiler](vertical/compiler.py) · [Independent oracle and replay](vertical/replay.py)

This run establishes exact execution and deployment for these authored finite contracts. The tool arm is a typed transport control inside the test process. The option chooser is deterministic. The vocabulary, scene layout primitives and lookahead rule are supplied. The migration quiesces writes during handover, and the browser uses a trusted loopback fixture rather than production authentication. Foundation-model reasoning, learned scene composition, simultaneous migration and unrestricted contract compilation require further evidence.

## Foundation model and certified plan results

The next [experiment series](foundation/README.md) runs two pretrained checkpoints locally and checks generated proposals against real durable effects. Exact feasibility evidence routed into the same input embeddings ties the external path. Separately grounding the objective and compiling the plan's outcome across policy revisions is a new, measurable architectural result. The proof, effect-table quotient, language-control comparison and negative tests are documented together with pinned weights and commands.

## B3 and B4 · One experiment from contract to running model

The first integrated contract combines delayed holds, versioned allocation pressure and an approval or compensation chain. It has a scarce resource, two actors, revocable authority and an external receipt. A new policy changes a decision in a situation where two apparently similar plans lead to different future effects. An outstanding request was opened before that policy change. An independently authored second contract must use different data and effect topology. Both may be artificial. Exercise a task that requires comparing at least two multi-step continuations before choosing or rendering a plan.

1. Freeze the model checkpoint, solver, semantics compiler, primitive basis, renderer, storage adapters, declared contract, splits and scorer. Give the authoring process the definition and permitted questions. Keep its outputs and learned artifacts out of the reference evaluator's generation path.
2. Produce the Deck, bind it to a generic Host and start with an event log. Generate Holo trees for unseen tasks and stream access-checked patches into a real browser. Run matched sequences through the API, Holo and agent. Give each arm current state, policy, candidate action sets, exact hypothetical results and equivalent assessment feedback.
3. While work is open, switch a single verified policy module, then migrate the log to a separately implemented relational adapter. Repeat from the same snapshot across intervention assignments. Cover both operation orders, pre/post commit interruption, duplicate delivery, stale UI and two racing clients.
4. Compare the internal semantic bridge with an external tool arm that has the same backbone, cached state, batched counterfactual lookahead, typed exact results, enabled action masks, renderer and effect engine. Also run internal ablation that still gets the results through the external path, plus a conventional application generated from the same contract. Include neutral exact interpreter and tensor VM for execution parity. In addition to equal compute caps, publish a budget curve for the external arm to expose advantages due purely to tool latency or extra inference.
5. Intervene on *the same revisioned policy cell*: feed old and new values into each execution arm. Predeclare the expected change in the exact threatened outcome, chosen plan, useful control and committed receipt. Use a matched internal bridge severance and an external-result intervention to distinguish use of correct information from an architectural effect. Estimate main effects and interactions on paired identical initial states.

Hard acceptance in the declared scope requires zero invalid writes, unauthorized actionable controls, missing mandatory actions under the task coverage contract, incorrect receipts, duplicate observable effects or accepted semantic divergences. A refused or unresolved case reduces coverage. Compare success at the task level at matched wall clock and accelerator cost, and report syntax violations and patch latency separately. Measure time to the first usable patch, p50/p95 convergence, patch volume, recovery and action success. Full state/render parity is separate from task usefulness. Actor-specific observation masks and identity/revision transport are tested under reconnection and refresh.

Candidate promotion threshold for internal coupling is at least ten percentage points on exact authorized task completion over the strongest external control, with a paired family-level uncertainty interval excluding zero. The internal path must have more than a replay advantage, and the effect must shrink under matched ablation. Freeze and power the actual study before scoring. One counterexample inside a certified scope refutes that certificate. A tie or external win leaves deep coupling unqualified and redirects the model architecture effort while preserving the measured compiler and deployment results.

## B5 · Learn abstractions that pay off on the next definition

Begin with 16 development and eight diagnostic composed contracts built over reusable mechanisms such as expiry, sequence approval, quota, idempotent retry, versioned pricing and compensation. Use a second sealed suite drawn by independent authors, crossing unseen identifiers, unseen composition, new authority and storage revision rules, and mechanisms outside the grammar. An eight-case diagnostic supplies design feedback only. Choose final sample size using family-level paired variance on the development data before publishing the hidden seed commitments.

Compare independent compile, symbolic library induction, learned library, shuffled learned library and the combined arm. Freeze rewrite and proof templates. Score at equal exact coverage and no false qualification. Plot marginal cost to verified Deck in author clarifications, oracle interactions, proof time, accelerator time and model artifact size. Include per-family scores. Use paired analysis over structural families. Predeclare the primary metric and the minimum improvement once development variance is known. Treat twofold reduction in marginal qualification cost as a stretch target. Audit for information moving into supplied state, hand-authored primitives or uncharged pretraining.

## B6 · Independent release and replication

Freeze shared compiler, model, renderer, Host and primitive versions. Give another team an authoring kit. They supply new domains, real data profiles and accepted obligations. Require the complete workflow with effect failures, migration and a policy replacement without adding app-specific code to the serving stack. Give the team full freedom to find a legal counterexample. Publish build inputs, proof scopes, external effects and first failures with reproduction instructions.

[Project roadmap](../../docs/roadmap.md) · [State and model theory](../../docs/model-application-architecture.md)
