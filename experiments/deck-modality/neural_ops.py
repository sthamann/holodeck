"""Neural operators: calibrated model judgements enter a Deck as journaled facts.

A rule may depend on a judgement such as "is this message urgent". The backbone answers with a
calibrated set of labels. The Host commits only when every retained label leads to the same writes
and effects. The judgement is stored with the commit, so a replay never calls the model.
"""

import hashlib
import json
from itertools import product
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "model-applications" / "vertical"))

from compiler import Model, canonical, compile_contract  # noqa: E402

SUPPORT = {
    "initial_revision": 1, "revisions": {"1": 1},
    "fields": {"open": [0, 3, 3], "paged": [0, 3, 0], "refunds": [0, 3, 0]},
    "public": ["open", "paged", "refunds"], "actors": {"desk": 0},
    "actions": {"resolve": {
        "label": "Resolve ticket", "args": {"urgent": [0, 1], "refund": [0, 1]},
        "guard": ["and", ["le", 1, "open"],
                  ["and", ["le", ["add", "paged", "urgent"], 3], ["le", ["add", "refunds", "refund"], 3]]],
        "writes": {"open": ["sub", "open", 1], "paged": ["add", "paged", "urgent"],
                   "refunds": ["add", "refunds", "refund"]},
        "effects": [["page", "urgent"], ["refund", "refund"]]}},
}


def calibrate(scores, labels, fit, calibration, alpha):
    """Split conformal threshold on signed margins. Returns the offset and the admitted nonconformity."""
    positive = [scores[i] for i in fit if labels[i]]
    negative = [scores[i] for i in fit if not labels[i]]
    offset = (sum(positive) / len(positive) + sum(negative) / len(negative)) / 2
    errors = sorted(-(1 if labels[i] else -1) * (scores[i] - offset) for i in calibration)
    rank = min(len(errors), -(-(len(errors) + 1) * (1000 - round(alpha * 1000)) // 1000))
    return offset, errors[rank - 1]


def label_set(score, offset, threshold):
    return [label for label, sign in ((0, -1), (1, 1)) if -sign * (score - offset) <= threshold]


class Desk:
    """A Host slice with a judgement journal. State, receipts and judgements share one append-only file."""

    def __init__(self, path, judge=None, model_digest=""):
        self.path, self.judge, self.model_digest = Path(path), judge, model_digest
        self.model = Model(compile_contract(SUPPORT))
        self.state = {"revision": 1, "fields": {name: bounds[2] for name, bounds in SUPPORT["fields"].items()}}
        self.facts, self.receipts, self.calls = {}, {}, 0
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                self.apply(json.loads(line))

    def apply(self, record):
        if record["kind"] == "judgement":
            self.facts[record["key"]] = record
        else:
            self.state["fields"].update(record["writes"])
            self.receipts[record["key"]] = record

    def append(self, record):
        with self.path.open("a") as handle:
            handle.write(canonical(record) + "\n")
        self.apply(record)

    def judgement(self, message, criterion):
        """Return the journaled fact when it exists. Only a missing fact reaches the model."""
        key = hashlib.sha256(f"{criterion}\0{message}".encode()).hexdigest()
        if key not in self.facts:
            if self.judge is None:
                raise LookupError("judgement is not in the journal and no model is attached")
            self.calls += 1
            self.append({"kind": "judgement", "key": key, "criterion": criterion,
                         "model": self.model_digest, "labels": self.judge(message, criterion)})
        return self.facts[key]

    def resolve(self, message, key):
        if key in self.receipts:
            return self.receipts[key]["result"]
        sets = {name: self.judgement(message, name)["labels"] for name in ("urgent", "refund")}
        proposals = {}
        for urgent, refund in product(sets["urgent"], sets["refund"]):
            proposal = self.model.propose(self.state, "resolve", "desk", {"urgent": urgent, "refund": refund})
            proposals[canonical(proposal)] = proposal
        if len(proposals) != 1 or next(iter(proposals.values())) is None:
            open_questions = [name for name, labels in sets.items() if len(labels) != 1]
            return {"status": "withheld", "ask": open_questions}
        proposal = next(iter(proposals.values()))
        result = {"status": "committed", "effects": proposal["effects"]}
        self.append({"kind": "commit", "key": key, "writes": proposal["writes"], "result": result,
                     "judgements": sorted(hashlib.sha256(f"{name}\0{message}".encode()).hexdigest()
                                          for name in sets)})
        return result
