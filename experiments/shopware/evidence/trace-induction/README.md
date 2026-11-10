# Trace induction evidence

The [experiment](../../../../docs/trace-induction.md) uses synthetic Shopware sales channels. The unchanged holdout was recorded after both 18 journey fits were frozen. The changed teacher holdout was recorded after its separate 16 journey fit was frozen.

The JSONL files contain normalized input facts, observed cart snapshots, persisted order fields and events. The unchanged passive training partition is [bundled with the earlier joint deck](../joint-deck/traces/numeric-train.jsonl). The three fit receipts record expression trees, observed match counts and the training and compiler digests. The evaluator uses held out outcomes for assertions. The shop runtime uses the compiled grammar, unchanged selector weights and current merchant inputs.

[Summary and source restoration hashes](summary.json) · [Four browser journeys](browser-report.json)
