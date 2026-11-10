# Shipping rule acquisition evidence

[Experiment and reproduction](../../../../docs/shipping-rules.md)

| File | Contents |
| --- | --- |
| `train.jsonl` | Native method availability and quote observations across four training shops. |
| `prior.json` | The 45 fitted structural weights, training channel IDs and observed signatures. |
| `support.jsonl` | The context pool for four new shops. Acquisition reveals one row at a time. |
| `programs.json` | Frozen acquisition transcripts and recovered programs by strategy, shop and budget. |
| `holdout.jsonl` | Separate Shopware captures with different prices and weights in the carts. A subset includes the saved native order's shipping method and totals. |
| `summary.json` | Independent comparison, false offer control and Joint Deck checkout replay. |

The shops, products and orders are synthetic. The captures contain no customer accounts, session tokens or hidden rule definitions.
