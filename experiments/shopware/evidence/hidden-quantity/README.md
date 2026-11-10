# Masked quantity policy

[Method, findings and reproduction](../../../../docs/hidden-quantity.md)

| Artifact | Contents |
| --- | --- |
| `support.jsonl` | Initial Cart API probes for three acquisition strategies. Product imports omit purchase policy fields. |
| `adaptive.json`, `analytic.json`, `fixed.json` | Executable policies frozen before the first holdout. |
| `holdout.jsonl` | Native quantity controls and cart outcomes at stock 5 and 23. |
| `additional.jsonl`, `resolved.json` | One additional query per unresolved product in the analytic arm and the frozen result. |
| `resolution-holdout.jsonl` | New native observations at stock 3 and 11 for those four products. |
| `browser-input.jsonl` | Native references at stock 23 for the live Holoshop browser run. |
| `native-browser.json` | Native DOM controls and the manual input check, paired with separate Store API outcomes. |
| `deck-browser.json` | Holoshop controls and completed synthetic orders with Shopware stopped. |
| `summary.json` | Offline replay against the two holdouts. |

The products and orders are synthetic. The public captures contain masked records and normalized observations.
