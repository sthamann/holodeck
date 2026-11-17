# The application as a modality: first measured run

This experiment trains a foundation model to read compiled Decks as tokens, to use exact execution inside its forward pass and to stream interfaces whose controls are bound to authorized handles. It then compares that model with the same backbone reading the same application as text. [Plan and claims](../../docs/deck-modality-plan.md)

**Result in one paragraph.** The text arms win. The same Qwen3 0.6B with the same adapters and training budget decides 86% of unseen applications correctly from a plain text description and 91% with one round of exact previews. The Deck-token arm reaches 58%, and 66% with executor blocks. Keeping the contract as text and adding executor blocks ties the text arm and stays below the tool arm. The executor blocks have a large causal effect on the models trained with them, and the Deck tokens need a ninth of the context. Neither is a gain at matched correctness. What held without qualification is safety by construction for generated scenes and replay of model judgements from the journal.

## Task

A generated contract, a state, an acting role and an English request. The answer is the grounded action that starts a shortest plan, or a refusal when no plan exists. Plans are one to four steps long. Labels come from exhaustive search and are confirmed by a second, independent search in the replay. Training and test contracts share no field names, action names, role names or request phrasings. Start states are chosen so that at least three actions are legal and legality alone does not reveal the answer.

| Cohort | Tasks | Uniform choice among legal options |
| --- | ---: | ---: |
| Unseen contracts, names and phrasings | 763 | 36% |
| Larger structure than any training contract | 382 | 31% |
| The two hand-written vertical contracts | 40 | 48% |

## Arms

All arms use Qwen3 0.6B with rank 16 adapters on the attention query and value projections, 1,200 steps of 16 examples from 2,000 training contracts, one seed.

| Arm | Sees the application as | Exact evidence |
| --- | --- | --- |
| `text` | Serialized contract, state and request | None |
| `text+tool` | The same | Exact one-step preview for every option, as text |
| `deck` | Deck tokens from a learned encoder over the compiled action graphs | None |
| `deck+exec` | Deck tokens | Four executor blocks inside the forward. For every option the model picks a continuation, the block applies it exactly and writes the resulting state and goal status back. |
| `text+exec` | Serialized contract | The same executor blocks, written onto the option lines |

The Deck and hybrid arms train 10.1 million parameters, the text arms 2.3 million.

## Decisions

Correct decisions with the legal-action mask applied to every arm.

| Arm | Unseen, of 763 | Larger, of 382 | Hand-written, of 40 | Context tokens |
| --- | ---: | ---: | ---: | ---: |
| `text` | 658 | 321 | 26 | 271 |
| `text+tool` | **698** | **345** | **28** | 293 |
| `deck` | 446 | 215 | 7 | 32 |
| `deck+exec` | 503 | 246 | 20 | 32 |
| `text+exec` | 666 | 328 | 24 | 271 |

Controls on the executor arms, unseen cohort.

| Control | `deck+exec` | `text+exec` |
| --- | ---: | ---: |
| As trained | 503 | 666 |
| Executor runs, write-back removed at test time | 250 | 125 |
| Executor checks the true goal instead of the goal the model read | 627 | 697 |
| Typed goal read completely right | 378 | 271 |
| Read the goal, then exhaustive exact search without the model | 502 | 578 |

What this shows:

- **Reading.** A pretrained language model reads a structured text description of an unseen application far better than a Deck encoder trained from scratch on 19,200 examples. C1 fails as tested.
- **Coupling.** The models trained with executor blocks depend on them. That is a statement about those models. Against the strongest external control it is a loss on Deck tokens and a tie on text: `text+exec` 666, `text` 658, `text+tool` 698. C2 is not supported.
- **Grounding is the bottleneck of every executor arm.** With the true goal, `text+exec` matches `text+tool` at 697. The typed goal head reads at most half of the unseen phrasings completely. The text arms never form a typed goal and do better.
- **Cost.** Deck tokens need 32 context tokens against 271 to 293. This is the one measured advantage of the modality, and it comes with 20 to 33 points less accuracy. C3 is not met at matched correctness.
- **Search baseline.** For these small state spaces, reading the goal and searching exhaustively equals `deck+exec`. Model-guided exact rollouts can only pay off where exhaustive search is out of reach.

