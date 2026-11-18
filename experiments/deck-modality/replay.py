"""Replay the exact parts of the Deck modality experiment with the standard library alone."""

from collections import deque
import json
from pathlib import Path
import random
import sys
from tempfile import TemporaryDirectory

from confirm import confirm, equivalent, propose
import holo
from neural_ops import Desk, calibrate, label_set
from phrasings import restate, without_goal
from worlds import authored, build, interpret, parse_goal

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "model-applications" / "vertical"))

from compiler import digest  # noqa: E402
from runtime import Host  # noqa: E402
from storage import Journal  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check_compiler(cohort):
    """Every transition of every generated contract: definition semantics against the compiled graph."""
    count = 0
    for world, _ in cohort:
        for actor in world.spec["actors"]:
            for state in world.states:
                fields = world.fields(state)
                for action, args in world.candidates:
                    expected = interpret(world.spec, fields, action, actor, args)
                    require(expected == world.model.propose({"revision": 1, "fields": fields}, action, actor, args),
                            "compiled Deck diverged from the definition")
                    count += 1
    return count


def check_labels(cohort):
    """Recompute every label with a forward search that shares no code with the distance tables."""
    count = 0
    for world, tasks in cohort:
        for task in tasks:
            start = tuple(task["state"])
            depth, seen, queue = {start: 0}, {}, deque([start])
            found = None
            while queue and found is None:
                state = queue.popleft()
                if world.satisfied(state, task["goal"]):
                    found = depth[state]
                    break
                for candidate in range(len(world.candidates)):
                    after = world.step(state, candidate, task["actor"])
                    if after is not None and after in world.index and after not in depth:
                        depth[after] = depth[state] + 1
                        seen[after] = seen.get(state, candidate)
                        queue.append(after)
            require((found or 0) == task["depth"], "shortest plan length diverged")
            for candidate in task["optimal"]:
                after = world.step(start, candidate, task["actor"])
                require(world.distance(task["actor"], task["goal"])[world.index[after]] == found - 1,
                        "labelled first step is not on a shortest plan")
            count += 1
    return count


def check_scenes(cohort):
    """No sequence of decoder choices can produce a foreign, repeated or unbound control."""
    rng = random.Random("scenes")
    references = adversarial = tokens = 0
    for world, tasks in cohort:
        for task in tasks:
            legal = [index for index, ok in enumerate(task["legal"]) if ok]
            facts = range(len(world.names))
            require(holo.wellformed(holo.reference(world, task), facts, legal), "reference scene left the grammar")
            references += 1
            for _ in range(8):
                scene = []
                while not scene or scene[-1] != holo.END:
                    scene.append(rng.choice(holo.allowed(scene, facts, legal)))
                    require(len(scene) <= holo.MAX_TOKENS, "scene exceeded its length bound")
                shown = holo.controls(scene)
                require(set(shown) <= set(legal) and len(shown) == len(set(shown)), "unauthorized control decoded")
                require(json.dumps(holo.tree(scene, world, task)) is not None, "scene did not build a tree")
                adversarial += 1
                tokens += len(scene)
    return {"reference_scenes_in_grammar": references, "adversarial_scenes": adversarial,
            "adversarial_tokens": tokens, "unauthorized_or_repeated_controls": 0}


def check_host():
    """A generated control commits through the real Host, and its handle dies with the epoch."""
    world = next(world for world, _ in authored() if "hold" in world.spec["actions"])
    state = [bounds[2] for bounds in world.bounds]
    goal = [[world.names.index("pending"), "ge", 2]]
    task = {"actor": "member", "state": state, "goal": goal, "legal": world.legal(state, "member"),
            "optimal": world.optimal(state, "member", goal)}
    with TemporaryDirectory() as root:
        host = Host(Journal(Path(root) / "journal"), world.model)
        scene = holo.tree(holo.reference(world, task), world, task, host.snapshot()["epoch"])
        stream = list(holo.patches(scene))
        handles = [node["handle"] for part in scene for node in part.get("children", []) if node["kind"] == "control"]
        primary = next(node["handle"] for part in scene for node in part.get("children", [])
                       if node["kind"] == "control" and node["primary"])
        require(host.invoke("controller", primary, "foreign") == {"status": "refused"}, "foreign actor used a control")
        receipt = host.invoke("member", primary, "first")
        require(receipt["status"] == "committed" and host.snapshot()["fields"]["pending"] >= 2,
                "primary control did not reach the request")
        require(host.invoke("member", primary, "first") == receipt, "retry returned a different receipt")
        stale = [host.invoke("member", handle, f"late-{index}")["status"] for index, handle in enumerate(handles)]
        require(set(stale) == {"stale"}, "a control from the previous epoch was accepted")
    return {"patches_streamed": len(stream), "primary_control": primary["action"], "args": primary["args"],
            "committed_effects": [effect["kind"] for effect in receipt["effects"]],
            "foreign_actor": "refused", "retry": "same receipt", "controls_after_commit": "stale"}


