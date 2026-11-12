# MyNotes application transfer

[Learn the publication workflow](publication/README.md) uses the same recurrent core and discovers its state space from native API, browser and SQLite evidence. The offline Holo manages private edits, public snapshots, republication and withdrawal.

[One workspace, two model versions](versions/README.md) compares a source compiled policy with a trained recurrent core. Swapping only the model changes which actions the Holo offers, what it previews and what is persisted.

[Qwen and native MyNotes traces](learned/README.md) records the next experiment. The measured mixed provenance Deck adds free intent grounding, a guided generated Holo and generated tag operations.

The [standalone notes Deck](deck/README.md) runs the covered workspace entirely through model parameters with a generated Holo. The WebAssembly comparison below remains an earlier control.

This experiment takes the notes workspace from [MyNotes at 73ef4b7](https://github.com/mikaelstaldal/mynotes/tree/73ef4b7b37109a5209d9fbcfbd5d3799918071be) through a connected browser, API and database path. The native server acts as the reference and is shut down before both transferred builds run.

| Build | Application behavior | Browser interface |
| --- | --- | --- |
| Original | Native MyNotes | Original bundled interface |
| Source | The original handler, services and repositories compiled to WebAssembly | The same bundled interface |
| Weights | The same WebAssembly application with repository revision acceptance and next revision computed by an exact parameter circuit | The same bundled interface |

Both transferred builds use the same local host for HTTP, WebAssembly execution, static assets and SQLite. The guest runs the original OpenAPI handlers, requests SQL and decides what to return. The host does not implement MyNotes note, tag or link rules. The compiler reads the relevant Go source and emits the circuit selectors. The weight build removes the corresponding repository branches and calls the fixed forward function at that point in the transaction. The HTTP handler and earlier service validation still run as compiled source. Weights are produced by compilation. Qwen, gradient training and Holo generation are open work.

### Connected verification

Both offline builds matched **26 native HTTP responses and four persisted SQLite tables**. Independent clocks were normalized for comparison, and a deliberately seeded future revision exercised the increment rule at an exact millisecond. The same journey checks creation, edits across title, body and tags, stale edits, no-op edits, search, backlinks, a split with a slug collision, Markdown import and export, deletion and final records. Chrome drives the original interface against each offline build and the harness verifies the browser edit after a host restart.

The circuit has a counterfactual check on the real write path. Changing just the clock gate's operation selector from less than or equal to equality causes the same API request to write the wall clock instead of advancing the seeded future revision. The unchanged original database behavior and the mutation outcome give an independently testable distinction between source execution and parameter execution.

### Run both builds

Build the pinned native MyNotes binary and its generated API and web assets with the [upstream development instructions](https://github.com/mikaelstaldal/mynotes/blob/73ef4b7b37109a5209d9fbcfbd5d3799918071be/docs/DEVELOPMENT.md). The host uses Go 1.26.10. Verification uses Python 3, Node, Playwright and Chrome. Set `HOLODECK_PLAYWRIGHT` or `HOLODECK_CHROME` if needed.

```sh
workspace=experiments/mynotes/workspace
out=$(mktemp -d)
bash "$workspace/build.sh" "$MYNOTES_SOURCE" "$out/source" source
bash "$workspace/build.sh" "$MYNOTES_SOURCE" "$out/weights" weights
HOLODECK_TEST_WEIGHT_MUTATION=1 bash "$workspace/verify.sh" \
  "$MYNOTES_BINARY" "$out/source" "$out/weights" "$out/evidence"
```

This is a local research build with a serial guest and a single SQLite connection. It tests the shared notes workspace. Authentication, public pages, full deployment parity, concurrent load and independent browser surfaces have no acceptance result here.

The later [standalone Deck](deck/README.md) put the tested note operations and Holo bindings into one artifact. The acquisition question is now whether a learner can recover the entire connected graph from evidence. The native and source builds remain the controls. [Research status](../../docs/research-status.md)

## Earlier title edit probe

The first profile is a conditional title edit of an existing note in [MyNotes at 73ef4b7](https://github.com/mikaelstaldal/mynotes/tree/73ef4b7b37109a5209d9fbcfbd5d3799918071be). The title is valid and already normalized. The request has an `If-Match` header. Content and tags stay unchanged. The native editor sends content and tags even for a title edit. This probe admits that request when the content is identical and both tag sets are empty.

## What executes

`compile.go` parses the pinned Go source and requires the matching title-diff, conditional version, no-op and repository guard shapes. It lowers this specific control-flow slice to a fixed-size parameter circuit. It recognizes the native timestamp guard and one deliberate comparison mutation. It is a profile-specific source frontend.

The Rust `circuit` binary loads the one-hot operation, operand and output masks. A single fixed integer forward computes HTTP decision, write/no-write, resulting version and selected title. The replay adapter parses timestamps, interns strings as exact IDs and stores the predicted record in a separate SQLite file. Python performs recording, transport normalization, SQLite persistence and comparison. The rule decisions come from Rust. The adapter deliberately restricts the experiment to the declared profile.

## Evidence

The pinned MyNotes handler, service and repository tests passed locally. The original binary ran on loopback with a fresh SQLite database. We recorded 24 API cases and one real browser title edit. Each native result was checked by GET and by reading the actual SQLite row. The cases cover writes, no-ops, stale versions and stale versions on an otherwise unchanged request.

With the original server stopped, the circuit matched the status, write/no-write decision and non-clock persisted fields in **25 of 25** cases. The stored timestamp matched exactly in 23 cases. In two successful writes it fell in the observed request-time interval because the source's internal clock reading was not captured. Some seeded test records are ahead of the current clock, forcing the real application to use its monotonic increment. The offline records survived a new SQLite connection.

A source-only mutation changes the equality case of the timestamp guard. Recompilation changed a single operation selector. The same Rust binary returned 401 for the original version and 400 for the mutant when the stored value and clock both equaled 400. A simulated change between initial read and commit returned 412. An unchanged title remained a no-op in that same simulation.

This earlier probe replays the title profile directly against the Rust forward function and storage adapter. The workspace build above adds the live HTTP and browser path.

### Reproduce the title probe

Build and run the pinned original with its [upstream development instructions](https://github.com/mikaelstaldal/mynotes/blob/73ef4b7b37109a5209d9fbcfbd5d3799918071be/docs/DEVELOPMENT.md). Use a fresh synthetic database and bind to `127.0.0.1:55766`. The recorder inserts fixture notes and can seed future versions for the clock test.

From the Holodeck root, with a working Go toolchain and the Rust dependencies:

```sh
experiment=experiments/mynotes
out=$(mktemp -d)
go run "$experiment/compile.go" "$MYNOTES_SOURCE" > "$out/checkpoint.json"
cargo build --locked --release --manifest-path experiments/model-core/Cargo.toml --bin circuit
python3 "$experiment/replay.py" capture "$MYNOTES_DB" "$out/traces.json"
```

Stop the original. The offline runner refuses to run while the reference port is listening.

```sh
python3 "$experiment/replay.py" replay "$out/traces.json" \
  experiments/model-core/target/release/circuit "$out/checkpoint.json" \
  "$out/offline.sqlite" "$out/report.json"
```

For the extra browser capture, run `node experiments/mynotes/capture-browser.cjs http://127.0.0.1:55766 "$out/browser.json"` before stopping the teacher. It uses the installed Playwright package, with optional `HOLODECK_PLAYWRIGHT` and `HOLODECK_CHROME` overrides. The resulting trace row can be appended to the JSON trace array before replay.