The executor block positions were moved from layers 6, 10, 14 and 18 to 12, 16, 20 and 24 after the first run had been scored on the test cohorts. That raised `deck+exec` from 468 to 503 on the unseen cohort. The first run is kept in [results/blocks-6-10-14-18](results/blocks-6-10-14-18). Timings were taken while several runs shared one GPU and are not reported as evidence.

## Generated Holos

The scene head of `deck+exec` streams containers, facts and controls. The grammar offers a control token only for a handle the Deck enables in the current state. 200 scenes per cohort, 40 for the hand-written contracts.

| Measure | Unseen | Larger | Hand-written |
| --- | ---: | ---: | ---: |
| Well formed and authorized with the grammar | 200 of 200 | 200 of 200 | 40 of 40 |
| The same model decoding without the grammar | 173 | 158 | 8 |
| Primary control starts a shortest plan | 132 of 179 | 117 of 165 | 22 of 36 |
| Controls a person scans to reach it, Holo against generated form | 261 against 395 | 253 against 376 | 53 against 44 |
| Notice shown when no plan exists | 6 of 21 | 21 of 35 | 0 of 4 |

C4 holds by construction, and the replay adds 2,216 adversarially decoded scenes with no foreign or repeated control. A generated control commits through the durable Host, a foreign actor is refused and every handle is stale after the commit. C5 holds on generated contracts and fails on the hand-written ones. Layout quality was scored against a reference designer, not with people.

## Neural operators

A rule can depend on a model judgement. The frozen backbone scores a message, split conformal calibration turns the score into a set of labels, and the Host commits only when every retained label gives the same writes and effects. The judgement is journaled with the commit.

On 24 held-back authored messages the zero-shot 0.6B judge is weak on urgency (15 right, calibrated set contains the reference in 19) and good on refunds (22 and 22). The support Deck commits 7 tickets correctly, 6 wrongly and withholds 11. Taking the highest-scoring label commits 3 correctly and 21 wrongly. Replay from the journal reproduces state and receipts with the model removed, and a different model never recomputes a journaled judgement. C6 holds. The six wrong commits show that twenty calibration messages do not deliver the nominal coverage.

## What follows

1. **Keep the application readable as text for the backbone.** The pretrained reader is the asset. The next architecture is a text-reading backbone with exact Deck tools, the legal-action mask and the grammar-bound scene head.
2. **Drop the typed goal head as a gate.** Let the executor verify candidate plans against goals the model proposes in text, and measure false commits with the common-optimum rule from the scope experiment.
3. **Test the modality where it can win.** Deck tokens are nine times shorter. Contracts with hundreds of actions do not fit a text prompt. That is the regime for C3, together with state spaces too large for exhaustive search for C2.
4. **Train the Holo head on the text arm** and score it with people.

## Reproduce

The exact parts need only Python. The command checks 734,778 compiled transitions against the definitions, 277 labels against an independent search, the scene grammar, the Host binding and the judgement journal, and summarizes the recorded runs.

```sh
python3 experiments/deck-modality/replay.py > /tmp/deck-modality.json
cmp experiments/deck-modality/evidence.json /tmp/deck-modality.json
```

Training needs the packages in `requirements-training.txt` and a local snapshot of the [pinned backbone](https://huggingface.co/Qwen/Qwen3-0.6B/tree/c1899de289a04d12100db370d81485cdf75e47ca). Each arm takes 30 to 75 minutes on an M3 Ultra.

```sh
cd experiments/deck-modality
python train.py deck+exec "$QWEN_SNAPSHOT" --save ../../.holodeck/deck+exec.pt
python train.py text+tool "$QWEN_SNAPSHOT"
python judge.py "$QWEN_SNAPSHOT" > judgement_scores.json
python demo.py "$QWEN_SNAPSHOT"
```

The demo serves five Decks. A typed request becomes a streamed Holo, and a click commits on the Host.

[Recorded runs](results) · [Evidence](evidence.json) · [Generator and oracle](worlds.py) · [Model](fusion.py) · [Scene grammar](holo.py) · [Neural operators](neural_ops.py)

## Limits

Bounded integer fields without entities. One acting role per task. Requests come from templates. One backbone size and one training seed, so differences of a few tasks between arms are not established. The external arm receives one round of previews. The judgement and hand-written cohorts are small and authored for this study.