def check_judgements():
    data = json.loads((ROOT / "judgement_cases.json").read_text())
    archive = json.loads((ROOT / "judgement_scores.json").read_text())
    fit, calibration, held = range(16), range(16, 36), range(36, 60)
    sets, report = {}, {"model": archive["model"], "revision": archive["revision"], "alpha": 0.1, "criteria": {}}
    for column, name in enumerate(("urgent", "refund"), start=1):
        scores = archive["scores"][name]
        labels = [case[column] for case in data["cases"]]
        offset, threshold = calibrate(scores, labels, fit, calibration, 0.1)
        sets[name] = [label_set(score, offset, threshold) for score in scores]
        report["criteria"][name] = {
            "held_back": len(held),
            "single_label_correct_at_offset": sum((scores[i] > offset) == bool(labels[i]) for i in held),
            "set_contains_reference": sum(labels[i] in sets[name][i] for i in held),
            "set_is_single_label": sum(len(sets[name][i]) == 1 for i in held)}
    lookup = {(case[0], name): sets[name][index] for index, case in enumerate(data["cases"]) for name in sets}
    outcome = {"committed_correct": 0, "committed_wrong": 0, "withheld": 0}
    top_label = {"committed_correct": 0, "committed_wrong": 0}
    with TemporaryDirectory() as root:
        calls = 0
        for index in held:
            message, urgent, refund = data["cases"][index]
            path = Path(root) / f"{index}.jsonl"
            desk = Desk(path, judge=lambda text, name: lookup[(text, name)], model_digest=archive["revision"])
            result = desk.resolve(message, "ticket")
            calls += desk.calls
            expected = [["page", urgent], ["refund", refund]]
            if result["status"] == "withheld":
                outcome["withheld"] += 1
            else:
                outcome["committed_correct" if result["effects"] == expected else "committed_wrong"] += 1
            guess = [["page", int(archive["scores"]["urgent"][index] > 0)],
                     ["refund", int(archive["scores"]["refund"][index] > 0)]]
            top_label["committed_correct" if guess == expected else "committed_wrong"] += 1
            # Replay with the model removed and under a different model digest.
            before = (desk.state, desk.receipts)
            offline = Desk(path)
            require((offline.state, offline.receipts) == before and offline.resolve(message, "ticket") == result
                    or result["status"] == "withheld", "replay without the model diverged")
            other = Desk(path, judge=lambda text, name: [1 - label for label in lookup[(text, name)]],
                         model_digest="another-model")
            other.resolve(message, "again")
            require(other.calls == 0, "a journaled judgement was recomputed by a different model")
            if result["status"] == "withheld":
                try:
                    Desk(Path(root) / "empty.jsonl").resolve(message, "ticket")
                    require(False, "a missing judgement was invented without a model")
                except LookupError:
                    pass
    report["support_deck"] = {"calibrated_sets_with_common_outcome": outcome, "highest_scoring_label": top_label,
                              "model_calls": calls, "model_calls_during_replay": 0}
    return report


