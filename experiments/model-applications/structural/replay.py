"""Replay pinned independently-authored PDDL benchmarks through executable Decks."""

import argparse
from dataclasses import replace
from hashlib import sha1
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from pddl import parse, require
from orbit import Kernel, Observation, Quotient, inventory, search, successors, verify_partition
from deck import (
    SharedModel, canonical, certify_weights, compile_flat, compile_shared,
    replay_store, sizes, source_digest, verify_source,
)
from compiler import Model


ROOT = Path(__file__).resolve().parent
REF = "e21d49c2cb61d147a46c5966f2581bf6fd422b9f"
UPSTREAM = f"https://raw.githubusercontent.com/aibasel/downward-benchmarks/{REF}"
CASES = (
    ("gripper", "prob01.pddl", True, True),
    ("gripper", "prob02.pddl", True, True),
    ("gripper", "prob04.pddl", True, False),
    ("gripper", "prob09.pddl", False, False),
    ("gripper", "prob20.pddl", False, False),
    ("blocks", "probBLOCKS-4-0.pddl", True, True),
    ("miconic", "s4-0.pddl", True, True),
)


def blob_id(data):
    return sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def fetch_sources(root, paths):
    for path, expected in paths.items():
        with urlopen(f"{UPSTREAM}/{path}", timeout=25) as response:
            data = response.read(200000)
        require(blob_id(data) == expected, f"unexpected upstream blob at {path}")
        output = root / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)


def read_sources(root, path, pins):
    data = (root / path).read_bytes()
    require(blob_id(data) == pins[path], f"unexpected local blob at {path}")
    return data


def new_observer(kernel, orbit, name, previous_plan):
    # Fixture input only. The planner and compiler never use domain names to pick a reducer.
    keep = frozenset({("at", name, "rooma")})
    remainder = frozenset(fact for fact in kernel.task.goal_yes if name not in fact[1:])
    require(len(kernel.task.goal_yes) == len(remainder) + 1, "expected one designated object")
    scoped = Observation(kernel, goal_yes=remainder, always_yes=keep)
    limited = Quotient(kernel, scoped)
    chosen_block = next(block for block in orbit.blocks if name in block)
    require(len(chosen_block) > 1 and all(name not in block for block in limited.blocks),
            "the revised observer failed to pin the named object")
    sibling = next(item for item in chosen_block if item != name)

    def picked(item):
        index = next(i for i, action in enumerate(kernel.task.operators)
                     if action.signature == ("pick", item, "rooma", "left"))
        return kernel.next(kernel.start, index)

    one, two = picked(name), picked(sibling)
    require(orbit.key(one) == orbit.key(two) and not scoped.allowed(one) and scoped.allowed(two),
            "expected a concrete counterexample to reusing the old observer quotient")
    require(limited.key(one) != limited.key(two), "revised quotient still merges distinguished states")
    prefix = previous_plan[:7]
    start = kernel.start
    for index in prefix:
        start = kernel.next(start, index)
        require(start is not None and scoped.allowed(start), "earlier work did not preserve the new invariant")
    solution = search(kernel, scoped, start, limited)
    require(solution["status"] == "resolved", "revised plan failed")
    verify_source(kernel, scoped, start, solution["plan"], solution["end"])
    return scoped, solution, prefix, {
        "pinned_object": name, "remaining_exchangeable": max(map(len, limited.blocks), default=1),
        "steps_already_committed": len(prefix),
        "steps": len(solution["plan"]), "states": solution["states"],
        "old_quotient_collision": True, "pinned_object_untouched_at_every_step": True,
    }


def asymmetric_policy(kernel, named):
    operators = tuple(op for op in kernel.task.operators
                      if not (op.signature[0] == "pick" and op.signature[1] == named))
    modified = Kernel(replace(kernel.task, operators=operators))
    observer = Observation(modified)
    quotient = Quotient(modified, observer)
    result = search(modified, observer, modified.start, quotient)
    require(result["status"] == "unreachable" and all(named not in block for block in quotient.blocks),
            "an asymmetric permission was hidden by an invalid symmetry")
    return {"policy_change": f"disable pickup for {named}", "status": result["status"],
            "examined_orbits": result["states"]}


