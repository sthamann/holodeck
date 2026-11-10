# Contributing to Holodeck

Good contributions make a claim easier to test. Start with the [concepts](docs/concepts.md), the [architecture](docs/architecture.md) and the [experiment replay](experiments/shopware/README.md).

An experiment should specify the initial state, event, configuration, expected effects and independent reference. Use synthetic cases. Separate recorded results from future design and show which part of the model or host produces a decision. If a new rule requires a new fixed primitive, describe it as a runtime change.

For a proposed projection, include the data bindings and the actual action. A useful negative case often shows whether the interface and executable semantics really share a source.

Before submitting a change to the included experiments, run:

```sh
python experiments/shopware/replay.py
```

Report any limits that affect interpretation, such as a fixed schema, supported currency or unavailable reference behaviour.
