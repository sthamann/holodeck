# Learn a second workflow without naming its states

This experiment takes the recurrent core from the [versioned split experiment](../versions/README.md) into MyNotes publication. It uses the pinned native MyNotes revision `73ef4b7b37109a5209d9fbcfbd5d3799918071be`. One checkpoint runs both workflows through the same categorical recurrence, with the previous split weights and a newly fitted publication bank. The split consumes heading tokens. Publication consumes a history of actions. [Architecture and scaling](../../../docs/recurrent-workflow-acquisition.md)

In Chrome, a new private note becomes a public page. Editing the draft leaves the public copy intact. After a full Deck restart, the Holo offers republish and unpublish. Republishing changes what the public reader sees. Unpublishing leaves the private note intact and the old public URL returns 404. A second restart preserves that withdrawal. Republishing and deleting the source also withdraws the public page. Creating a different note at the same address starts a fresh history.

[Private draft changed, public copy intact](evidence/draft-out-of-date.png) · [Public copy before republish](evidence/public-before-republish.png) · [Public copy after republish](evidence/public-after-republish.png)

## How it learned

1. **Observe the native app.** Four typed actions are exercised in every four-action order, for 256 training journeys and 1,024 mutation attempts. The capture observes private responses, public responses, exact SQLite snapshot effects and the private revision copied by each write. Chrome supplies five observed Holo-relevant views and actual button-to-request bindings. [Training traces](evidence/train.json.gz) · [Browser observations](evidence/browser.json)
2. **Discover the states.** The learner groups observed prefixes by visible behavior and all observed next actions. It checks that the resulting groups agree across the training traces. It infers four states and 16 transitions from 341 distinct prefixes. State count and identities are outputs of this step. The chosen observations are response statuses, SQLite effects and whether the private revision has changed since the observed snapshot. [Discovered structure](evidence/fit.json)
3. **Fit the same recurrent machine.** The trainer learns the next categorical state and the observable readouts. Readouts supply action status, observed durable effect, view heading and available controls. Field comparison identifies the snapshot title as a private note field, the HTML as the request body and its timestamp as the publication receipt's timestamp. The last is supplied by the runtime clock at execution. The builder lowers bindings and a typed adapter into the numeric artifact. [Learner](learn.py) · [Shared trainer](../recurrent_fit.py) · [Compiled adapter source](program.js)
4. **Run it with the original off.** The generic Deck runtime consumes the artifact. It replays the recorded action tokens through its existing recurrent operation to decide what an API action does and which controls the Holo shows. SQLite stores the private note, public snapshot and the accepted action history. Common note operations, typed API adapters, the generic renderer and the safe fragment grammar are supplied. The experiment does not acquire unrelated MyNotes features.

The tested profile is publication of plain text notes and escaped paragraph or heading fragments. Attachments, embedded media, complex HTML sanitization, authentication and production deployment are outside this profile.

## Evidence

Six reserved native journeys use fresh records and sequences of up to 12 actions. All **60 mutation results, 60 private reads, 60 public reads and 60 SQLite snapshot comparisons** matched after clock normalization. Chrome completed creation, publishing, a no-op edit, private editing, republishing, withdrawal, deletion and address reuse, across two host restarts. The same Holo then ran the previous recurrent split and reopened both saved sections. MyNotes and the earlier WebAssembly listeners were off.

A controlled intervention zeroed only the output layer of the publication weight bank. The same create and publish requests then produced a **404** at the public URL and a **Publish note** Holo. With the fitted weights the public URL returned **200** and the Holo showed **Published note**. [Results](evidence/summary.json) · [Reserved traces](evidence/holdout.json)

The same runtime and trainer now support a workflow whose states were discovered from observations. The grouping is qualified against the observed and reserved profile. It is not an equivalence proof for arbitrary sequences or another application.

## Run and verify

From the repository root, with Node 24:

```sh
publication=experiments/mynotes/publication
node experiments/mynotes/deck/host.mjs "$publication/evidence/checkpoint.json" /tmp/notes-publication.sqlite 55775
```

Open [the local workspace](http://127.0.0.1:55775/), create a note and select **Publish**. For the recorded comparison, use Python 3, curl, Playwright and Chrome:

```sh
publication=experiments/mynotes/publication
out=$(mktemp -d)
bash "$publication/verify.sh" "$publication/evidence" "$out/proof"
```

To reacquire the evidence, build the [pinned native source](../README.md) with its generated browser assets, install Acorn for the compiler and PyTorch for training, then run:

```sh
publication=experiments/mynotes/publication
out=$(mktemp -d)
bash "$publication/build-lab.sh" "$MYNOTES_BINARY" "$out/lab"
bash "$publication/verify.sh" "$out/lab" "$out/proof"
```

The capture script checks the binary's native Go revision metadata. For a rebuild from the archived observations, unpack `evidence/train.json.gz` and run `learn.py TRAIN_JSON BROWSER_JSON FITTED_JSON REPORT_JSON` with the PyTorch environment, then `node build.mjs FITTED_JSON CHECKPOINT`. Set `HOLODECK_NODE`, `HOLODECK_GO`, `HOLODECK_TRAIN_PYTHON`, `HOLODECK_ACORN`, `HOLODECK_PLAYWRIGHT` or `HOLODECK_CHROME` to override local defaults.
