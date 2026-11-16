"""Check language predictions against exact plans and actual durable outcomes."""

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
from itertools import combinations
import json
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory

from verify import compiled, relocation
from deck import ACTOR, bits_from_state, commit, issue, verify_source
from orbit import search
from pddl import require
from retarget import Lexicon, Meaning, decide_meanings, observe, parse
from runtime import Host
from storage import Relational

HERE = Path(__file__).resolve().parent
LABELS = ("final", "persistent", "both", "outside")
ALPHA = Fraction(1, 5)
PREFIXES = {"cold": (), "ready": ("start", "prepare"), "lifted": ("lift",)}
VAULT = frozenset({("at", "sample", "vault")})
FINISH = {("done",)}
FINAL = Meaning(VAULT | FINISH, frozenset())
PERSISTENT = Meaning(VAULT | FINISH, VAULT)
MEANINGS = {"final": (FINAL,), "persistent": (PERSISTENT,), "both": (FINAL, PERSISTENT)}


def ground(labels):
    if "outside" in labels:
        return ()
    return tuple(sorted({m for label in labels for m in MEANINGS[label]}, key=Meaning.key))


def lexical(text, kernel):
    try:
        meanings = parse("finish the job and " + text, Lexicon(kernel, "at", {}), {"finish the job": FINISH})
    except ValueError:
        return ("outside",)
    for label, expected in MEANINGS.items():
        if set(meanings) == set(expected):
            return (label,)
    return ("outside",)


def calibrate(rows, fixtures):
    selected = sorted(row[LABELS.index(label)] for row, label in zip(rows, labels(fixtures), strict=True))
    rank = ceil((len(selected) + 1) * (1 - ALPHA))
    return selected[rank - 1] if rank <= len(selected) else float("inf")


def labels(fixtures):
    return [label for label in LABELS for _ in fixtures[label]]


def one_best(row):
    return (LABELS[min(range(len(row)), key=row.__getitem__)],)


def within(row, cutoff):
    return tuple(label for label, margin in zip(LABELS, row, strict=True) if margin <= cutoff)


def turn(kernel, host, prefix, meanings):
    if prefix:
        index = {op.signature[0]: i for i, op in enumerate(kernel.task.operators)}
        actions = [{"action": f"a{index[name]}", "args": {}} for name in prefix]
        require(host.dispatch_plan(ACTOR, actions, "already-durable", 0)["status"] == "committed",
                "starting work did not commit")
    before = host.snapshot()
    start = bits_from_state(before)
    decided = decide_meanings(kernel, start, meanings)
    if decided["status"] != "resolved":
        require(host.snapshot() == before, "unresolved request wrote durable state")
        return start, decided["status"], None
    observer = observe(kernel, decided["joint"])
    proof = issue(host, observer, decided["solution"])
    receipt = commit(host, observer, proof, "change-of-scope")
    require(receipt["status"] == "committed", "qualified plan was refused")
    plan = tuple(effect["value"] for effect in receipt["effects"])
    require(plan == tuple(proof["plan"]) and bits_from_state(host.snapshot()) == proof["final"],
            "plan and durable effects differ")
    verify_source(kernel, observer, start, plan, proof["final"])
    require(host.snapshot()["outbox"][:len(prefix)] == before["outbox"], "prior effects changed")
    return start, "resolved", plan


def correct(kernel, start, plan, gold):
    if not gold:
        return False
    for meaning in gold:
        observer = observe(kernel, meaning)
        optimum = search(kernel, observer, start)
        if optimum["status"] != "resolved" or len(plan) != len(optimum["plan"]):
            return False
        state = start
        for op in plan:
            state = kernel.next(state, op)
            require(state is not None, "committed action is not executable in reference kernel")
            if not observer.allowed(state):
                return False
        if not observer.achieved(state):
            return False
    return True


