"""Probe a manifest rank reversal with unchanged admissibility and exact outcome optimization."""

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from benchmark import HERE, LFM2_SHA, REVISION, TINYLLAMA_SHA, action, token_id
from compiler import Model, canonical, compile_contract, require
from grounding import GOAL_NAMES, TRANSFER, readout_scores, train_readout
from llama import TinyLlama
from lfm import LFM2
from preferences import board, invoke_certified, minima, resolve, typed_projection, verify_selection
from runtime import Host
from storage import Relational


OPTIONS = {
    "delivery": {"courier": [action("courier")], "relay": [action("relay")]},
    "compute": {"interactive": [action("warm"), action("run")], "batch": [action("batch")]},
}
FIRST_FIXTURE = {
    "time": (
        "I need it finished as quickly as possible.", "Prioritize the shortest elapsed time.",
        "Choose whichever will complete first, even if it costs more.",
        "Getting the result sooner matters more than spending less.",
    ),
    "price": (
        "Pick the lowest price.", "I need to keep the total charge as low as possible.",
        "Save my budget, even if completion takes longer.",
        "Spending less matters more than getting the result sooner.",
    ),
    "unknown": (
        "Either quick or inexpensive would be nice. I have not chosen which to prioritize.",
        "Make a reasonable choice. I haven't stated a preference.",
        "I need it soon without spending much, with no priority specified.",
        "If there is a tradeoff, ask me which matters more.",
    ),
}
GOALS = {"time": (1, 1), "price": (0, 0), "unknown": (0, 1)}
ROUTER_PROMPT = (
    "Classify the user's most important objective. A means finish in the least time. "
    "B means pay the lowest price. C means no clear priority or ask.\n"
    "User: I need the faster option. Answer: A\n"
    "User: The best bargain is what I care about. Answer: B\n"
    "User: Both speed and cost matter; ask before trading one for the other. Answer: C\n"
    "User: {request}\nAnswer:"
)
DIRECT_DEMOS = (
    "Request: Do the quickest one.\nA: time 8, price 2.\nB: time 4, price 7.\nAnswer: B\n"
    "Request: Spend as little as possible.\nA: time 8, price 2.\nB: time 4, price 7.\nAnswer: A\n"
    "Request: Both priorities matter equally for now, ask if they disagree.\n"
    "A: time 8, price 2.\nB: time 4, price 7.\nAnswer: C\n"
)


def logits(model, prompts, labels):
    tokenizer = model.tokenizer
    ids = [tokenizer.encode(prompt) for prompt in prompts]
    width = max(map(len, ids))
    read = [len(row) - 1 for row in ids]
    padded = [row + [2] * (width - len(row)) for row in ids]
    prefix = " " if isinstance(model, LFM2) else ""
    return model.forward(padded, [token_id(tokenizer, prefix + label) for label in labels], read=read)


def choices(model, requests, result, names, disclose, demos=False, batch=6):
    labels = ("A", "B", "C")
    entries = []
    for label, name in zip(labels, names):
        facts = result[name]
        content = f"{name}: time {facts['time']}, price {facts['price']}." if disclose else name + "."
        entries.append(f"{label}: {content} Allowed: yes.")
    rows = [
        "Choose the best plan for the user. Smaller time and price are better for the relevant goal. "
        "If unspecified and the plans trade time for price, answer C to ask a question.\n"
        + (DIRECT_DEMOS if demos else "")
        + f"Request: {request}\n" + "\n".join(entries) + "\nC: Ask which to prioritize.\n"
        "Answer A, B or C. Answer:"
        for request in requests
    ]
    prediction = np.concatenate([logits(model, rows[i:i + batch], labels) for i in range(0, len(rows), batch)])
    return [labels[index] for index in prediction.argmax(axis=1)]


def independently_expected(contract, revision, goal):
    primary, secondary = tuple(OPTIONS[contract])
    first, second = [(2, 5), (5, 2), (8, 5)][revision - 1], (4, 3)
    if goal == "unknown" and revision in (1, 2):
        return "clarify"
    axis = 1 if goal == "price" else 0
    return primary if first[axis] < second[axis] else secondary


