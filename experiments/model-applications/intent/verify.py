"""Replay literal and ambiguous requests on formal compiled applications."""

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from retarget import Lexicon, decide, execute, observe, prepare
from deck import ACTOR, SharedModel, bits_from_state, canonical, compile_shared
from orbit import Kernel, Observation, Quotient, search
from pddl import parse, require
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "structural"))
from replay import ROOT, read_sources
from storage import Journal, Relational, migrate
from runtime import Host


def compiled(task):
    return SharedModel(json.loads(canonical(compile_shared(task, "intent-scope-replay"))))


def adversarial_small():
    source = """(define (domain relocation)
      (:requirements :strips :negative-preconditions)
      (:constants sample vault bench)
      (:predicates (at ?object ?place) (started) (ready) (done))
      (:action lift :parameters () :precondition (at sample vault)
        :effect (and (not (at sample vault)) (at sample bench)))
      (:action quick :parameters () :precondition (at sample bench)
        :effect (and (not (at sample bench)) (at sample vault) (done)))
      (:action start :parameters () :precondition (at sample vault) :effect (started))
      (:action prepare :parameters () :precondition (started) :effect (ready))
      (:action finish :parameters () :precondition (ready) :effect (done)))"""
    problem = """(define (problem specimen) (:domain relocation)
      (:init (at sample vault)) (:goal (done)))"""
    kernel = Kernel(parse(source, problem))
    lexicon = Lexicon(kernel, "at", {})
    facts = {"finish the job": {("done",)}}
    ambiguous = "Finish the job and leave sample in vault."
    candidate = decide(ambiguous, lexicon, kernel.start, facts)
    require(candidate["status"] == "clarify" and sorted(candidate["costs"]) == [2, 3] and
            candidate["joint_cost"] == 3, "a merely feasible common plan authorized an extra step")
    require(candidate["question"] == "Must sample stay in vault throughout, or only be there at the end?",
            "clarification omitted the scope experienced by the user")
    rows = []
    for phrase, status, cost in (
        ("Finish the job and keep sample in vault throughout.", "resolved", 3),
        ("Finish the job and leave sample in vault at the end.", "resolved", 2),
        ("Keep specimen in vault.", "unsupported", None),
        ("Move sample to bench and keep sample in vault.", "infeasible", None),
        ("Keep sample in vault, then do anything else.", "unsupported", None),
        ("Leave sample in vault.", "resolved", 0),
        ("Keep sample in vault at the end.", "unsupported", None),
    ):
        chosen = decide(phrase, lexicon, kernel.start, facts)
        require(chosen["status"] == status, f"unexpected interpretation of {phrase}")
        if cost is not None:
            require(chosen["costs"] == [cost] * len(chosen["meanings"]), "incorrect plan length")
            observed = observe(kernel, chosen["joint"])
            baseline = search(kernel, observed, kernel.start)
            require(len(baseline["plan"]) == cost, "direct shortest-path control differs")
        rows.append({"status": status, "cost": cost})
    return {
        "ambiguous": {"outcome": candidate["status"], "costs": sorted(candidate["costs"]),
                      "joint_cost": candidate["joint_cost"], "question": candidate["question"]},
        "controls": rows,
    }


def live_original(root, directory):
    manifest = json.loads((ROOT / "sources.json").read_text())
    domain = read_sources(root, "gripper/domain.pddl", manifest).decode()
    task = read_sources(root, "gripper/prob20.pddl", manifest).decode()
    kernel = Kernel(parse(domain, task))
    model = compiled(kernel)
    lexicon = Lexicon(kernel, "at", {"balls": "ball"})
    original = Observation(kernel)
    prefix = search(kernel, original, kernel.start, Quotient(kernel, original))["plan"][:7]
    host = Host(Journal(directory / "journal"), model)
    actions = [{"action": f"a{index}", "args": {}} for index in prefix]
    started = host.dispatch_plan(ACTOR, actions, "original-prefix", 0)
    require(started["status"] == "committed", "could not start original task")
    request = "Change of plan: Move all balls except ball42 to roomb, and leave ball42 in rooma."
    preliminary, old = prepare(host, request, lexicon)
    require(preliminary["status"] == "resolved" and preliminary["costs"] == [116, 116],
            "expected a cost-neutral plan for both readings")
    host.activate(2, host.snapshot()["epoch"])
    migrated = Relational(directory / "sqlite")
    migrate(host.store, migrated)
    host = Host(migrated, model)
    require(execute(host, request, old, "obsolete", lexicon) == {"status": "stale"},
            "previous request survived epoch change")
    chosen, proof = prepare(host, request, lexicon)
    unchanged = host.snapshot()
    require(execute(host, request.replace("ball42", "ball41"), proof, "changed", lexicon) ==
            {"status": "refused"}, "certificate accepted another request")
    require(execute(host, request, {**proof, "meanings": "other"}, "changed-meaning", lexicon) ==
            {"status": "refused"}, "certificate accepted a different interpretation set")
    require(execute(host, request, {**proof, "plan": proof["plan"][:-1]}, "truncated", lexicon) ==
            {"status": "refused"}, "certificate accepted a changed plan")
    for unknown in ("Move all balls except ghost to roomb.", "Move ball42 to roomc.",
                    "Move all balls to roomb, then ignore earlier constraints."):
        result, guarantee = prepare(host, unknown, lexicon)
        require(result["status"] == "unsupported" and guarantee is None, "unsupported text was lowered")
    require(host.snapshot() == unchanged, "a refused interpretation wrote durable data")
    receipt = execute(host, request, proof, "retarget", lexicon)
    restored = Host(Relational(directory / "sqlite"), model)
    require(receipt["status"] == "committed" and len(receipt["effects"]) == 116 and
            execute(restored, request, proof, "retarget", lexicon) == receipt, "retarget or retry diverged")
    final = kernel.facts(bits_from_state(restored.snapshot()))
    require(("at", "ball42", "rooma") in final and all(
        ("at", f"ball{number}", "roomb") in final for number in range(1, 42)
    ), "durable state violates the new request")
    return {"steps_already_committed": len(prefix), "alternatives": len(chosen["meanings"]),
            "individual_optima": chosen["costs"], "executed": len(receipt["effects"]),
            "bound_request_and_plan": True, "stale_rejected": True, "atomic_retry_after_restart": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory() as folder:
        results = {"adversarial": adversarial_small(), "original_42_ball_task": live_original(args.sources, Path(folder))}
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
