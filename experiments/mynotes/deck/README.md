# Standalone notes Deck

This experiment replaces the MyNotes application path for creating, editing, tagging, linking, finding and splitting notes. The API and a generated browser workspace operate on the same durable records. MyNotes at revision `73ef4b7b37109a5209d9fbcfbd5d3799918071be` supplies calibration and comparison data during verification.

## Run the Deck

From the repository root with Node 24:

```sh
deck=experiments/mynotes/deck
node "$deck/host.mjs" "$deck/checkpoint.json" /tmp/holodeck-notes.sqlite 55770
```

Open `http://127.0.0.1:55770`. The runtime files are `host.mjs`, `kernel.mjs`, `grounding.mjs`, the `web` directory and the fitted checkpoint. Node supplies HTTP and SQLite. The model authoring files, training code, original server, WebAssembly and Python are absent from the isolated runtime used for verification.

## What executes

```text
API request or browser interaction
           |
       Typed inputs + SQLite record snapshot
           |
       Exact forward over checkpoint rows
          / \
HTTP result + atomic effects    Holo component tree + API bindings
          |                              |
        SQLite                     Generic DOM renderer
```

- **The model artifact** contains the routes, operation selectors, operand addresses, callable routines and projection structure. The shared rules handle normalization and allocation, tag membership, forward and backward links, revisions, and Markdown section boundaries. The model emits one transaction for a split, including every new note.
- **The kernel** reads sparse one hot selectors over a fixed vocabulary of record, string, numeric, branching and collection operations. It has no note, tag, or split instruction. The host supplies the clock, a read snapshot, transaction isolation and atomic record effects.
- **The Holo** is recomposed for the requested task and live data. The model emits editor fields, valid tag choices, inline link bindings, split previews and request bindings. For example, it emits the current revision for the edit form and uses the same section routine in the preview and write path. The DOM renderer handles component rendering and transport.
- **Acquisition** starts from an authored graph in `parameters.mjs`. The compiler translates it to sparse rows. Three native observations distinguish eight candidate assignments for three selectors. The fitted choices govern automatic suffixes, stale edits and the level at which a note is split. The broader graph and the projection vocabulary are authored. This is a discrete specialist experiment. Qwen and learned Holo composition remain open.

## What passed

- **26 HTTP responses and the persisted workspace** matched the original on a separate workspace after wall clock normalization. The test checks status and bodies, verifies ETags on reads and edits, and inspects SQLite directly. A deliberately advanced test revision verifies the exact one millisecond increment.
- **Chrome** created and deleted notes and tags, added and removed tag assignments and links, followed inline links and backlinks, handled a stale edit, previewed and executed a split, searched by text and tag, and checked the narrow layout. The runtime was then stopped and restarted against the same database. Saved notes, tags and backlink results were still available.
- **One changed parameter** moved the split boundary from the shallowest heading to the deepest. On the same input, the fitted checkpoint previewed and saved `Agenda` and `Decisions`. The altered checkpoint previewed and saved `Details`. Both runs used the same runtime, renderer and API bindings.

The compatibility result covers the tested workspace and Markdown forms. It does not establish parity for every Markdown extension, front matter, advanced full text search, publishing, authentication or performance at scale.

[Selector fit](evidence/fit.json) · [Native transcript](evidence/native.json) · [Offline transcript](evidence/replay.json) · [Later Qwen experiment](../learned/README.md)

## Reproduce the comparison

Build the [pinned native reference](https://github.com/mikaelstaldal/mynotes/tree/73ef4b7b37109a5209d9fbcfbd5d3799918071be) using its upstream build instructions. The verification command additionally uses Python 3, curl, Playwright and Chrome. Set `HOLODECK_NODE`, `HOLODECK_PLAYWRIGHT` and `HOLODECK_CHROME` to override the local executables and module location.

```sh
out=$(mktemp -d)
bash experiments/mynotes/deck/verify.sh "$MYNOTES_BINARY" "$out/run"
cmp experiments/mynotes/deck/checkpoint.json "$out/run/runtime/weights.json"
```

The runner captures training observations, fits the candidate parameters, starts a fresh native workspace for the comparison, then stops the reference. It starts the Deck in a separate directory with only its deployable files. The output includes the fit report, the API transcripts, database files and desktop and mobile browser captures.