def replay():
    kernel = relocation()
    model = compiled(kernel)
    all_sets = [group for count in range(len(LABELS) + 1) for group in combinations(LABELS, count)]
    results = {}
    with TemporaryDirectory() as directory:
        for context, prefix in PREFIXES.items():
            for number, prediction in enumerate(all_sets):
                host = Host(Relational(Path(directory) / f"{context}-{number}.sqlite"), model)
                start, status, plan = turn(kernel, host, prefix, ground(prediction))
                results[context, prediction] = {
                    "result": status, "plan": plan,
                    "valid": {label: plan is not None and correct(kernel, start, plan, ground((label,)))
                              for label in LABELS},
                }
    for context, reference in {"cold": [2, 3, None], "ready": [1, 1, 1],
                               "lifted": [1, None, None]}.items():
        costs = [None if (plan := results[context, (label,)]["plan"]) is None else len(plan)
                 for label in LABELS[:3]]
        require(costs == reference, f"causal context {context} lost its distinguishing optimum")
    return kernel, results


def summarize(fixtures, scores, limits, kernel, decisions):
    columns = {}
    for i, (text, label) in enumerate((text, label) for label in LABELS for text in fixtures[label]):
        frozen, trained = (scores[name][i] for name in ("frozen", "trained"))
        frozen_set = within(frozen, limits["frozen"])
        calibrated = within(trained, limits["trained"])
        arms = {
            "grammar": (lexical(text, kernel), False),
            "frozen_point": (one_best(frozen), False),
            "frozen_calibrated_singleton": (frozen_set, True),
            "frozen_calibrated_semantic": (frozen_set, False),
            "frozen_calibrated_semantic_scope_oracle": (frozen_set if label != "outside" else ("outside",), False),
            "trained_point": (one_best(trained), False),
            "trained_calibrated_singleton": (calibrated, True),
            "trained_calibrated_semantic": (calibrated, False),
            "always_both": (("both",), False),
            "oracle": ((label,), False),
        }
        for name, (prediction, singleton_only) in arms.items():
            report = columns.setdefault(name, {
                "utterances": Counter(), "decisions": Counter(),
                "by_context": {context: Counter() for context in PREFIXES},
                "false_by_label": Counter(),
                "false_witnesses": [],
            })
            report["utterances"]["total"] += 1
            report["utterances"]["missed_label"] += label not in prediction
            report["utterances"]["omitted_meaning"] += (
                not set(ground((label,))).issubset(ground(tuple(p for p in prediction if p != "outside")))
                if label != "outside" else "outside" not in prediction
            )
            for context in PREFIXES:
                plan = decisions[context, () if singleton_only and len(prediction) != 1 else prediction]
                eligible = decisions[context, (label,)]["result"] == "resolved"
                executed = plan["result"] == "resolved"
                is_false = executed and not plan["valid"][label]
                require(not is_false or label not in prediction, "covered reference label led to a false commit")
                counts = {
                    "cases": 1, "committed": executed, "false_commit": is_false,
                    "correct_commit": executed and not is_false, "unnecessary_hold": eligible and not executed,
                }
                report["decisions"].update(counts)
                report["by_context"][context].update(counts)
                if is_false:
                    report["false_by_label"][label] += 1
                if is_false and len(report["false_witnesses"]) < 5:
                    report["false_witnesses"].append({"request": text, "context": context, "gold": label,
                                                      "retained": prediction, "actions": len(plan["plan"])})
    return columns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, default=HERE / "scope_scores.json")
    args = parser.parse_args()
    source = (HERE / "scope_cases.json").read_bytes()
    fixtures = json.loads(source)
    archived = json.loads(args.scores.read_text())
    require(archived["corpus_sha256"] == hashlib.sha256(source).hexdigest() and
            archived["labels"] == list(LABELS), "candidate scores use a different corpus or label order")
    all_text = [text for split in fixtures.values() for group in split.values() for text in group]
    require(len(all_text) == len(set(all_text)), "a request occurs in more than one cohort")
    limits = {name: calibrate(values, fixtures["calibration"]) for name, values in archived["scores"]["calibration"].items()}
    kernel, decisions = replay()
    contexts = {context: {label: ("hold" if (plan := decisions[context, (label,)]["plan"]) is None else len(plan))
                          for label in LABELS} for context in PREFIXES}
    results = {split: summarize(fixtures[split], archived["scores"][split], limits, kernel, decisions)
               for split in ("holdout", "stress")}
    print(json.dumps({"calibration_alpha": str(ALPHA), "thresholds": limits, "contexts": contexts,
                      "candidate_sets_replayed": len(decisions), "results": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
