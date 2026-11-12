# Model core

This experiment learns a state transition from an isolated executable teacher and runs it from a parameter checkpoint. It has no Shopware dependency. The same Rust runner loads every checkpoint. It receives only state, an event and configuration.

```text
teacher → active queries → learner → parameter checkpoint
                                             │
state + event + configuration ───────► exact forward → decision + state
                                                             │
                                                      SQLite commit
```

The teacher, learner and deck are separate executables. The learner enumerates small typed expressions from generic operations. It asks the teacher for the input that rules out the most remaining distinguishable candidates in the worst case. It fits acceptance and the state update separately. An observation can teach a state update only when the action was accepted. The teaching grid bounds what this selection can distinguish.

The exported checkpoint contains a fixed-size table of one-hot parameters. In each model step the parameters select operands from a register vector and select a result from a shared bank of exact operations. The scratch vector carries intermediate results forward. A learned Boolean output controls whether the state update takes effect. The inference binary uses the same Rust forward for all checkpoints. It loads neither the training expression tree nor the teacher. Integer operations use wide intermediates and reject an accepted result that exceeds the state type.

The local host provides a SQLite transaction, a revision check and durable receipts. Repeating a request returns its original receipt. Two different requests racing for the same revision cannot both commit. Each processed rejection also gets a receipt and consumes one revision. The only model output the store understands is an acceptance decision and an integer state.

## Results

The learner froze all three checkpoints before the holdout labels were recorded. The runner was copied into a fresh directory with only the checkpoints. It ran with a cleared environment, after the teacher processes had exited.

| Reference | Teacher queries | Matching offline cases |
| --- | ---: | ---: |
| Buffer up to the configured limit | 11 | 15,024 / 15,024 |
| Changed teacher requires strictly below the limit | 11 | 15,024 / 15,024 |
| Signal processing with a subtraction and cap | 9 | 15,024 / 15,024 |

The old buffer checkpoint disagreed with the changed teacher in 536 cases. The two checkpoints differ by two entries in a single one-hot operator mask, selecting less-than-or-equal or less-than. The runner and state contract were identical. The suite also verifies restart, receipt replay after a checkpoint change, racing writes, malformed parameters, and a teacher outside the grammar for which no checkpoint is exported.

This is a bounded integer experiment. It uses enumerative synthesis and active examples. DreamCoder-style library learning, a trained search prior, a Loom-style attention machine, arbitrary control flow and multimodal Holos remain part of the [target architecture](../../docs/architecture.md).

## Reproduce

Use Rust and SQLite development headers. From this directory run:

```sh
cargo test --locked --release -- --nocapture
```

To inspect the learned checkpoint and its separate teaching report:

```sh
cargo build --locked --release
out=$(mktemp -d)
target/release/learn "$out/model.json" "$out/lesson.json" target/release/teacher buffer
printf '%s\n' '{"state":7,"event":3,"config":10}' | target/release/deck forward "$out/model.json"
```

The last command yields `{"accepted":true,"state":10}`. Change the teacher to `strict-buffer` and learn into another checkpoint to replay the same input against the changed behavior.
