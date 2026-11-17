# Finite causal state discovery

This pilot tests whether a learner can recover hidden control state by intervening on a resettable application. It uses a finite deterministic Mealy machine. An observation is a canonical string per action and can encode an approval, refusal or a durable effect. Each query starts at the same reset state. The learner receives the action alphabet and an observation function. It builds a table of histories and distinguishing suffixes, exports an executable transition table, and corrects it with bounded conformance probes.

```sh
node experiments/causal-discovery/replay.mjs
```

Requires Node.js 18 or newer. The experiment has no package dependencies. On the two public fixtures the replay checks the following results.

| Fixture | Recovered states | Conformance horizon | Reset queries | Executed actions |
| --- | ---: | ---: | ---: | ---: |
| Publication gate | 3 | 3 | 48 | 138 |
| Delayed inventory release | 4 | 4 | 47 | 201 |

The horizon covers bounded conformance from reset. The learner may issue longer diagnostic words while constructing its observation table. The query and action totals include those probes. A run that exhausts its budget reports that status even if it has a tentative executable candidate. The exported machine can be replayed with no calls to the original.

The evaluation code has access to the private reference transition tables and checks all pairs of reachable states for a distinguishing trace. The learner does not see those tables or the evaluator's results. The negative control verifies that a horizon of two misses the four action counterexample `tick, tick, tick, release`.

This is a public mechanism test. It uses two authored fixtures and a fixed action vocabulary. The next [causal acquisition experiment](../../docs/roadmap.md#causal-acquisition-and-transfer) requires generated applications, sealed families, factored state, matched baselines and frozen primitive transfer. Its larger domains will need conformance methods that avoid enumerating all words.
