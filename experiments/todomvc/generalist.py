"""Probe: can the untrained backbone pick the right outcome by reading the Deck's English descriptions?

The Deck lists every single statement it would accept and describes what each would do. The frozen
model scores how well each description explains the request. No adapter and no training is involved.
"""

import argparse
from collections import Counter
import json
from pathlib import Path

import torch

from adapter import Adapter, load_backbone
from compose import key
from deck import Deck
from describe import describe, enumerate_programs
from intent import screen
from tasks import build

ROOT = Path(__file__).resolve().parent
TEXT_KINDS = ("add", "rename", "missing", "empty")


@torch.no_grad()
def pick(model, deck, task, programs):
    view = screen(deck, task["state"], numbered=True)
    scores = model.loglik([{**task, "prompt": f"{view}\nWhat the app just did: {describe(deck, task['state'], program)}\n"
                            "The user had asked for this by saying:"} for program in programs])
    return programs[max(range(len(programs)), key=scores.__getitem__)]


class Scorer(Adapter):
    def prompt(self, task):
        return self.ids(task["prompt"])

    def target(self, task):
        return task["request"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backbone")
    parser.add_argument("--tasks", type=int, default=150)
    args = parser.parse_args()
    deck = Deck()
    tokenizer, lm = load_backbone(args.backbone, "mps" if torch.backends.mps.is_available() else "cpu")
    model = Scorer(tokenizer, lm, deck).eval()
    single = [task for task in build(9, 600, "test") if ";" not in task["program"] and "category" not in task["kind"]]
    report = {}
    for name, with_text in (("all single statements", True), ("statements without free text", False)):
        tasks = [task for task in single if with_text or task["kind"] not in TEXT_KINDS][:args.tasks]
        right, total, offered = Counter(), Counter(), 0
        for task in tasks:
            programs = [program for program in enumerate_programs(deck, task["state"], task["request"])
                        if with_text or not program.startswith(("add", "rename", "unclear"))]
            wanted = json.dumps(task["outcome"], ensure_ascii=False)
            offered += wanted in [key(deck, task["state"], program) for program in programs]
            total[task["kind"]] += 1
            right[task["kind"]] += key(deck, task["state"], pick(model, deck, task, programs)) == wanted
        report[name] = {"tasks": len(tasks), "right outcome among the Deck's candidates": offered,
                        "frozen model picks it": sum(right.values()),
                        "by_kind": {kind: [right[kind], total[kind]] for kind in sorted(total)}}
    (ROOT / "results" / "generalist-probe.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
