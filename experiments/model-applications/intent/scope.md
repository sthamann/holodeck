# Does an exact Deck repair a language grounding error?

The model supplies possible meanings. The Deck tests whether one plan is optimal under every supplied meaning, then revalidates the concrete ordered effects at commit. The experiment changes the durable state while holding the application, utterance and model predictions fixed. After preparation, an ambiguous request authorizes the same one-step plan under both readings. At the initial state, one reading has a two-step optimum and the other a three-step optimum. After the sample has already been lifted, a final-only request has a one-step optimum and the persistent requirement is already impossible.

## Setup

The language task assumes a grounded sample, vault and job. It asks the model to distinguish a final condition, a persistent condition, both plausible readings, or a request outside this typed scope. The latter includes additional obligations the Deck cannot express. The two readings in the third label are both required for complete ambiguity coverage. The experiment uses one set of authored labels per utterance.

The pinned LFM2.5 1.2B backbone supplies (1) frozen logits on four answer labels after four fixed examples and (2) features for a linear readout selected by cross-validation on 32 training utterances. Each arm uses a separate split conformal threshold at alpha 0.2, calculated solely on 24 calibration utterances. The nonconformity is the best score minus the candidate score. The held-back cohorts contain 32 paraphrases and 16 stress cases, including negation, unstated timing and requests with an extra obligation. Those cohorts are synthetic, held back from training and calibration, and written for this study. There is no independent application family or exchangeability study.

Every one of the 16 possible predicted label sets is replayed at each of three durable checkpoints. The same typed planner consumes each set. The empty set and any set containing the outside label withhold execution. Each accepted distinct set and checkpoint commits to its own SQLite store. The harness compares concrete effects to the source interpreter and checks the action path and length against direct state search under the authored reference meanings. The 48 language inputs produce 144 repeated context decisions. The tally counts decisions, with 48 language samples.

## Result

| Grounder and commit rule | Correct commits | False commits | Withheld when the reference could commit |
| --- | ---: | ---: | ---: |
| Restricted English grammar | 0 | 0 | 72 |
| Frozen model, highest-scoring label | 60 | 84 | 0 |
| Frozen calibrated set, require a single label | 3 | 0 | 69 |
| Frozen calibrated set, exact common optimum | **15** | **6** | **57** |
| Same exact check, oracle flags outside scope | 15 | 0 | 57 |
| Trained readout, highest-scoring label | 62 | 37 | 6 |
| Trained calibrated set, require a single label | 59 | 37 | 9 |
| Trained calibrated set, exact common optimum | 61 | 37 | 7 |
| Always assume both expressible readings | 36 | 12 | 36 |
| Full reference meaning set | 72 | 0 | 0 |

For the frozen calibrated set, all six false commits involve an outside obligation. For example, the model omitted the timed clause in “Sample must stay in vault until the alarm, then move it to bench.” The Deck finished the job with the sample still in the vault. The trusted scope flag is an oracle intervention that isolates that failure. The trained readout also missed explicit restrictions inside the vocabulary. Its calibrated set still admits the two-step temporary removal for “I won't accept a detour through bench, even if sample comes back.”

## What the guarantee covers

Let the authored reference label encode a set of required readings, or the outside case. Expand every predicted label into its readings. Execution requires a plan in the intersection of the optimum sets for every retained reading and withholds on a retained outside label. If the reference label is in the prediction set, then any commit is valid and optimal for every reference reading. For the outside case, coverage implies no commit. Thus a false commit implies reference-label miscoverage. This implication holds per request and checkpoint within the declared semantics. The converse can fail when omitted and retained readings share an optimum.

For exchangeable calibration and future requests, standard split conformal calibration bounds marginal miscoverage by alpha. The implication gives the same upper bound on the marginal probability of a false commit under these assumptions. It supplies no conditional guarantee for committed cases or a shifted subset. The authored paraphrase and stress cohorts do not certify deployment risk. A closed action vocabulary can support a full exact proof of a candidate plan while leaving it possible that a person requested a different operation altogether.

Related work calibrates action candidates in language-directed planning. Holodeck tests a separate decision rule at matched predictions: execute across multiple predicted labels when the current application semantics provide a common optimum. [KnowNo](https://proceedings.mlr.press/v229/ren23a.html) · [CoFineLLM, 2026](https://proceedings.mlr.press/v331/wang26c.html)

## Reproduce

The [score archive](scope_scores.json) pins the model weights and corpus and records the selected readout. The [decision evidence](scope_evidence.json) contains context-specific counts and false commit witnesses.

```sh
d=experiments/model-applications/intent
python3 "$d/scope_model.py" "$LFM2_SNAPSHOT" > /tmp/holodeck-scope-scores.json
python3 "$d/scope_eval.py" --scores /tmp/holodeck-scope-scores.json > /tmp/holodeck-scope-evidence.json
cmp "$d/scope_evidence.json" /tmp/holodeck-scope-evidence.json
```

The model pass uses NumPy and the [pinned LFM2.5 checkpoint](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Thinking/tree/ec77db7f39f41c028790706fc1e0cb33461e6a7b). CI uses the archived scores and reruns calibration, source checks and durable execution with Python alone.
