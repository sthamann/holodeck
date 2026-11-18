"""Record candidates from the reader and scores from the explainer, then compose them over exact outcomes."""

import argparse
import json
from pathlib import Path

import torch

from adapter import Adapter, load_backbone
from compose import analyse, key, views_of
from deck import Deck
from tasks import build

ROOT = Path(__file__).resolve().parent


def chunks(items, size):
    return [items[index:index + size] for index in range(0, len(items), size)]


def record(reader, explainer, deck, views, samples, batch):
    """Per task and view: distinct programs with reader log probability and explainer log likelihood."""
    table = [[{} for _ in views] for _ in views[0]]
    for number, cohort in enumerate(views):
        for start in range(0, len(cohort), batch):
            part = cohort[start:start + batch]
            drawn = [reader.generate(part)] + [reader.generate(part, sample=True) for _ in range(samples)]
            for offset in range(len(part)):
                for attempt in drawn:
                    program, score = attempt[offset]
                    table[start + offset][number].setdefault(program, score)
    jobs = [(index, number, program) for index, row in enumerate(table) for number, found in enumerate(row)
            for program in found if key(deck, views[number][index]["state"], program) is not None]
    scores = {}
    for part in chunks(jobs, batch):
        values = explainer.loglik([{**views[number][index], "program": program} for index, number, program in part])
        scores.update(zip(part, values))
    return [[[[program, round(score, 4), None if (index, number, program) not in scores
               else round(scores[(index, number, program)], 4)] for program, score in found.items()]
             for number, found in enumerate(row)] for index, row in enumerate(table)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backbone")
    parser.add_argument("--reader", default=str(ROOT.parents[1] / ".holodeck" / "todomvc-adapter.pt"))
    parser.add_argument("--explainer", default=str(ROOT.parents[1] / ".holodeck" / "todomvc-explainer.pt"))
    parser.add_argument("--tasks", type=int, default=1000)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--batch", type=int, default=48)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    torch.manual_seed(args.seed)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    deck, models = Deck(), []
    for path, backward in ((args.reader, False), (args.explainer, True)):
        tokenizer, lm = load_backbone(args.backbone, device)
        model = Adapter(tokenizer, lm, deck)
        model.backward = backward
        model.load_state_dict(torch.load(path), strict=False)
        models.append(model.eval())
    cohorts = {"scored": build(args.seed, args.tasks, "test"), "weight selection": build(args.seed + 5, 200, "test")}
    recorded = {}
    for name, tasks in cohorts.items():
        recorded[name] = record(*models, deck, views_of(tasks, args.seed), args.samples, args.batch)
    (ROOT / "results" / "candidates.json").write_text(
        json.dumps({"seed": args.seed, "samples_per_view": args.samples, "tasks": {
            name: len(tasks) for name, tasks in cohorts.items()}, "candidates": recorded},
            ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps(analyse(deck, args.seed, cohorts, recorded), indent=1))


if __name__ == "__main__":
    main()
