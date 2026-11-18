# TodoMVC as the reference application

All current Holodeck experiments use one application: [TodoMVC](https://github.com/tastejs/todomvc), `examples/javascript-es5` at commit `ff43b02e59dfa604386bb382034b2cd07c2bcd8a`. It has records with identity, an unbounded collection, free text, bulk actions and a view that hides rows.

The architecture and the reasons for each approach are in [Next architecture](../../docs/next-architecture.md). This page holds what was built and what was measured.

## Summary

| Question | Result |
| --- | --- |
| Does a declarative Deck reproduce the original application? | Yes. 5,600 recorded steps of the original in a real browser, all equal. |
| Does the durable Host run a Deck with entities unchanged? | Yes. |
| Does an adapter carry the application without rules in the prompt? | Yes on known wording: 477 of 500 right with 70 prompt tokens. The frozen model with eight examples in 830 tokens reaches 204. |
| Does it read looser wording? | Poorly. 553 of 1,000 right. |
| Do intent programs beat written actions? | Mixed. Better on known wording (477 against 450), worse on looser wording (553 against 628). |
| Does agreement under relabelling filter wrong readings? | No. 130 of 559 agreed commits are wrong. |
| Does composing experts over exact outcomes help? | A little. 553 rises to 577. The right outcome is among the top three in 633 cases. Its confidence is worse than the greedy token probability. |
| Can a frozen 0.6B model choose among the Deck's own descriptions? | No. 23 of 150. |

One backbone (Qwen3-0.6B), one seed, 1,200 training steps. Requests are authored for this study and were not collected from people.

## The Deck and the original

[`deck.py`](deck.py) is a definition with two scalars, one collection, one view and seven actions, plus a small interpreter. [`original.py`](original.py) serves the original application and [`harness.html`](harness.html) drives it through DOM events: 400 random sequences of 14 actions. [`traces.json`](traces.json) records what a person would see after every step.

The first comparison found one real difference. The specification says the "mark all as complete" box follows the items. In the original it is a flip-flop that single items never update, so the first click can go the wrong way. The Deck now models the flip-flop, and all 5,600 steps agree.

The same Deck runs on the Host of the [vertical pilot](../model-applications/vertical/runtime.py) without changes to it: commits, refusals, a stale epoch, an atomic plan of three steps and replay from the journal.

## Intent programs

The model writes a line such as `complete #12 #5; delete completed; add "book flights"`. Each statement says what should hold afterwards. [`intent.py`](intent.py) simulates the Deck, emits only the actions still needed and refuses when a record is not reachable. The flip-flop is absorbed by the compiler.

## Adapter and controls

[`adapter.py`](adapter.py) trains a LoRA of rank 16 on 19,200 generated tasks. The prompt is the screen and the request. Training and scored tasks share no todo title. The looser cohort also shares no request wording with training.

| Arm | Prompt tokens | Looser wording, unseen titles | Training wording, unseen titles | Training wording, training titles |
| --- | ---: | ---: | ---: | ---: |
| Adapter, intent programs | 72 | 553 / 1,000 | 477 / 500 | 499 / 500 |
| Adapter, written actions | 68 | 628 / 1,000 | 450 / 500 | 460 / 500 |
| Frozen model, eight examples | 830 | 180 / 1,000 | 204 / 500 | 208 / 500 |

On looser wording the intent adapter is weakest on two requests in one sentence (65 of 195), on "delete completed" phrased loosely (11 of 38) and on selection by category (47 of 120). It never acts on an empty or impossible request (86 of 86). The action adapter adds and completes better on loose wording and fails on anything that needs several rows.

**Agreement under relabelling.** The model reads the same list under three sets of handles and the Deck compares the outcomes. On looser wording 559 readings agree and 130 of those are wrong. The error is in reading the request, and that error does not depend on the handles.

## Experts composed over exact outcomes

[Du et al.](https://arxiv.org/abs/2302.11552) compose diffusion models as energy functions and sample from the product. The transfer here is that an application gives an exact space to compose in. [`compose.py`](compose.py) multiplies three kinds of factor:

1. the reader, p(program | screen, request), under three labellings of the rows,
2. an explainer trained in the opposite direction, p(request | screen, program),
3. the Deck as a hard factor: a program that does not compile has probability zero.

Candidates are the greedy program and five samples per labelling. Mass is pooled per exact outcome, so different programs with the same result count as one. [`posterior.py`](posterior.py) records the candidates with the model. The analysis runs without it.

| Way of deciding | Top outcome right | Right among top three | Refused |
| --- | ---: | ---: | ---: |
| Greedy reader | 553 | 553 | 113 |
| Reader, mass over outcomes | 566 | 605 | 74 |
| Reader under three labellings | 575 | 633 | 50 |
| With explainer, weight 0.1 chosen on 200 other tasks | 577 | 633 | 50 |

A weight of 1.0 gives 590 on the scored tasks. That value was not selected in advance and is not a result.

The posterior does not give a better commit rule. Among the most confident half of the decisions that change the list, the greedy reader is wrong in 36 of 306 and the composed posterior in 78 of 332. Sampled wrong programs often land on the same wrong outcome, which inflates its mass.

What holds is coverage. A preview that offers three outcomes contains the right one in 63% of looser requests, against 55% for one.

## A frozen model scoring the Deck's descriptions

[`describe.py`](describe.py) lets the Deck list every single statement that is possible and render it in English. [`generalist.py`](generalist.py) lets the frozen model score p(request | description). No training is involved, so wording cannot overfit. The right outcome is among the candidates in 143 of 150 tasks and the model picks it in 23. It prefers `add "<the whole request>"`, because copying is the cheapest way to predict the request. Without free-text candidates it reaches 68 of 150. A 0.6B model is too weak for this. A larger one was not tested.

## What this means

- The exact part is settled for this application. A Deck can be written from a specification and corrected from recordings.
- An adapter carries the application at a twelfth of the context, for wording it has seen.
- The open problem is reading unfamiliar wording. Neither agreement nor composition solves it. Both treat the symptom after one reader has misread.
- The next steps follow from that: far broader wording in training, a larger frozen scorer, and showing three outcomes where the posterior is spread.

## Run it

```bash
python3 experiments/todomvc/replay.py > experiments/todomvc/evidence.json
```

This replays the original's recording, the Host run and the composition, and summarises the recorded runs. It needs no model and no packages.

Training and the recorded runs need the Python environment of the repository and a local snapshot of Qwen3-0.6B. `--actions`, `--shots 8` and `--backward` give the controls and the explainer.

```bash
.venv/bin/python experiments/todomvc/train.py <snapshot> --name adapter --save .holodeck/todomvc-adapter.pt
```

```bash
.venv/bin/python experiments/todomvc/posterior.py <snapshot>
```

```bash
.venv/bin/python experiments/todomvc/demo.py
```

The demo serves the application at `http://localhost:8780`. A request is read, the exact outcome is shown as a preview and committed after confirmation.