def check_confirmation():
    """Replay what the reader stated. A simulated person accepts a goal only when it means the request.

    Every accepted plan is committed on the durable Host from the task's state. The agreement rule
    of the earlier runs is recomputed on the same rows for comparison.
    """
    unseen = [(world, task) for world, tasks in build(1, 250, "test") for task in tasks]
    sets = {"template requests": unseen, "looser requests": restate(unseen), "requests without a goal": without_goal(unseen)}
    report = {}
    with TemporaryDirectory() as root:
        for name, rows in json.loads((ROOT / "stated" / "reader+loose.json").read_text()).items():
            tasks = sets[name]
            require(len(rows) == len(tasks), "recorded statements do not match the cohort")
            real = name != "requests without a goal"
            stats = {"requests": len(tasks), "agreement_rule": {"committed": 0, "wrong": 0},
                     "confirmation": {"goal_shown": 0, "accepted": 0, "plans_committed_on_host": 0,
                                      "steps_committed": 0, "correctly_reported_no_way": 0, "wrong": 0,
                                      "rejected_and_asked_again": 0, "not_understood": 0}}
            for number, ((world, task), (text, choice, agree)) in enumerate(zip(tasks, rows)):
                right = real and (choice in task["optimal"] if task["optimal"] else choice == len(world.candidates))
                stats["agreement_rule"]["committed"] += agree
                stats["agreement_rule"]["wrong"] += agree and not right
                row, goal = stats["confirmation"], parse_goal(world, text)
                if goal is None:
                    row["not_understood"] += 1
                    continue
                row["goal_shown"] += 1
                if not real or not equivalent(world, goal, task["goal"]):
                    row["rejected_and_asked_again"] += 1
                    continue
                row["accepted"] += 1
                store = Journal(Path(root) / f"{name[:3]}-{number}")
                store.save(None, {"epoch": 0, "revision": world.model.checkpoint["initial_revision"],
                                  "checkpoint": digest(world.model.checkpoint),
                                  "fields": dict(zip(world.names, task["state"])), "receipts": {}, "outbox": []})
                host = Host(store, world.model)
                proposal = propose(world, host.snapshot(), task["actor"], goal)
                receipt = confirm(host, proposal, "confirmed")
                if task["depth"] == 0:
                    require(receipt == {"status": "unreachable"}, "a plan was committed where none exists")
                    row["correctly_reported_no_way"] += 1
                    continue
                after = host.snapshot()["fields"]
                reached = receipt["status"] == "committed" and len(proposal["steps"]) == task["depth"] and \
                    world.satisfied([after[field] for field in world.names], task["goal"])
                row["wrong"] += not reached
                row["plans_committed_on_host"] += reached
                row["steps_committed"] += len(proposal["steps"])
                require(confirm(host, proposal, "confirmed") == receipt, "retry returned a different receipt")
                require(confirm(host, proposal, "again") == {"status": "stale"}, "an old confirmation was accepted")
            report[name] = stats
    return report


def study(run):
    """A saved reader scored on looser requests, on requests without a goal and on verified scenes."""
    loose = run["looser requests on the unseen cohort"]
    count = lambda label: [loose["decisions"][label]["correct"], loose["decisions"][label]["n"]] \
        if label in loose["decisions"] else [0, 0]
    return {
        "checkpoint": run["checkpoint"],
        "looser_requests": {
            "tasks": loose["tasks"], "goal_stated_exactly": loose["goal_grounded"][0],
            "exact_search_under_stated_goal": count("read goal then exhaustive search")[0],
            "model_answer": count("masked")[0],
            "committed_correct_of": count("committed when search under the stated goal agrees"),
            "withheld_answer_right_of": count("withheld, answer would have been")},
        "requests_without_a_goal": {kind: [row["committed"], row["requests"]]
                                    for kind, row in run["requests without a goal"].items()},
        "scenes_built_from_the_verified_decision": run["scenes built from the verified decision"]}


def measured():
    """Recorded training runs. These numbers come from `train.py` and are not recomputed here."""
    runs = {}
    for path in sorted((ROOT / "results").glob("*.json")):
        run = json.loads(path.read_text())
        if "training" not in run:
            runs[run["arm"]] = study(run)
            continue
        runs[run["arm"]] = {
            "backbone": run["backbone"], "seed": run["seed"],
            "training_examples": run["training"]["examples"],
            "trainable_parameters": run["training"]["trainable_parameters"],
            "cohorts": {name: {
                "tasks": cohort["tasks"],
                "uniform_choice_among_legal_options": cohort["uniform_choice_among_legal_options"],
                "context_tokens": cohort["context_tokens"],
                "correct": {label: tally["correct"] for label, tally in cohort["decisions"].items()
                            if not label.startswith(("committed", "withheld"))},
                **({"commit_rule": {label: [tally["correct"], tally["n"]]
                                    for label, tally in cohort["decisions"].items()
                                    if label.startswith(("committed", "withheld"))}}
                   if "goal_parsed" in cohort else {}),
                "correct_by_plan_length": {label: tally["by_depth"] for label, tally in cohort["decisions"].items()
                                           if label in ("masked", "severed masked")},
                **{key: cohort[key] for key in ("goal_parsed", "goal_grounded", "exact_steps_per_decision", "holo") if key in cohort},
            } for name, cohort in run["cohorts"].items()}}
    return runs


def main():
    unseen = build(1, 60, "test")
    larger = build(1, 20, "test", "larger")
    hand = authored()
    print(json.dumps({
        "compiled_transitions_equal_to_definition": check_compiler(unseen + larger),
        "labels_confirmed_by_independent_search": check_labels(unseen + larger + hand),
        "scene_grammar": check_scenes(unseen + larger + hand),
        "generated_control_on_durable_host": check_host(),
        "neural_operators": check_judgements(),
        "confirmed_goals_on_durable_host": check_confirmation(),
        "recorded_training_runs": measured(),
    }, indent=1))


if __name__ == "__main__":
    main()
