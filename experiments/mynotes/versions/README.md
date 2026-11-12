# One MyNotes workspace, two model versions

The same Notes workspace was served on the same address by one runtime. Each browser run started from a byte-identical SQLite snapshot. The model artifact was the only changed input at startup. MyNotes and the previous WebAssembly runtimes were stopped. The browser read both views before making any writes.

| Same workspace and inputs | Original model | Review model |
| --- | --- | --- |
| Cedar contains two outer sections and two nested sections | Previews and saves outer sections 1 and 4 | Previews and saves nested sections 2 and 3 |
| Split with an explicitly selected review tag | New notes get the selected review tag | New notes get both the source and review tags |
| River has just one section at the deepest level | The Holo offers Split and the API returns 201 | The Holo omits Split and the API returns 400 |

| Original recurrent model | Review recurrent model |
| --- | --- |
| [Cedar preview](evidence/original-preview.png) · [River actions](evidence/original-actions.png) | [Cedar preview](evidence/review-preview.png) · [River actions](evidence/review-actions.png) |

Both teachers are built from the pinned MyNotes source at `73ef4b7b37109a5209d9fbcfbd5d3799918071be`. Review is our controlled source variant. It selects the deepest visible heading level, requires two sections at that level and inherits the source tags. The source remains intact after a split in both versions. Create, edit, revision checks, tags, links, list and delete are exercised as part of the same workspace. [Teacher variant](prepare.py)

## The two execution paths

**Source compiled reference.** A narrow Go AST translator reads the depth comparison, minimum section count, refusal text and inheritance branch from each executable teacher. It lowers them into the existing typed Notes workspace graph. The unchanged workspace and the component vocabulary come from the previously authored graph. This is a direct compilation test for the changed source semantics. [Translator](source-policy.go) · [Original policy](evidence/original.policy.json) · [Review policy](evidence/review.policy.json)

**Trained recurrent core.** The native Go scanner supplies its observed behavior on successive prefixes. An independent native HTTP run supplies acceptance and tag inheritance observations. Each version has 78 observed state/token transitions and 13 terminal outcomes. A small PyTorch network takes the current categorical state and the next heading level, predicts the next state and feeds it back into itself. Three fitted readouts emit the chosen level, whether the action is available and whether to inherit tags. A refusal string comes from the native response. The state representation was chosen before training. It records the selected depth and whether the selected level has zero, one or multiple sections. The two learned artifacts have identical application rows, routes and routines. Only the recurrent model block changes. [Training](train.py) · [Original transitions](evidence/original.transitions.json) · [Review transitions](evidence/review.transitions.json)

The Holo and API both consume the same computed split plan. The generic kernel handles dataflow and recurrent inference. SQLite commits the resulting records. There is no version flag in the runtime and no native executable or WebAssembly module on the offline path.

## Results

| Execution path | Original | Review |
| --- | --- | --- |
| Compiled reference | 64/64 native HTTP interactions | 63/63 native HTTP interactions |
| Trained recurrent core | 64/64 native HTTP interactions | 63/63 native HTTP interactions |

All four runs also matched their teacher's final notes and tag memberships read directly from SQLite after clock normalization. Chrome verified creation, editing, links, tags, changes in available actions, preview and saved sections. A new process reopened the records after each run. The original and review browser projections started with the same SQLite bytes. The HTTP suites differ by one request because the review teacher refuses the River split and produces no child to reopen.

The two recurrent models also matched their compiled references on 2,321 token-sequence cases each, including every four-token sequence and longer sampled inputs up to 48 tokens. Fit examples covered transitions on prefixes up to three headings. This compares the recurrent mechanism on the chosen finite state representation. The native full-workspace holdouts independently compare behavior on actual documents, including fences and longer heading sequences. [Machine-readable results, runtime hashes and initial database hash](evidence/summary.json)

This experiment establishes a model-only workspace change and a learned recurrent implementation of the changed behavior. The direct translator covers the versioned rules. The trained controller covers their sequential decision. The common workspace graph still carries the other operations. Compiling an arbitrary Go application or learning a whole application graph requires further work.

## Run it

Node 24 runs the recorded artifacts. From the repository root:

```sh
versions=experiments/mynotes/versions
node experiments/mynotes/deck/host.mjs "$versions/evidence/original.recurrent.json" /tmp/notes-version.sqlite 55774
```

Open [the workspace](http://127.0.0.1:55774/). Stop the host and start it with `review.recurrent.json` and the same database to switch the version. For a comparison with the prepared Cedar and River data, run the offline verifier. It requires Python 3, curl, Playwright and Chrome.

```sh
versions=experiments/mynotes/versions
out=$(mktemp -d)
bash "$versions/verify.sh" "$versions/evidence" "$out/proof"
```

To rebuild the models and their native references, use the [pinned native build instructions](../README.md) to prepare a checkout with generated browser assets. The training process requires PyTorch.

```sh
versions=experiments/mynotes/versions
out=$(mktemp -d)
bash "$versions/build-lab.sh" "$MYNOTES_SOURCE" "$out/lab"
bash "$versions/verify.sh" "$out/lab" "$out/proof"
```

The scripts support `HOLODECK_NODE`, `HOLODECK_GO`, `HOLODECK_TRAIN_PYTHON`, `HOLODECK_PLAYWRIGHT` and `HOLODECK_CHROME` overrides. The verifier stops all Deck processes it starts and refuses to run if the known native or WebAssembly reference ports are still listening.
