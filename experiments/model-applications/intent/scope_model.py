"""Produce held-out scope scores with the pinned frozen LFM2.5 weights."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "foundation"))
from benchmark import LFM2_SHA
from grounding import features, fit, score
from lfm import LFM2
from transfer import logits

LABELS = ("final", "persistent", "both", "outside")
LETTERS = ("A", "B", "C", "D")
PROMPT = (
    "Classify a request about the location of sample in vault. "
    "A: required at completion only. B: required throughout the work. "
    "C: location required but time scope unspecified. D: another task or extra obligation.\n"
    "Request: When we finish, sample needs to be back in vault. Answer: A\n"
    "Request: Keep sample inside vault while the job runs. Answer: B\n"
    "Request: Please have sample in vault. Answer: C\n"
    "Request: Check who has the vault key. Answer: D\n"
    "Request: {text}\nAnswer:"
)


def records(fixtures, split):
    return [(text, label) for label in LABELS for text in fixtures[split][label]]


def train(model, rows):
    values = features(model, [text for text, _ in rows])
    labels = np.array([LABELS.index(label) for _, label in rows])
    per_label = len(labels) // len(LABELS)
    folds = np.tile(np.arange(per_label) % 4, len(LABELS))
    options = []
    for (layer, pool), matrix in values.items():
        for penalty in (0.01, 0.1, 1.0):
            errors = 0
            for fold in range(4):
                use = folds != fold
                learned = fit(matrix[use], labels[use], penalty, len(LABELS))
                errors += int(np.sum(score(matrix[~use], learned).argmax(axis=1) != labels[~use]))
            options.append((errors, layer, pool, penalty))
    errors, layer, pool, penalty = min(options)
    key = (layer, pool)
    return key, fit(values[key], labels, penalty, len(LABELS)), {
        "layer": layer, "pool": pool, "penalty": penalty, "train_cv_errors": errors,
    }


def margins(scores):
    return np.round(scores.max(axis=1, keepdims=True) - scores, 5).tolist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    with (args.snapshot / "model.safetensors").open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != LFM2_SHA:
            raise ValueError("Use the pinned LFM2.5-1.2B-Thinking model.")
    corpus = (HERE / "scope_cases.json").read_bytes()
    fixtures = json.loads(corpus)
    model = LFM2(args.snapshot)
    key, readout, configuration = train(model, records(fixtures, "train"))
    scores = {}
    for split in ("calibration", "holdout", "stress"):
        utterances = [text for text, _ in records(fixtures, split)]
        prompts = [PROMPT.format(text=text) for text in utterances]
        frozen = np.concatenate([logits(model, prompts[i:i + 4], LETTERS) for i in range(0, len(prompts), 4)])
        learned = score(features(model, utterances)[key], readout)
        scores[split] = {"frozen": margins(frozen), "trained": margins(learned)}
    print(json.dumps({"weights_sha256": LFM2_SHA, "corpus_sha256": hashlib.sha256(corpus).hexdigest(),
                      "configuration": configuration, "labels": LABELS, "scores": scores},
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
