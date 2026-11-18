# The application as a modality

This plan replaces "hand the Deck to a frozen model" with "train the foundation model to read Decks". A vision-language model receives an image through an encoder as tokens in its residual stream. A Deck-native model receives an application the same way. A new application then needs a compiled Deck and no training.

The earlier coupling test tied because the backbone was frozen and the exact evidence arrived as text or as identical input embeddings. Identical tensors give identical outputs. A gain can only come from a trained interface, from exact computation used inside the forward, or from cost.

## Target architecture

| Part | What it does | Exactness |
| --- | --- | --- |
| **Deck IR** | Entities, relations, transactions, effects, time and resource bounds, executed by a small deterministic VM. The affine and threshold graphs stay as the guard fragment. | Exact |
| **Three inlets** | A formal definition compiles directly. Existing source is lowered. Observed behaviour is turned into IR candidates by a coding model and rejected by counterexamples from the original. | Checked |
| **Deck encoder** | A schema-conditioned encoder turns fields, compiled action graphs and grounded candidates into soft tokens in the backbone's embedding space. Names enter through the backbone's own token embeddings. | Learned |
| **Executor block** | Inside the forward, the model picks a continuation for each candidate. The block computes guard, successor state and goal status exactly on a separate integer tape and writes the result back into the residual stream. Several blocks give several steps of exact lookahead in one forward. | Exact, model guided |
| **Masked heads** | Actions and scene tokens are decoded only from what the Deck allows in the current state and revision. | By construction |
| **Neural operators** | A rule may call the backbone for a typed judgement with a calibrated label set. The outcome is journaled as a fact, so replay never calls the model. | Calibrated, replayable |
| **Holo stream** | The model generates the whole scene as typed tokens: containers, facts and controls. A control token exists only for a currently authorized handle. | Bindings by construction |
| **Host** | Unchanged: journal or SQLite, compare and swap, receipts, migration. | Exact |

Integers never live in float activations. The residual stream carries addresses, choices and encodings of exact results. Whether the VM runs as a threshold network or as a native operator in the forward is an implementation detail. The learned interface is the part under test.

## Why this is trainable

Decks are executable, so supervision is free and exact. A generator draws contracts, an exhaustive planner labels every shortest plan, and the reference designer derives the scene that surfaces it. No example is labelled by hand, and the supply of unseen applications is unlimited.

## Claims and how each one fails

| Claim | Decisive measurement | Fails when |
| --- | --- | --- |
| **C1 · Zero-shot applications** | Held-out contracts with unseen names, unseen request phrasings and larger structure than training, plus the two hand-written vertical contracts. | The modality arm is not better than the same backbone reading the contract as text at equal training budget. |
| **C2 · Exact lookahead inside the forward** | Accuracy by required plan depth, with and without executor blocks, at one forward. | Removing the blocks or severing their write-back does not reduce depth two and deeper accuracy. |
| **C3 · Cost** | Context tokens and wall time per decision against the text arm with tool previews. | The modality arm is not cheaper at matched accuracy. |
| **C4 · Safety by construction** | Scene decoding under adversarial logits and stale epochs. | One unauthorized or stale control is ever actionable. |
| **C5 · Useful generated Holos** | Position of the control that starts a shortest plan, against a generated form over the same actions. | The generated scene does not reduce the controls a person must scan. |
| **C6 · Replayable intelligence** | Journal replay with the model removed and a changed model digest. | Replay diverges or a judgement is recomputed. |

A tie on C1 to C3 is reported as a tie. It then credits the compiler, the Host and the masks, and leaves fusion unqualified.

## Outcome of phase 1

