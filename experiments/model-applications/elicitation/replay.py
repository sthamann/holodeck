"""Compile observed plans, solve discovery cases and replay proof-bound commits."""

from collections import deque
from functools import lru_cache
from itertools import product
import json
from pathlib import Path
import random
import sys
from tempfile import TemporaryDirectory

from discover import Problem, Solver, bits, decide, effect_signature

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "vertical"))
from compiler import Model, compile_contract, digest, require
from runtime import Host, preview_plan
from storage import Journal, Relational, migrate


ACTOR = "operator"


def compile_world(raw, target):
    contexts = sorted(raw)
    index = {name: i for i, name in enumerate(contexts)}
    actions = {}
    options = {}
    for context, board in sorted(raw.items()):
        for label, events in sorted(board.items()):
            route = len(options) + 1
            first, second = f"open_{route}", f"finish_{route}"
            require(all(isinstance(name, str) and name.isidentifier() and type(amount) is int
                        for name, amount in events), "invalid effect")
            actions[first] = {
                "label": label, "guard": ["and", ["eq", "phase", 0], ["eq", "context", index[context]]],
                "writes": {"phase": 1, "route": route}, "effects": events[:len(events) // 2],
            }
            actions[second] = {
                "label": label, "guard": ["and", ["eq", "phase", 1], ["eq", "route", route]],
                "writes": {"phase": 2}, "effects": events[len(events) // 2:],
            }
            options[first] = label
    spec = {
        "initial_revision": 1, "revisions": {"1": 0, "2": 0},
        "fields": {"context": [0, len(contexts) - 1, index[target]],
                   "phase": [0, 2, 0], "route": [0, len(options), 0]},
        "public": ["phase"],
        "actors": {ACTOR: 0},
        "actions": actions,
    }
    return Model(compile_contract(spec)), index, options


def enumerate_plans(model, actor, state, options, propose=None):
    propose = propose or model.propose
    discovered = {}
    queue = deque([(state, [], [])])
    while queue:
        current, steps, effects = queue.popleft()
        if current["fields"]["phase"] == 2:
            label = options[steps[0]["action"]]
            require(label not in discovered, "duplicate completed plan label")
            discovered[label] = {"steps": steps, "effects": effects}
        elif len(steps) < 2:
            for action, rule in model.checkpoint["actions"].items():
                arguments = sorted(rule.get("args", {}).items())
                for values in product(*(allowed for _, allowed in arguments)):
                    supplied = dict(zip((key for key, _ in arguments), values))
                    proposal = propose(current, action, actor, supplied)
                    if proposal is not None:
                        next_state = {**current, "fields": {**current["fields"], **proposal["writes"]}}
                        queue.append((next_state, [*steps, {"action": action, "args": supplied}],
                                      [*effects, *proposal["effects"]]))
    return discovered


def compile_case(case):
    raw = case["boards"]
    target = case["target"]
    require(all(board for board in raw.values()), "empty plan board")
    model, indexes, names = compile_world(raw, target)
    boards = {}
    for context, index in indexes.items():
        begin = {"revision": 1, "fields": {"context": index, "phase": 0, "route": 0}}
        board = enumerate_plans(model, ACTOR, begin, names)
        require({label: plan["effects"] for label, plan in board.items()} == raw[context],
                "compiled application changed ordered effects")
        boards[context] = board
    problem = Problem(boards, target, set(boards) - {target})
    return problem, model, names, boards[target]


def is_sufficient(problem, depth, answer_or_deferral=False):
    """Verify the minimal worst-case depth by a separate bounded search."""
    @lru_cache(None)
    def verify(mask, left):
        partitions = {}
        for hypothesis in bits(mask):
            signature, optima = problem.classes[hypothesis]
            options = {label for index, label in enumerate(problem.labels) if optima & (1 << index)}
            partitions.setdefault(signature, []).append(options)
        if set.intersection(*(options for values in partitions.values() for options in values)):
            return True
        if answer_or_deferral and not any(set.intersection(*values) for values in partitions.values()):
            return True
        if not left:
            return False
        for masks in problem.answer_groups:
            parts = [mask & answer for answer in masks if mask & answer]
            if len(parts) > 1 and all(verify(part, left - 1) for part in parts):
                return True
        return False
    return verify(problem.full, depth)


def verify_deferral(problem, mask, witness_groups):
    expected = {problem.classes[index][0] for index in bits(mask)}
    proven = set()
    for witness in witness_groups:
        signatures = {tuple(item["answers"]) for item in witness}
        if len(signatures) != 1 or not witness:
            return False
        signature = signatures.pop()
        if signature in proven:
            return False
        proven.add(signature)
        for item in witness:
            optima = set(item["optima"])
            indexes = [index for index in bits(mask) if problem.classes[index][0] == signature and
                       optima == {problem.labels[i] for i in bits(problem.classes[index][1])}]
            rules = [
                [[*problem.features[column], sign] for column, sign in rule]
                for index in indexes for rule in problem.members[index]
            ]
            if item["rule"] not in rules:
                return False
        if set.intersection(*(set(item["optima"]) for item in witness)):
            return False
    return proven == expected


def grader(case):
    ranks = {}
    for board, groups in case["ranks"].items():
        labels = [label for group in groups for label in group]
        require(len(labels) == len(set(labels)) and set(labels) == set(case["boards"][board]),
                f"bad grading order for {board}")
        ranks[board] = {label: rank for rank, group in enumerate(groups) for label in group}
    def answer(question):
        context, first, second = question
        require(context != case["target"], "target answer leaked")
        left, right = ranks[context][first], ranks[context][second]
        return (left > right) - (left < right)
    return answer


def filter_history(problem, history):
    mask = problem.full
    seen = set()
    for index, answer in history:
        require(type(index) is int and 0 <= index < len(problem.questions) and index not in seen,
                "unknown or repeated calibration question")
        require(type(answer) is int and answer in (-1, 0, 1), "invalid answer")
        seen.add(index)
        mask &= problem.answer_groups[index][answer + 1]
    return mask


def make_certificate(host, board, outcome):
    state = host.snapshot()
    return {"epoch": state["epoch"], "checkpoint": state["checkpoint"], "actor": ACTOR,
            "board": digest(board), "plan": outcome["plan"], "history": outcome["history"]}


def apply_certificate(problem, host, names, certificate, trusted_history):
    state = host.snapshot()
    if certificate["epoch"] != state["epoch"] or certificate["checkpoint"] != state["checkpoint"]:
        return {"status": "stale"}
    require(certificate["actor"] == ACTOR, "certificate belongs to another actor")
    board = enumerate_plans(host.model, ACTOR, state, names, propose=host.propose)
    plan = certificate["plan"]
    require(effect_signature(board) == problem.target_effects, "recompile the objective space for the current target")
    require(certificate["board"] == digest(board) and plan in board, "certificate outcome changed")
    require(certificate["history"] == trusted_history, "certificate transcript was not supplied by the session")
    remaining = filter_history(problem, trusted_history)
    require(problem.common(remaining) & (1 << problem.labels.index(plan)), "plan lacks decision consensus")
    return host.dispatch_plan(ACTOR, board[plan]["steps"], "elicitation", state["epoch"])


def replay_commit(problem, model, names, board, outcome, root):
    source = Host(Journal(root / "journal"), model)
    certificate = make_certificate(source, board, outcome)
    source.activate(2, 0)
    target = Relational(root / "sqlite")
    migrate(source.store, target)
    migrated = Host(target, model)
    require(apply_certificate(problem, migrated, names, certificate, outcome["history"]) == {"status": "stale"},
            "certificate survived revision change")
    certificate = make_certificate(migrated, board, outcome)
    stored = migrated.snapshot()
    try:
        apply_certificate(problem, migrated, names, {**certificate, "history": [(-1, 0)]}, outcome["history"])
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a forged transcript")
    if len(board) > 1:
        alternative = next(label for label in sorted(board) if label != outcome["plan"])
        if not problem.common(outcome["mask"]) & (1 << problem.labels.index(alternative)):
            try:
                apply_certificate(problem, migrated, names, {**certificate, "plan": alternative}, outcome["history"])
            except ValueError:
                pass
            else:
                raise AssertionError("accepted a forged plan choice")
            require(migrated.snapshot() == stored, "forgery touched storage")
    result = apply_certificate(problem, migrated, names, certificate, outcome["history"])
    require(result["status"] == "committed" and
            [[e["kind"], e["value"]] for e in result["effects"]] == board[outcome["plan"]]["effects"],
            "committed effects diverged")
    return True


def evaluate(case, root):
    problem, model, names, board = compile_case(case)
    solver = Solver(problem)
    exact_identity = Solver(problem, identify=True)
    depth, _ = solver.solve(problem.full)
    answer = grader(case)
    outcome = decide(problem, solver, answer)
    identity = decide(problem, exact_identity, answer, identify=True)
    completion = Solver(problem, prove_deferral=True)
    completed = decide(problem, completion, answer)
    require(("plan" in completed) == ("plan" in outcome), "proof-aware terminal changed solvability")
    if "deferral" in completed:
        require(verify_deferral(problem, completed["mask"], completed["deferral"]),
                "impossibility proof omitted a still feasible answer signature")
        require(not verify_deferral(problem, completed["mask"], completed["deferral"][1:]),
                "incomplete impossibility proof was accepted")
    full_answers = tuple(answer(question) for question in problem.questions)
    expected_optima = sum(1 << problem.labels.index(label)
                         for label in case["ranks"][case["target"]][0])
    supported = (full_answers, expected_optima) in problem.classes
    if depth != float("inf"):
        require(is_sufficient(problem, depth), "reported limit insufficient")
        require(depth == 0 or not is_sufficient(problem, depth - 1), "limit not minimal")
    expected = case["expected"]
    actual = "resolved" if "plan" in outcome else "contradiction" if outcome.get("contradiction") else "underdetermined"
    if supported:
        require(actual != "contradiction", "in-class grader contradicted itself")
    result = {
        "case": case["name"], "fixture_expected": expected, "observed": actual,
        "oracle_in_grammar": supported, "features": [list(feature) for feature in problem.features],
        "rules": problem.raw, "classes": len(problem.classes), "questions": len(outcome["history"]),
        "identity_questions": len(identity["history"]), "remaining_classes": outcome["mask"].bit_count(),
        "decision_or_deferral_questions": len(completed["history"]),
        "decision_or_deferral_worst_case": completion.solve(problem.full)[0],
        "worst_case": None if depth == float("inf") else depth, "states": solver.expanded,
        "asked": [list(problem.questions[i]) + [answer] for i, answer in outcome["history"]],
    }
    if "plan" in outcome:
        correct = (outcome["plan"] in case["ranks"][case["target"]][0] and
                   completed["plan"] in case["ranks"][case["target"]][0])
        require(correct or not supported, "incorrect external decision for an in-class grader")
        result["plan"] = outcome["plan"]
        result["agrees_with_grader"] = correct
        if supported:
            result["commit_verified"] = replay_commit(problem, model, names, board, completed, root)
    elif "conflict" in outcome:
        witness = outcome["conflict"]
        require(len({tuple(item["answers"]) for item in witness}) == 1, "distinguishable counterexample")
        require(not set.intersection(*(set(item["optima"]) for item in witness)), "no genuine conflict")
        result["conflict"] = witness
    return result


def full_policy_replay(case):
    problem, _, _, _ = compile_case(case)
    solver = Solver(problem)
    exact_identity = Solver(problem, identify=True)
    completion = Solver(problem, prove_deferral=True)
    optimum_counts = {"resolved": 0, "conflict": 0, "decision_questions": 0, "identity_questions": 0,
                      "greedy_decision_questions": 0, "decision_better": 0, "fewer_identity_questions": 0,
                      "decision_or_deferral_questions": 0}
    worst_observed = 0
    complete_worst_observed = 0
    for answers, optima in problem.classes:
        oracle = dict(zip(problem.questions, answers)).__getitem__
        based = decide(problem, solver, oracle)
        baseline = decide(problem, exact_identity, oracle, identify=True)
        greedy = decide(problem, solver, oracle, greedy=True)
        done = decide(problem, completion, oracle)
        require(("plan" in done) == ("plan" in based), "undesired terminal refusal or commitment")
        if "plan" in done:
            require(optima & (1 << problem.labels.index(done["plan"])), "invalid proof-aware decision")
        else:
            require(verify_deferral(problem, done["mask"], done["deferral"]),
                    "invalid non-identifiability proof")
        optimum_counts["decision_or_deferral_questions"] += len(done["history"])
        complete_worst_observed = max(complete_worst_observed, len(done["history"]))
        optimum_counts["greedy_decision_questions"] += len(greedy["history"])
        require(not based.get("contradiction"), "consistent answer was discarded")
        if "plan" in based:
            require(optima & (1 << problem.labels.index(based["plan"])), "decision outside objective optima")
            optimum_counts["resolved"] += 1
        else:
            require("conflict" in baseline, "baseline resolved a certified ambiguity")
            optimum_counts["conflict"] += 1
        questions, naive = len(based["history"]), len(baseline["history"])
        optimum_counts["decision_questions"] += questions
        optimum_counts["identity_questions"] += naive
        optimum_counts["decision_better"] += int(questions < naive)
        optimum_counts["fewer_identity_questions"] += int(naive < questions)
        worst_observed = max(worst_observed, questions)
    depth, _ = solver.solve(problem.full)
    if depth != float("inf"):
        require(worst_observed == depth, "worst-case bound was not reached")
        require(depth == 0 or not is_sufficient(problem, depth - 1), "worst-case bound was not minimal")
    complete_depth, _ = completion.solve(problem.full)
    require(complete_worst_observed == complete_depth, "proof-aware worst case was not reached")
    require(complete_depth == 0 or not is_sufficient(problem, complete_depth - 1, True),
            "proof-aware worst case was not minimal")
    return {"case": case["name"], "rules": problem.raw, "classes": len(problem.classes),
            "decision_or_deferral_worst_case": complete_depth,
            "identity_worst_case": finite(exact_identity.solve(problem.full)[0]),
            "worst_case": None if depth == float("inf") else depth, "states": solver.expanded, **optimum_counts}


def finite(value):
    return None if value == float("inf") else value


def retarget_test():
    raw = {
        "cal": {"A": [["x", 0], ["y", 0]], "B": [["x", 1], ["y", 1]], "C": [["x", 2], ["y", 2]]},
        "aligned": {"A": [["x", 0], ["y", 0]], "B": [["x", 1], ["y", 1]], "C": [["x", 2], ["y", 2]]},
        "shifted": {"A": [["x", 0], ["y", 2]], "B": [["x", 2], ["y", 0]], "C": [["x", 1], ["y", 1]]},
    }
    boards = {context: {label: {"effects": events} for label, events in board.items()}
              for context, board in raw.items()}
    first = Problem(boards, "aligned", ("cal",))
    second = Problem(boards, "shifted", ("cal",))
    require(first.questions == second.questions, "calibration changed on retarget")
    initial = decide(first, Solver(first), lambda _: -1)
    require(initial.get("plan") == "A" and len(initial["history"]) == 1, "initial decision not certified")
    remaining = filter_history(second, initial["history"])
    require(not second.common(remaining) and not list(second.splits(remaining)), "missing future ambiguity")
    witness = second.failure(remaining)
    require(len(witness) == 2, "expected a two-rule witness")
    return {"first": "A", "new_target": "underdetermined", "witness": witness}


def contradiction_test():
    board = {"A": {"effects": [["signal", 0]]}, "B": {"effects": [["signal", 1]]}}
    problem = Problem({"cal": board, "target": board}, "target", ("cal",))
    result = decide(problem, Solver(problem), lambda _: 0)
    require(result.get("contradiction") and result["mask"] == 0,
            "impossible tie accepted as an objective")
    return True


def generated_world(seed):
    rng = random.Random(seed)
    raw = {}
    for context in ("cal1", "cal2", "cal3", "target"):
        board = {}
        for label in "ABCD":
            board[label] = [[f"signal_{axis}", rng.randrange(6)]
                            for axis in (0, 1, 2, 0, 3)]
        raw[context] = board
    return {"name": f"generated_{seed}", "target": "target", "boards": raw}


def main():
    suites = [json.loads((ROOT / name).read_text()) for name in ("independent.json",)]
    generated = [full_policy_replay(generated_world(seed)) for seed in range(12)]
    results = {
        "retarget": retarget_test(),
        "contradiction_refused": contradiction_test(),
        "generated": generated,
        "totals": {key: sum(row[key] for row in generated)
                   for key in ("classes", "rules", "resolved", "conflict", "decision_questions",
                               "identity_questions", "greedy_decision_questions", "decision_better", "fewer_identity_questions",
                               "decision_or_deferral_questions")},
    }
    with TemporaryDirectory() as folder:
        root = Path(folder)
        for suite in suites:
            results["separate_author"] = []
            for i, case in enumerate(suite["cases"]):
                case_root = root / str(i)
                case_root.mkdir()
                results["separate_author"].append(evaluate(case, case_root))
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
