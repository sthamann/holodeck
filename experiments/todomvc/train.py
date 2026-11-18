"""Distil the TodoMVC Deck into an adapter and score the programs it writes on unseen lists and wordings."""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import random
import time

import torch

from adapter import Adapter, load_backbone
from deck import Deck
from intent import compile_program, outcome, parse, run_actions, screen
from tasks import build, draw_task, relabel

ROOT = Path(__file__).resolve().parent


def chunks(items, size):
    return [items[index:index + size] for index in range(0, len(items), size)]


GUIDE = """You operate a todo list. Answer with a program on one line. Statements are separated by "; ".
add "title" | complete #id ... | reopen #id ... | delete #id ... | rename #id "title" | complete all |
reopen all | delete completed | show all|active|completed | unclear (when the request names nothing you can do)

"""


def judge(deck, task, text, actions=False):
    """`right`, `wrong` or `refused` for one written program, judged by the state it leads to."""
    if actions:
        compiled = run_actions(deck, task["state"], text)
    else:
        statements = parse(text)
        compiled = compile_program(deck, task["state"], statements) if statements is not None else None
    if compiled is None:
        return "refused", None
    return ("right" if outcome(compiled[1]) == task["outcome"] else "wrong"), outcome(compiled[1])


@torch.no_grad()
def evaluate(model, deck, tasks, batch, seed):
    model.eval()
    rng = random.Random(f"relabel:{seed}")
    variants = [tasks, [relabel(rng, task) for task in tasks], [relabel(rng, task) for task in tasks]]
    texts, tokens = [], 0
    for cohort in variants:
        written = []
        for part in chunks(cohort, batch):
            programs, size = model.write(part)
            written += programs
            tokens += size * len(part)
        texts.append(written)
    by_kind = defaultdict(lambda: defaultdict(int))
    totals, rows = defaultdict(int), []
    for index, task in enumerate(tasks):
        verdicts = [judge(deck, cohort[index], text[index], model.actions) for cohort, text in zip(variants, texts)]
        verdict, result = verdicts[0]
        by_kind[task["kind"]]["tasks"] += 1
        by_kind[task["kind"]][verdict] += 1
        totals[verdict] += 1
        totals["program identical to reference"] += texts[0][index] == model.target(task)
        changes = result is not None and result != outcome(task["state"])
        totals["writes a change"] += changes
        # Same outcome under three labelings of the rows: commit without asking.
        agree = all(v[1] is not None and v[1] == result for v in verdicts)
        if agree and changes:
            totals["agreement: committed"] += 1
            totals["agreement: committed wrong"] += verdict == "wrong"
        rows.append([task["request"], texts[0][index], verdict, agree])
    return {"tasks": len(tasks), **dict(totals), "prompt_tokens": round(tokens / (3 * len(tasks)), 1),
            "by_kind": {kind: dict(row) for kind, row in sorted(by_kind.items())}}, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backbone")
    parser.add_argument("--steps", type=int, default=1200)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--tasks", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--name", default="adapter")
    parser.add_argument("--save", default="")
    parser.add_argument("--actions", action="store_true", help="control: write the application's actions directly")
    parser.add_argument("--backward", action="store_true", help="train the explaining expert and only save it")
    parser.add_argument("--shots", type=int, default=0, help="control: no training, this many examples in the prompt")
    args = parser.parse_args()
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer, lm = load_backbone(args.backbone, device)
    deck = Deck()
    prefix = ""
    if args.shots:
        args.steps = 0
        shots = random.Random(f"shots:{args.seed}")
        examples = [draw_task(shots, ("train", "train"), deck) for _ in range(args.shots)]
        prefix = GUIDE + "".join(f"{screen(deck, task['state'])}\nRequest: {task['request']}\n"
                                 f"Program: {task['program']}\n\n" for task in examples)
    model = Adapter(tokenizer, lm, deck, actions=args.actions, prefix=prefix)
    model.backward = args.backward
    parameters = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(parameters, lr=2e-4, weight_decay=0.01)
    schedule = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: min(1, (step + 1) / 100) * (
        0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * min(1, step / max(1, args.steps))))))
    rng, started, curve = random.Random(f"train:{args.seed}"), time.time(), []
    model.train()
    for step in range(args.steps):
        loss = model([draw_task(rng, ("train", "train"), deck) for _ in range(args.batch)])
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(parameters, 1.0)
        optimizer.step()
        schedule.step()
        if step % 50 == 0 or step == args.steps - 1:
            curve.append([step, round(float(loss.detach()), 4)])
            print(f"step {step} loss {float(loss.detach()):.4f} {time.time() - started:.0f}s", flush=True)
    if args.backward:
        torch.save({name: value for name, value in model.state_dict().items() if name.endswith((".down", ".up"))},
                   args.save)
        return
    cohorts = {"unseen titles, looser wording": build(args.seed, args.tasks, "test"),
               "unseen titles, training wording": build(args.seed, args.tasks // 2, "train", "test"),
               "training titles, training wording": build(args.seed + 1, args.tasks // 2, "train")}
    report = {"arm": args.name, "backbone": Path(args.backbone).name, "seed": args.seed,
              "training": {"steps": args.steps, "batch": args.batch, "examples": args.steps * args.batch,
                           "seconds": round(time.time() - started),
                           "trainable_parameters": sum(p.numel() for p in parameters), "loss": curve},
              "cohorts": {}}
    stated = {}
    for name, tasks in cohorts.items():
        report["cohorts"][name], stated[name] = evaluate(model, deck, tasks, args.batch, args.seed)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / f"{args.name}.json").write_text(json.dumps(report, indent=1) + "\n")
    (ROOT / "results" / f"{args.name}-programs.json").write_text(json.dumps(stated, indent=0, ensure_ascii=False) + "\n")
    if args.save:
        torch.save({name: value for name, value in model.state_dict().items() if name.endswith((".down", ".up"))},
                   args.save)
    print(json.dumps({name: {key: value for key, value in cohort.items() if key != "by_kind"}
                      for name, cohort in report["cohorts"].items()}, indent=1))


if __name__ == "__main__":
    main()