| Claim | Outcome on unseen contracts |
| --- | --- |
| C1 · Zero-shot applications | **Failed.** Deck tokens 58%, with executor blocks 66%. The same backbone reading text 86%. |
| C2 · Exact lookahead inside the forward | **Not supported.** The blocks matter causally to the models trained with them. Text with executor blocks 87%, text with one round of external previews 91%. |
| C3 · Cost | **Not met at matched correctness.** A ninth of the context tokens at 20 to 33 points less accuracy. |
| C4 · Safety by construction | **Held.** No unauthorized control in any generated or adversarial scene. |
| C5 · Useful generated Holos | **Mixed.** Fewer controls to scan on generated contracts, more on the hand-written ones. |
| C6 · Replayable intelligence | **Held.** Replay never calls the model. Calibration on twenty messages was too small for the nominal coverage. |

The pretrained reader is the asset, and typed goal reading is the bottleneck. Phase 2 therefore keeps the application readable as text for the backbone, uses exact Deck tools with masks and the grammar-bound scene head, and tests Deck tokens only where text cannot follow: contracts too large for a prompt and state spaces too large for exhaustive search. [Measured run](../experiments/deck-modality/README.md)

## Outcome of the reader arm

The first step of the narrowed plan is measured. The backbone reads text, states the goal as text over the contract's field names, and the Deck searches and checks. Goals are stated exactly in 97% of unseen requests against 50% for the typed head. Exact search under the stated goal is right in 97%. Committing only when search and model agree gave 1,060 commits on the three cohorts with none wrong and withheld 11% of unseen tasks. The model's own multi-step planning stayed at the level of the text arm with previews. [Numbers](../experiments/deck-modality/README.md#reader-arm)

Looser wordings of the same requests showed the limit of that result. The reader stated 69% of them exactly and committed wrongly on 4%, and it committed on a third of requests without any goal. Retraining with broader wordings and requests marked unclear gave 83% exact goals, 91% correct by search, 3% wrong commits and 19 commits on 240 goal-less requests. Agreement between model and search lowers the risk and does not remove it. Scenes built from the verified step put the right primary control in 378 of 380 cases. [Numbers](../experiments/deck-modality/README.md#looser-requests)

## Phases

1. **Deck modality on the existing contract grammar.** Generator, exhaustive oracle, Deck encoder, executor blocks, masked action head, scene head, neural operators. Backbone Qwen3 0.6B with low-rank adapters. Matched text arms. *Implemented in [experiments/deck-modality](../experiments/deck-modality/README.md).*
2. **Deck IR and VM.** Entities with identity, relations, bounded collections, money, strings as opaque references, delayed continuations. The executor block steps the VM. The encoder becomes a graph encoder over entity instances with a retrieval window.
3. **Inlets.** Lowering from source for one real application slice, and trace-driven synthesis with counterexample search against the Shopware reference. Coding agent control arm on the same Host.
4. **Scale.** Larger backbone, a hundred thousand generated Decks, sealed families from independent authors, usability study for Holos, optional generative style layer over the scene stream.
5. **Independent challenge.** Frozen model, Host and renderer. Another team brings a definition.

Phase 2 was planned to start on a measured gain on C1 or C2. Phase 1 showed none, so the claim is narrowed as described above before the IR work begins.

## Phase 1 design

**Task.** A contract, a state, an actor and an English request. The answer is a grounded action that starts a shortest plan to satisfy the request, or a refusal when no plan exists. Depth runs from one to four steps.

**Arms, same backbone, same data, same optimisation budget.**

| Arm | Sees the application as | Exact evidence |
| --- | --- | --- |
| `text` | Serialized contract and state in the prompt | None |
| `text+tool` | The same | One round of exact previews for every candidate, as text |
| `deck` | Deck tokens | None beyond the current state |
| `deck+exec` | Deck tokens | Model-guided exact rollouts in executor blocks |
| `deck+exec, severed` | Deck tokens | Executor runs, write-back replaced by zeros at test time |

Every arm scores the same candidate list and is reported with and without the legal-action mask.

**Splits.** Contracts, field and action names and request templates are disjoint between training and test. A structural cohort has more fields and actions than any training contract. The two vertical contracts were written before the generator.

**Limits of phase 1.** Bounded integer fields, no entities, one acting role per task, generated English from templates, a 0.6B backbone, one training seed unless stated. The external arm receives one round of previews. A multi-round external agent would recover the same information at several forwards.