def exact_outcome(result):
    return result.get("name", result["status"])


def separation_law():
    """Check a separate finite grid in addition to the endpoint derivation."""
    examined = 0
    for index in range(81):
        values = [(index // (3 ** i)) % 3 for i in range(4)]
        result = {name: {"time": values[2 * j], "price": values[2 * j + 1], "steps": []}
                  for j, name in enumerate(("first", "second"))}
        for left in range(5):
            for right in range(left, 5):
                lower, upper = Fraction(left, 4), Fraction(right, 4)
                at_every_grid_point = set.intersection(
                    *(minima(result, Fraction(value, 60)) for value in range(15 * left, 15 * right + 1))
                )
                result_type = resolve(result, (lower, upper))
                require((result_type["status"] == "selected") == bool(at_every_grid_point),
                        "endpoint intersection disagrees with finite grid")
                if at_every_grid_point:
                    proof = result_type["proof"]
                    require(result_type["name"] in at_every_grid_point and
                            verify_selection(result, (lower, upper), proof), "wrong invariant choice")
                    for other in result.keys() - at_every_grid_point:
                        altered = {**proof, "choice": other}
                        require(not verify_selection(result, (lower, upper), altered),
                                "accepted a false certificate at an interior tradeoff")
                examined += 1
    return examined


def commit_typed(root, deck, revision, solution, result, plans, interval):
    host = Host(Relational(root), deck)
    if revision > 1:
        host.activate(revision, 0)
    snapshot = host.snapshot()
    scene = typed_projection({"actor": "operator", "epoch": snapshot["epoch"], "interval": interval},
                             solution, result)
    if solution["status"] == "selected":
        receipt = invoke_certified(host, "operator", plans, interval, scene, "choice")
        require(receipt["status"] == "committed", "typed plan did not commit")
        observed = {kind: sum(effect["value"] for effect in receipt["effects"] if effect["kind"] == kind)
                    for kind in ("time", "price")}
        require(observed == {"time": scene["time"], "price": scene["price"]},
                "rendered effect disagreed with durable execution")
    else:
        require(host.snapshot() == snapshot, "clarification changed durable state")


def summarize(rows, arms):
    pairs = {}
    for row in rows:
        if row["goal"] != "unknown" and row["revision"] < 3:
            pairs.setdefault((row["contract"], row["reversed"], row["utterance"]), []).append(row)
    require(all(len({row["expected"] for row in group}) == 2 for group in pairs.values()), "rank reversal lost")
    return {
        "cases": len(rows), "rank_reversal_pairs": len(pairs),
        "results": {
            arm: {
                "exact": sum(row[arm] == row["expected"] for row in rows),
                "rank_reversal_pairs": sum(all(row[arm] == row["expected"] for row in pair)
                                           for pair in pairs.values()),
                "correct_clarifications": sum(row[arm] == "clarify" == row["expected"] for row in rows),
                "unwanted_commits": sum(row[arm] != "clarify" == row["expected"] for row in rows),
            } for arm in arms
        }
    }


def run(model, definitions, utterance_sets, *, name, learned):
    rows = []
    utterances = [(goal, request) for goal, requests in utterance_sets.items() for request in requests]
    requests = [request for _, request in utterances]
    ground_prompt = logits(model, [ROUTER_PROMPT.format(request=text) for text in requests], "ABC")
    ground_learned = readout_scores(model, *learned, requests)
    anchors = {
        "prompt": [GOAL_NAMES[index] for index in ground_prompt.argmax(axis=1)],
        "readout": [GOAL_NAMES[index] for index in ground_learned.argmax(axis=1)],
    }
    margins = np.sort(ground_learned, axis=1)[:, -1] - np.sort(ground_learned, axis=1)[:, -2]
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for contract, definition in definitions.items():
            deck = Model(compile_contract(definition))
            blind = {}
            for revision in (1, 2, 3):
                names = list(OPTIONS[contract])
                first_state = {"revision": revision, "fields": {key: bounds[2]
                                                               for key, bounds in definition["fields"].items()}}
                result = board(deck, first_state, "operator", OPTIONS[contract])
                evidence = [(result[plan]["time"], result[plan]["price"]) for plan in names]
                require(evidence == [[(2, 5), (4, 3)], [(5, 2), (4, 3)], [(8, 5), (4, 3)]][revision - 1],
                        "two independent reference metrics diverged")
                for reverse in (False, True):
                    order = names[::-1] if reverse else names
                    label_to_name = dict(zip("ABC", (*order, "clarify")))
                    if revision == 1:
                        # Without numeric facts the prompt is bit-identical across revisions.
                        blind[reverse] = choices(model, requests, result, order, False)
                    external = choices(model, requests, result, order, True)
                    external_demos = choices(model, requests, result, order, True, demos=True)
                    for i, (goal, _) in enumerate(utterances):
                        expected = independently_expected(contract, revision, goal)
                        proof = resolve(result, GOALS[goal])
                        require(exact_outcome(proof) == expected, "endpoint result diverged from independent intent oracle")
                        prompt_goal, learned_goal = anchors["prompt"][i], anchors["readout"][i]
                        routed = resolve(result, GOALS[learned_goal])
                        wire = json.loads(canonical([result, GOALS[learned_goal]]))
                        require(resolve(*wire) == routed, "matched wire control diverged")
                        file = root / f"{contract}-{revision}-{int(reverse)}-{i}.db"
                        commit_typed(file, deck, revision, routed, result, OPTIONS[contract], GOALS[learned_goal])
                        rows.append({
                            "contract": contract, "revision": revision, "reversed": reverse, "utterance": i,
                            "goal": goal, "expected": expected, "blind": label_to_name[blind[reverse][i]],
                            "external": label_to_name[external[i]], "external_demos": label_to_name[external_demos[i]],
                            "factorized_prompt": exact_outcome(resolve(result, GOALS[prompt_goal])),
                            "factorized_readout": exact_outcome(routed), "typed_goal_oracle": exact_outcome(proof),
                        })

    arms = ("blind", "external", "external_demos", "factorized_prompt", "factorized_readout", "typed_goal_oracle")
    return {
        **summarize(rows, arms), "rows": rows, "name": name,
        "unique_utterances": len(requests),
        "goal_grounding": [
            {"ground_truth": expected, "text": text, "prompt": anchors["prompt"][i],
             "readout": anchors["readout"][i], "readout_margin": round(float(margins[i]), 5)}
            for i, (expected, text) in enumerate(utterances)
        ]
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--backbone", choices=("tinyllama", "lfm2"), default="tinyllama")
    options = parser.parse_args()
    architecture, snapshot, expected_hash = {
        "tinyllama": (TinyLlama, REVISION, TINYLLAMA_SHA),
        "lfm2": (LFM2, "ec77db7f39f41c028790706fc1e0cb33461e6a7b", LFM2_SHA),
    }[options.backbone]
    with (options.snapshot / "model.safetensors").open("rb") as file:
        weights_sha = hashlib.file_digest(file, "sha256").hexdigest()
    require(weights_sha == expected_hash, "expected the pinned model artifact")
    model = architecture(options.snapshot)
    sanity = logits(model, ["The capital of France is"], ("Paris", "London"))
    require(float(sanity[0, 0]) > float(sanity[0, 1]), "checkpoint sanity failed")
    definitions = json.loads((HERE / "effect_contracts.json").read_text())
    readout, key, settings = train_readout(model)
    reports = {name: run(model, definitions, fixture, name=name, learned=(readout, key))
               for name, fixture in (("first_fixture", FIRST_FIXTURE), ("transfer", TRANSFER))}
    print(json.dumps({
        "backbone": options.backbone, "snapshot": snapshot, "weights_sha256": weights_sha, "readout": settings,
        "endpoint_grid_checks": separation_law(), "matched_typed_wire_control": "passed",
        "reports": reports,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
