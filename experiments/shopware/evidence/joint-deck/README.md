# Joint deck evidence

This bundle contains the small checkpoints and synthetic Shopware 6.7.15.1 observations for the [published replay](../../../../docs/joint-deck.md#replay-the-published-checkpoints).

- `baseline` and `checkpoint` contain the earlier and numeric joint deck. Each manifest records the grammar and parameter hashes.
- `specialist` contains the return and refund status extension for the numeric checkpoint. `language` contains the fitted readouts used with the frozen Qwen encoder in the browser demo.
- `traces` separates numeric and service training, holdout and historical regression cases. These observations contain synthetic IDs, catalog and order values, settings and status transitions. No runtime request needs the expected outcomes.
- `lessons` contains the earlier trace checked labels used for refitting the sparse checkpoint. The file name `retrieval.json` refers to the earlier comparison arm. The numeric serving path does not load it.
- [`summary.json`](summary.json) records the original replay, shipping output intervention, service and Chrome results. The output files produced by the replay add full program details.
