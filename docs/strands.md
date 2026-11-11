# Strands decision heads in the joint Deck

Holodeck runs the Qwen3.5 2B Strands checkpoint locally. Two separately fitted pointer heads share its frozen backbone and released adapter. The projection head selects requested capabilities from the Deck's typed vocabulary. The service head selects the program for full or partial returns and refunds.

The two heads can be enabled independently. The current 9B language readout remains the default. The generated Holo still comes from the Deck's learned projection checkpoint and the renderer. A bound action still goes through the same program hash, tenant, revision and transaction checks. Selecting a service program changes the pinned Deck identity so that a persisted order cannot silently resume against another set of program weights.

## Training

The view head fits 166 annotated merchant requests from the repository's existing projection vocabulary and the added English combinations. Each option is scored relative to an omit option. More than one option can be selected in one forward pass.

The program head fits 14 distinct action and input state pairs extracted from native Shopware training traces. Its targets distinguish refusal, acceptance without a write, and the possible state updates for the relevant field. The Deck grammar supplies those values. The published specialist's observed input domain bounds which states are supported. Inside that domain, the trained pointer selects the program. The existing learned checkpoint still produces the workflow notifications and the projection cells.

## Reproduce locally

These commands use Apple Silicon and Python 3.13. They download the published checkpoint on first use. Use the local Qwen3.5 9B snapshot from [the joint Deck setup](joint-deck.md#replay-the-published-checkpoints) for the existing readout comparison.

```sh
uv venv --python 3.13 .holodeck/strands/venv
py=.holodeck/strands/venv/bin/python
uv pip install --python "$py" 'strands-decider[mlx] @ git+https://github.com/strands-labs/strands-decider.git@846b69ebe7603038caa3bfe250e50f1137b141d7'
evidence=experiments/shopware/evidence/joint-deck
qwen_base=/path/to/local/Qwen3.5-9B-Base-snapshot
model=$("$py" -c 'from huggingface_hub import snapshot_download; print(snapshot_download("amazon/strands-decider-2B-qwen3.5-v1-2610", revision="974685f92d5930b2d37e9cb5eacec7a8f882d9fa"))')
"$py" experiments/shopware/benchmark/strands_readout.py \
  --checkpoint "$model" --output .holodeck/strands/deck-head.pt
"$py" experiments/shopware/benchmark/strands_programs.py \
  --checkpoint "$model" --train "$evidence/traces/service-train.jsonl" \
  --holdout "$evidence/traces/service-holdout.jsonl" --specialist "$evidence/specialist" --output .holodeck/strands/program-head.pt
"$py" experiments/shopware/benchmark/check_strands.py \
  --base "$qwen_base" --evidence "$evidence" --model "$model" \
  --view-head .holodeck/strands/deck-head.pt --program-head .holodeck/strands/program-head.pt \
  --output .holodeck/strands/comparison.json
```

To use the program head, add `--strands-model "$model" --strands-program-head .holodeck/strands/program-head.pt` to the service-enabled joint shop command. Keep the original language arguments for this combination. Add `--strands-head .holodeck/strands/deck-head.pt` to use the Strands view head as well.

[Recorded comparison with individual requests and workflow steps](../experiments/shopware/evidence/joint-deck/strands-comparison.json)

## Observed results

| Admin intent readout | Exact facet sets | Median | p95 |
| --- | ---: | ---: | ---: |
| Existing Qwen3.5 9B readout | 11 / 14 | 55.1 ms | 71.0 ms |
| Published Strands, using the same omit comparison | 0 / 14 | 45.4 ms | 46.5 ms |
| Fitted Strands | 8 / 14 | 45.4 ms | 46.4 ms |

The fitted program head selected the reference result on 80 of 80 held out service commands. The released Strands head selected it on 44 of 80. The existing sparse specialist and the fitted Strands program head both matched all 16 complete journeys in the actual VM. That includes 44 writes and 36 commands that left the graph unchanged. These held out merchants reuse the same action and state vocabulary. The result shows transfer across the tested merchants and orders. Generalization to new rule families is an open experiment.

In browser runs with both heads, the shopper placed an order, the merchant generated an Admin workspace, the premature return was refused, and the shipped order could be returned. Shopware was offline. The tests compared the checkout result and service transitions with the captured reference in Birkenkai and Sandfracht. Both orders reopened with the same program checkpoint and final state after a server restart. A separate request reopened a persisted order with the existing language readout and the Strands program head. The film uses a freshly recorded English presentation of the Birkenkai workflow.

[Strands architecture](https://github.com/strands-labs/strands-decider/blob/main/docs/architecture.md) · [Published model](https://huggingface.co/amazon/strands-decider-2B-qwen3.5-v1-2610)
