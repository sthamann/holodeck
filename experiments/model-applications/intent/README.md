# Changing a running Deck through bounded English

A running Deck accepts a change of objective without restarting the application or losing the work already committed. The experiment lowers a supported English request into candidate pairs of a final condition and a condition that must hold at every state. It uses the same external PDDL sources and exact shared weights as the [structural replay](../structural/README.md).

| Request | What the verifier found |
| --- | --- |
| “Finish the job and leave sample in vault.” | Two admissible readings. The sample can return to the vault in two steps, or stay there throughout in three. A shared three-step plan exists. The pilot asks whether the sample must stay throughout or only be there at the end. |
| “Finish the job and keep sample in vault throughout.” | A three-step minimum. The sample stays in the vault. |
| “Finish the job and leave sample in vault at the end.” | A two-step minimum. A direct state search independently verifies both explicit optima. |
| “Change of plan: Move all balls except ball42 to roomb, and leave ball42 in rooma.” | After seven committed steps on the original 42-ball task, both readings and their conjunction have a 116-step minimum. The verified continuation keeps ball42 in rooma throughout. |

For each admissible meaning $m$, let $F_m$ be its feasible plans from the current state. Let $d_m$ denote the minimum action count over $F_m$ and $d_*$ the minimum over $\bigcap_m F_m$, with an empty set giving infinity. For every $m$ the inclusion $\bigcap_j F_j \subseteq F_m$ gives $d_* \ge d_m$. A plan optimal for all admitted meanings exists exactly when $d_*$ is finite and every $d_m = d_*$. The ambiguity rule certifies the plan under that precise cost model.

Retargeting verifies concrete actions against the frozen Deck. The certificate binds the request, grounded meanings, checkpoint, epoch, ordered actions and final facts. Changed requests, meaning digests and plan contents are rejected. An old certificate is stale after the epoch changes. The new certificate commits 116 ordered effects to SQLite after journal migration. An identical retry after restart returns the stored receipt.

The grammar recognizes enumerated movement and location phrases, conjunction and named exceptions. The vocabulary is declared for each fixture. The objective is shortest unit-cost action count. The published evidence covers these tasks and interpretations. An independent language benchmark should supply independently written requests, reference meaning sets and deliberate ambiguities, then compare grammar and learned grounders under the same verifier.

The [grounding stress test](scope.md) compares model and grammar predictions on held-back authored requests against the same exact planner in three durable states.

## Reproduce

Use the source tree at the pinned benchmark revision in [sources.json](../structural/sources.json). Source reads are verified by hash. Run from the repository root:

```sh
python3 experiments/model-applications/intent/verify.py --sources /path/to/downward-benchmarks > intent-replay.json
cmp experiments/model-applications/intent/evidence.json intent-replay.json
```
