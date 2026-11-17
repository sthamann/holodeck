"""Train one arm on generated contracts and score it on unseen applications."""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import random
import time

import torch

import holo
from fusion import DeckArm, HybridArm, TextArm, load_backbone
from reader import ReaderArm
from worlds import authored, build

ROOT = Path(__file__).resolve().parent
ARMS = {"text": ("text", False), "text+tool": ("text", True), "deck": ("deck", False), "deck+exec": ("deck", True),
        "text+exec": ("hybrid", True), "reader": ("reader", True)}


def flatten(cohort):
    return [(world, task) for world, tasks in cohort for task in tasks]


def chunks(items, size):
    return [items[index:index + size] for index in range(0, len(items), size)]


def decide(scores, task, masked):
    scores = scores.clone()
    if masked:
        for candidate, legal in enumerate(task["legal"]):
            if not legal:
                scores[candidate] = float("-inf")
    choice = int(scores.argmax())
    return choice in task["optimal"] if task["optimal"] else choice == len(task["legal"])


def tally(rows):
    """Accuracy overall and by the length of the shortest plan. Depth 0 means no plan exists."""
    by_depth = defaultdict(list)
    for depth, correct in rows:
        by_depth[depth].append(correct)
    return {"n": len(rows), "correct": sum(correct for _, correct in rows),
            "by_depth": {str(depth): [sum(values), len(values)] for depth, values in sorted(by_depth.items())}}


@torch.no_grad()
def evaluate(model, tasks, deck, batch_size, scene_limit):
    model.eval()
    report, rows = {}, defaultdict(list)
    tokens, seconds, steps, grounded, chance = 0, 0, 0, 0, 0
    for batch in chunks(tasks, batch_size):
        started = time.perf_counter()
        output = model(batch, scenes=[[] for _ in batch]) if deck else model(batch)
        if torch.backends.mps.is_available():
            torch.mps.synchronize()
        seconds += time.perf_counter() - started
        tokens += output["tokens"] * len(batch)
        variants = {"": output}
        if deck:
            steps += output["steps"]
            grounded += sum(output["goal_correct"])
            if model.executor:
                variants["severed "] = model(batch, scenes=[[] for _ in batch], sever=True)
                variants["true goal "] = model(batch, scenes=[[] for _ in batch], oracle_goal=True)
        for world, task in batch:
            legal = [index for index, ok in enumerate(task["legal"]) if ok]
            chance += len(set(legal) & set(task["optimal"])) / len(legal) if task["optimal"] else 0
        for label, result in variants.items():
            for (world, task), scores in zip(batch, result["answers"]):
                rows[label + "free"].append((task["depth"], decide(scores, task, False)))
                rows[label + "masked"].append((task["depth"], decide(scores, task, True)))
        if deck:
            for (world, task), goal in zip(batch, output["goals"]):
                plan = world.optimal(task["state"], task["actor"], goal)
                right = plan[0] in task["optimal"] if plan and task["optimal"] else not plan and not task["optimal"]
                rows["read goal then exhaustive search"].append((task["depth"], right))
    report["decisions"] = {label: tally(values) for label, values in rows.items()}
    report["uniform_choice_among_legal_options"] = round(chance / len(tasks), 4)
    report["context_tokens"] = round(tokens / len(tasks), 1)
    report["milliseconds"] = round(1000 * seconds / len(tasks), 2)
    if deck:
        report["goal_grounded"] = [grounded, len(tasks)]
        report["exact_steps_per_decision"] = round(steps / len(tasks), 1)
        if scene_limit:
            report["holo"] = evaluate_scenes(model, tasks[:scene_limit], batch_size)
    return report


@torch.no_grad()
def evaluate_reader(model, tasks, batch_size, scene_limit):
    """The reader states a goal and an answer. Exact search under the stated goal checks the answer."""
    model.eval()
    rows, tokens, seconds, chance, grounded, parsed = defaultdict(list), 0, 0, 0, 0, 0
    for batch in chunks(tasks, batch_size):
        started = time.perf_counter()
        output = model.run(batch)
        if torch.backends.mps.is_available():
            torch.mps.synchronize()
        seconds += time.perf_counter() - started
        tokens += output["tokens"] * len(batch)
        oracle = model.run(batch, oracle_goal=True)
        for index, (world, task) in enumerate(batch):
            legal = [candidate for candidate, ok in enumerate(task["legal"]) if ok]
            chance += len(set(legal) & set(task["optimal"])) / len(legal) if task["optimal"] else 0
            depth, goal = task["depth"], output["goals"][index]
            right = decide(output["answers"][index], task, True)
            rows["free"].append((depth, decide(output["answers"][index], task, False)))
            rows["masked"].append((depth, right))
            rows["true goal masked"].append((depth, decide(oracle["answers"][index], task, True)))
            parsed += goal is not None
            grounded += goal is not None and sorted(goal) == sorted(map(tuple, task["goal"]))
            plan = world.optimal(task["state"], task["actor"], goal) if goal is not None else None
            searched = goal is not None and (
                plan[0] in task["optimal"] if plan and task["optimal"] else not plan and not task["optimal"])
            rows["read goal then exhaustive search"].append((depth, searched))
            if output["agree"][index]:
                rows["committed when search under the stated goal agrees"].append((depth, right))
            else:
                rows["withheld, answer would have been"].append((depth, right))
    report = {"decisions": {label: tally(values) for label, values in rows.items()},
              "uniform_choice_among_legal_options": round(chance / len(tasks), 4),
              "context_tokens": round(tokens / len(tasks), 1),
              "milliseconds": round(1000 * seconds / len(tasks), 2),
              "goal_parsed": [parsed, len(tasks)], "goal_grounded": [grounded, len(tasks)]}
    if scene_limit:
        report["holo"] = evaluate_scenes(model, tasks[:scene_limit], batch_size)
    return report


