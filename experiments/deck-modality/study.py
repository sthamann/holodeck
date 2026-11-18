"""Score a saved reader on looser requests, on requests without a goal and on scenes built from the verified plan."""

import argparse
from collections import defaultdict
import json
from pathlib import Path

import torch

from fusion import load_backbone
from phrasings import restate, without_goal
from reader import ReaderArm
from train import chunks, evaluate_reader, evaluate_scenes, flatten
from worlds import authored, build

ROOT = Path(__file__).resolve().parent


@torch.no_grad()
def refusals(model, tasks, batch_size):
    """Every commit on a request without a goal is a false commit."""
    stats = defaultdict(lambda: defaultdict(int))
    examples = defaultdict(list)
    for batch in chunks(tasks, batch_size):
        output = model.run(batch)
        for index, (world, task) in enumerate(batch):
            row = stats[task["kind"]]
            row["requests"] += 1
            row["goal stated in the contract's terms"] += output["goals"][index] is not None
            row["committed"] += output["agree"][index]
            if len(examples[task["kind"]]) < 4:
                examples[task["kind"]].append([task["request"], output["goal_texts"][index]])
    return {kind: {**row, "examples": examples[kind]} for kind, row in stats.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backbone")
    parser.add_argument("--checkpoint", default=str(ROOT.parents[1] / ".holodeck" / "reader.pt"))
    parser.add_argument("--name", default="reader-study")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--batch", type=int, default=16)
    args = parser.parse_args()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    tokenizer, lm = load_backbone(args.backbone, device)
    model = ReaderArm(tokenizer, lm)
    model.load_state_dict(torch.load(args.checkpoint), strict=False)
    model.eval()

    unseen = flatten(build(args.seed, 250, "test"))
    cohorts = {"unseen contracts, names and phrasings": unseen,
               "larger structure than training": flatten(build(args.seed, 125, "test", "larger")),
               "hand-written vertical contracts": flatten(authored())}
    report = {"arm": args.name, "checkpoint": Path(args.checkpoint).name}
    report["looser requests on the unseen cohort"] = {
        "tasks": len(unseen), **evaluate_reader(model, restate(unseen, args.seed), args.batch, 0)}
    report["requests without a goal"] = refusals(model, without_goal(unseen, args.seed), args.batch)
    model.plan = True
    report["scenes built from the verified decision"] = {
        name: evaluate_scenes(model, tasks[:200], args.batch) for name, tasks in cohorts.items()}
    (ROOT / "results" / f"{args.name}.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
