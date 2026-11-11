# Forward-pass shop evidence

- `holdout.jsonl` contains 36 native Shopware 6.7.15.1 journeys from four fresh synthetic sales channels. The capture started after fitting the nine expressions. The benchmark imports live inputs into its own store and compares its writes with the expected outcomes separately.
- `induction.json` records the acquired expressions, training cohort and input digest. The [training traces](../trace-induction/contrast-train.jsonl) and [base checkpoint](../joint-deck/checkpoint/) reproduce the induced bank.
- `summary.json` records the four local comparison arms, step-level equivalence of the port and compiled port, checkpoint replay and the arithmetic weight intervention.
- `regression-summary.json` records the earlier 40-journey cohort. Its two promotion boundary misses remain visible.
- `changed-teacher-regular.json` and `changed-teacher-summary.json` compare the regular and refitted forward-pass models on the same modified reference. `changed-teacher-fit.json` records the acquired expressions.
- The browser reports and [captures](../../../../docs/forward-pass.md#one-order-across-three-holos) document the same end-to-end browser script for each variant.

[Architecture, scope and commands](../../../../docs/forward-pass.md)