@torch.no_grad()
def evaluate_scenes(model, tasks, batch_size):
    stats = defaultdict(int)
    for batch in chunks(tasks, batch_size):
        guided, _ = model.decode(batch, constrained=True)
        free, _ = model.decode(batch, constrained=False)
        for (world, task), scene, loose in zip(batch, guided, free):
            legal = [index for index, ok in enumerate(task["legal"]) if ok]
            facts = range(len(world.names))
            stats["scenes"] += 1
            stats["well formed and authorized"] += holo.wellformed(scene, facts, legal)
            stats["without grammar: well formed and authorized"] += holo.wellformed(loose, facts, legal)
            stats["matches reference designer"] += scene == holo.reference(world, task)
            shown = holo.controls(scene)
            if task["optimal"]:
                stats["tasks with a plan"] += 1
                marked = [scene[index + 1][1] for index, token in enumerate(scene[:-1]) if token == holo.PRIMARY]
                stats["primary control starts a shortest plan"] += bool(marked) and marked[0] in task["optimal"]
                position = next((index + 1 for index, control in enumerate(shown) if control in task["optimal"]),
                                len(legal) + 1)
                stats["controls scanned in Holo"] += position
                stats["controls scanned in generated form"] += next(
                    index + 1 for index, control in enumerate(legal) if control in task["optimal"])
            else:
                stats["tasks without a plan"] += 1
                stats["notice shown when no plan exists"] += scene[0] == holo.NOTICE
    return dict(stats)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("arm", choices=ARMS)
    parser.add_argument("backbone")
    parser.add_argument("--steps", type=int, default=1500)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--worlds", type=int, default=2000)
    parser.add_argument("--test-worlds", type=int, default=250)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--scene-limit", type=int, default=200)
    parser.add_argument("--out", default=str(ROOT / "results"))
    parser.add_argument("--save", default="")
    args = parser.parse_args()

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    kind, flag = ARMS[args.arm]
    tokenizer, lm = load_backbone(args.backbone, device)
    model = {"deck": DeckArm, "hybrid": HybridArm}[kind](tokenizer, lm, executor=flag) if kind in ("deck", "hybrid") \
        else {"text": TextArm, "reader": ReaderArm}[kind](tokenizer, lm, previews=flag)

    train = flatten(build(args.seed, args.worlds, "train"))
    cohorts = {
        "unseen contracts, names and phrasings": flatten(build(args.seed, args.test_worlds, "test")),
        "larger structure than training": flatten(build(args.seed, args.test_worlds // 2, "test", "larger")),
        "hand-written vertical contracts": flatten(authored()),
    }
    parameters = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(parameters, lr=2e-4, weight_decay=0.01)
    schedule = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: min(1, (step + 1) / 100) * (
        0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * min(1, step / args.steps)))))
    started, curve = time.time(), []
    model.train()
    for step in range(args.steps):
        batch = random.sample(train, args.batch)
        output = model(batch, teacher=0.5) if kind != "hybrid" else model(batch, scenes=[[] for _ in batch], teacher=0.5)
        loss = output["loss"] + (model.scene_loss(output, batch) if kind == "deck" else 0)
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(parameters, 1.0)
        optimizer.step()
        schedule.step()
        if step % 50 == 0 or step == args.steps - 1:
            curve.append([step, round(float(loss), 4)])
            print(f"{args.arm} step {step} loss {float(loss):.4f} {time.time() - started:.0f}s", flush=True)

    report = {
        "arm": args.arm, "backbone": Path(args.backbone).name, "seed": args.seed,
        "training": {"steps": args.steps, "batch": args.batch, "examples": args.steps * args.batch,
                     "worlds": args.worlds, "tasks": len(train), "seconds": round(time.time() - started),
                     "trainable_parameters": sum(p.numel() for p in parameters), "loss": curve},
        "cohorts": {name: {"tasks": len(tasks), **(
            evaluate_reader(model, tasks, args.batch, args.scene_limit) if kind == "reader" else
            evaluate(model, tasks, kind != "text", args.batch, args.scene_limit if kind == "deck" else 0))}
                    for name, tasks in cohorts.items()},
    }
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / f"{args.arm}.json").write_text(json.dumps(report, indent=1) + "\n")
    if args.save:
        torch.save({name: value for name, value in model.state_dict().items()
                    if not name.startswith("lm.") or name.endswith((".down", ".up"))}, args.save)
    print(json.dumps({name: {k: [v["correct"], v["n"]] for k, v in cohort["decisions"].items()}
                      for name, cohort in report["cohorts"].items()}, indent=1))


if __name__ == "__main__":
    main()