def verify_gripper_count(kernel, result, reachability):
    n = len(kernel.task.goal_yes)
    configurations = 2 * (2 ** n + 2 * n * 2 ** (n - 1) + n * (n - 1) * 2 ** (n - 2))
    expected_steps = 2 * n + 2 * ((n + 1) // 2) - 1
    require(reachability["named_states"] == configurations and len(result["plan"]) == expected_steps,
            "mechanical orbit count disagrees with independent combinatorial lower bound")


def run_one(domain, problem, root, replay_root, pins, raw_check, full_check, library):
    domain_data = read_sources(root, f"{domain}/domain.pddl", pins)
    problem_data = read_sources(root, f"{domain}/{problem}", pins)
    kernel = Kernel(parse(domain_data.decode(), problem_data.decode()))
    observer = Observation(kernel)
    quotient = Quotient(kernel, observer)
    result = search(kernel, observer, kernel.start, quotient)
    require(result["status"] == "resolved", f"failed on published task {domain}/{problem}")
    verify_source(kernel, observer, kernel.start, result["plan"], result["end"])
    reachability = inventory(kernel, observer, quotient)
    if domain == "gripper":
        verify_gripper_count(kernel, result, reachability)
    frozen = domain == "gripper" and bool(library)
    shared = compile_shared(kernel, source_digest(domain_data, problem_data), library, frozen=frozen)
    if not library:
        library.update({tuple(module["roles"]): module for module in shared["modules"].values()})
    library_hash = canonical(sorted(library.items()))
    if frozen:
        require(canonical(sorted((tuple(module["roles"]), module) for module in shared["modules"].values()))
                == library_hash, "scaled domain used different executable weights")
    model = SharedModel(json.loads(canonical(shared)))
    flat = compile_flat(kernel, shared)

    checks = certify_weights(kernel, model, Model(flat))
    summary = {
        "task": f"{domain}/{problem}", "facts": len(kernel.atoms), "steps": len(result["plan"]),
        "distinct_object_blocks": list(map(len, quotient.blocks)),
        "scope_blocks_not_used": [len(block) for block in quotient.exchangeable if block not in quotient.blocks],
        "states_until_goal": result["states"], "local_assignments_verified": checks,
        "inventory": reachability, "artifact": sizes(shared, flat),
    }
    summary["frozen_weights_only"] = frozen
    if raw_check:
        brute = search(kernel, observer, kernel.start)
        require(brute["status"] == "resolved" and len(brute["plan"]) == len(result["plan"]),
                "partition failed to preserve unit-cost plan length")
        summary["raw_states_until_goal"] = brute["states"]
    if full_check:
        crosscheck = verify_partition(kernel, observer, quotient)
        require(crosscheck["concrete"] == reachability["named_states"] and
                crosscheck["orbits"] == reachability["orbit_states"] and
                crosscheck["edges"] == reachability["named_edges"], "orbit multiplicity sum differs")
        summary["explicit_partition_check"] = crosscheck

    replay_dir = replay_root / f"{domain}-{problem}"
    replay_dir.mkdir()
    summary["transaction"] = replay_store(replay_dir, model, observer, result)
    if domain == "gripper" and problem == "prob20.pddl":
        refined, updated, prefix, detail = new_observer(kernel, quotient, "ball42", result["plan"])
        revised_dir = replay_root / "scoped-invariant"
        revised_dir.mkdir()
        detail["transaction"] = replay_store(revised_dir, model, refined, updated, prefix)
        summary["retarget"] = detail
        summary["asymmetric_policy"] = asymmetric_policy(kernel, "ball42")
    print(summary["task"], "verified", file=sys.stderr, flush=True)
    return summary


def parser_controls():
    domain = """(define (domain small) (:requirements :strips :typing :negative-preconditions :equality)
      (:types item)
      (:predicates (ready ?x - item) (complete ?x - item))
      (:action advance :parameters (?x ?y - item)
        :precondition (and (ready ?x) (not (complete ?y)) (not (= ?x ?y)))
        :effect (and (not (ready ?x)) (complete ?y))))"""
    problem = """(define (problem small-instance) (:domain small) (:objects a b - item)
      (:init (ready a)) (:goal (complete b)))"""
    kernel = Kernel(parse(domain, problem))
    observer = Observation(kernel)
    result = search(kernel, observer, kernel.start)
    require(result["status"] == "resolved" and len(result["plan"]) == 1 and
            ("ready", "a") not in kernel.facts(result["end"]), "typed negative/delete semantics regressed")
    verify_source(kernel, observer, kernel.start, result["plan"], result["end"])
    try:
        parse(domain.replace(":strips", ":durative-actions"), problem)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported requirement was silently lowered")
    pair_domain = """(define (domain pairs) (:requirements :strips)
      (:predicates (marked ?x))
      (:action both :parameters (?x ?y) :precondition (and)
        :effect (and (marked ?x) (marked ?y))))"""
    pair_task = """(define (problem pair-test) (:domain pairs) (:objects a b c)
      (:init) (:goal (and (marked a) (marked b) (marked c))))"""
    pairs = Kernel(parse(pair_domain, pair_task))
    pair_goal = Observation(pairs)
    pair_orbit = Quotient(pairs, pair_goal)
    edges = list(successors(pairs, pair_goal, pairs.start, pair_orbit))
    require(sorted(size for _, _, size in edges) == [3, 6] and
            len({pair_orbit.key(after) for _, after, _ in edges}) == 2,
            "action orbit lost the distinction between equal and distinct argument bindings")
    verify_partition(pairs, pair_goal, pair_orbit)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True, help="directory for pinned published files")
    parser.add_argument("--fetch", action="store_true", help="retrieve source bytes by pinned Git blob ID")
    options = parser.parse_args()
    pins = json.loads((ROOT / "sources.json").read_text())
    if options.fetch:
        fetch_sources(options.sources, pins)
    library = {}
    with TemporaryDirectory() as root:
        results = [run_one(domain, problem, options.sources, Path(root), pins, raw, complete, library)
                   for domain, problem, raw, complete in CASES]
    print(json.dumps({"upstream": REF, "parser_controls": parser_controls(), "tasks": results},
                     sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
