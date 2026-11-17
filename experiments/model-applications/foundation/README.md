# Grounded objectives and certified plan execution

This series tests three separate claims: whether a placement changes what a frozen model decides, whether the application exposes sufficient outcome information, and whether a plan remains correct for every objective the language step admitted. The synthetic domains, action sets and utterances were written for development. Repetitions across two domains, policy versions and option order measure stability. They do not create independent language samples.

## Same backbone, exact evidence in different places

The first experiment runs the pinned TinyLlama 1.1B Chat checkpoint in a read-only float32 NumPy forward. Elementary next-token and cached versus dense checks qualify the runner for this pilot. Both plans in each prompt can be previewed by the exact compiled application.

| Feasibility evidence path | Exact choices | Admissible choices | Both members correct on a changed-winner pair |
| --- | ---: | ---: | ---: |
| No exact evidence | 34 / 72 | 61 / 72 | 0 / 24 |
| Text from the external exact tool | 36 / 72 | 60 / 72 | 0 / 24 |
| Compiled inside the call and delivered in identical input embeddings | 36 / 72 | 60 / 72 | 0 / 24 |
| Additive calibrated residual interventions at layers 1, 6 and 12 | 36 / 72 each | 60 / 72 each | 0 / 24 each |
| External evidence with the same exact admissibility mask both paths can use | 48 / 72 | 72 / 72 | 12 / 24 |

With equal input embeddings, the maximum absolute difference between candidate logits is exactly zero. Three unrelated contexts supplied the additive residual calibration. Its failure to improve any decisions is scoped to those directions and layers. Each predicted plan was tried against the real durable host. A refused last step invalidates the whole plan, including previously valid steps. The accepted plan commits once and retries with its original receipt.

The same-embedding result isolates transport. For a deterministic forward, identical tensors and identical program state imply identical outputs. A learned internal representation has to change computation, exposure or cost to produce a genuine advantage.

## Exact effects when every plan is legal

Two new definitions produce either one action or a composed two-action plan. Both plans remain admissible in all three revisions. A revision can reverse their relative time and price, and a third makes one plan dominate on both. An ordinary tool baseline gives all numeric effect facts to the same backbone, with and without three demonstrations. The other architecture extracts a possible user objective from the frozen language model and uses exact arithmetic to choose a plan. It asks for clarification when two admissible objectives imply different choices. A typed tool receives the same effect table and grounded objective and ties that optimizer across the wire.

The goal readout was chosen using only 36 development utterances. Its held-back language suite contains 24 utterances crossed with two domains, three revisions and both option orders for 288 executions per backbone. The first, smaller authored fixture is retained in the evidence. An explicit objective is an oracle control. Each plan identified by the learned arm was projected as a typed Holo, revalidated and sent through an atomic SQLite commit in the local fixture. The streaming browser test for the common runtime remains in the preceding vertical experiment.

| Backbone and arm on the 288 case suite | Exact decisions | Both members of 64 rank reversals | Correct clarifications of 64 |
| --- | ---: | ---: | ---: |
| TinyLlama, full numeric tool results | 108 | 1 | 0 |
| TinyLlama, numeric tool results plus demonstrations | 112 | 0 | 0 |
| TinyLlama, frozen feature readout and exact optimizer | **200** | **24** | **56** |
| LFM2.5 1.2B, full numeric tool results | 131 | 2 | 13 |
| LFM2.5 1.2B, numeric tool results plus demonstrations | 59 | 0 | 58 |
| LFM2.5 1.2B, frozen feature readout and exact optimizer | **248** | **44** | **64** |
| Exact optimizer with the explicit objective supplied | 288 | 64 | 64 |

The frozen feature readouts correctly grounded 13 of 24 and 19 of 24 distinct language inputs, respectively. Remaining wrong objectives can still cause the system to choose the wrong legal plan. The engine is exact over the stated objective and effect model, while uncertainty about a person's intent remains a separate problem. The reported improvement over joint language and plan selection applies to these authored tasks and the tested prompts. It establishes no advantage of placement within the backbone over an equally capable typed tool stack.

[Observed checkpoints and results](evidence.json)

## Decision-only quotient and plan certificates

Each plan has an exact affine score in the supported preference weight. Slopes change order only at rational pairwise crossings. Removing crossings that preserve the minimizer set yields the same optimal choice relation for every continuous weight in the interval. Equality of these representations is equivalence for this decision observation. It is stronger than equality of the admissible set or of both per-metric ranks, and weaker than equality of exact effects.

At weight `3/5`, the pairs `A=(1,5), B=(5,1)` and `A=(1,10), B=(5,1)` have identical rankings by time and price but different optima. Their exact crossings are `1/2` and `9/13`. The compiler reduces **4,096** three-plan effect tables to **205** decision equivalence classes, with **53,440** exact query comparisons. That ratio counts distinguishable states in this finite grid. Raw numeric effects stay in the execution and display artifacts.

For an interval of admissible preferences, compare the selected plan against each competitor at both endpoints. Every difference is affine, so two nonnegative inequalities imply nonnegativity throughout. A clarification is required for a guaranteed optimum if the two endpoint optimal sets have empty intersection. The Holo binds its choice, objective interval, effect table and epoch to a revalidated atomic plan. The replay rejects forged plan steps, forged displayed effects and a forged certificate. A migration from the journal to SQLite preserves the current rule revision and permits the new plan while rejecting the old handle. This certifies the optimizer given its submitted objective, with intent grounding measured separately.

[Decision quotient evidence](preference_quotient.json) · [Certified plan replay](certified_replay.json) · [Architecture and assumptions](../../../docs/model-application-architecture.md)

## Reproduce

The reference certificate and quotient tests need only Python.

```sh
d=experiments/model-applications/foundation
python3 "$d/preference_quotient.py"
python3 "$d/certified_replay.py"
```

The foundation runs require NumPy and local Hugging Face snapshots. They read the weights without modifying them or contacting a model service. The LFM2 tokenizer implements the byte-level BPE behavior for these ASCII fixtures. The archived weight hashes pin the exact snapshots used.

```sh
d=experiments/model-applications/foundation
python3 "$d/benchmark.py" "$TINYLLAMA_SNAPSHOT"
python3 "$d/transfer.py" "$TINYLLAMA_SNAPSHOT"
python3 "$d/transfer.py" "$LFM2_SNAPSHOT" --backbone lfm2
```

[Pin for TinyLlama](https://huggingface.co/TinyLlama/TinyLlama-1.1B-Chat-v1.0/tree/fe8a4ea1ffedaf415f4da2f062534de366a451e6) · [Pin for LFM2.5](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Thinking/tree/ec77db7f39f41c028790706fc1e0cb33461e6a7b) · [LFM2 operator reference](https://github.com/huggingface/transformers/blob/main/src/transformers/models/lfm2/modeling_lfm2.py)
