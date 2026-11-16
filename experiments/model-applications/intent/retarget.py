"""Conservative language grounding and cost-neutral retargeting of an existing Deck."""

from dataclasses import dataclass
from itertools import product
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "structural"))
from deck import bits_from_state, commit, digest, issue
from orbit import Observation, Quotient, search


WORD = r"[a-z][a-z0-9_-]*"
MOVE = re.compile(rf"(?:move|take|bring|deliver) (.*?) to ({WORD})")
STAY = re.compile(rf"(keep|leave) ({WORD}) in ({WORD})(?: (throughout|at the end|for the rest of the plan))?")
COLLECTIVE = re.compile(rf"(?:all|every) (?:the )?({WORD})(?: except ({WORD}))?")


@dataclass(frozen=True)
class Meaning:
    goal: frozenset
    invariant: frozenset

    def key(self):
        return [sorted(self.goal), sorted(self.invariant)]


class Lexicon:
    """A scope-specific mapping. Word meaning is declared for this pilot."""

    def __init__(self, kernel, relation, collections):
        self.kernel = kernel
        self.relation = relation
        self.collections = {
            word: frozenset(atom[1] for atom in kernel.task.static_initial if len(atom) == 2 and atom[0] == concept)
            for word, concept in collections.items()
        }

    def fact(self, subject, place):
        atom = (self.relation, subject, place)
        if atom not in self.kernel.index:
            raise ValueError(f"Unknown object/location pair: {subject} in {place}")
        return atom

    def objects(self, selection):
        match = COLLECTIVE.fullmatch(selection)
        if match:
            noun, exception = match.groups()
            members = self.collections.get(noun, frozenset())
            if not members or (exception is not None and exception not in members):
                raise ValueError("Unknown collection or exception")
            if exception is not None:
                members = members - {exception}
            if not members:
                raise ValueError("The collection has no matching objects")
            return members
        if selection in self.kernel.task.objects:
            return {selection}
        raise ValueError(f"Unknown object reference: {selection}")


def parse(request, vocabulary, milestones=None):
    """Return every interpretation of the supported natural task grammar."""
    line = " ".join(request.lower().strip().removesuffix(".").split())
    if line.startswith("change of plan: "):
        line = line.removeprefix("change of plan: ")
    words = re.split(r",? and ", line)
    if not 1 <= len(words) <= 5:
        raise ValueError("Expected one to five supported clauses")
    clauses = []
    for clause in words:
        if milestones and clause in milestones:
            clauses.append([Meaning(frozenset(milestones[clause]), frozenset())])
        elif match := MOVE.fullmatch(clause):
            selection, place = match.groups()
            goal = frozenset(vocabulary.fact(subject, place) for subject in vocabulary.objects(selection))
            clauses.append([Meaning(goal, frozenset())])
        elif match := STAY.fullmatch(clause):
            verb, subject, place, scope = match.groups()
            fact = frozenset({vocabulary.fact(subject, place)})
            final = Meaning(fact, frozenset())
            persistent = Meaning(fact, fact)
            if scope == "at the end":
                if verb == "keep":
                    raise ValueError("Use leave for a final-only constraint")
                clauses.append([final])
            elif verb == "keep" or scope is not None:
                clauses.append([persistent])
            else:
                clauses.append([final, persistent])
        else:
            raise ValueError(f"Unsupported clause: {clause}")
    meanings = set()
    for combination in product(*clauses):
        meanings.add(Meaning(frozenset().union(*(meaning.goal for meaning in combination)),
                             frozenset().union(*(meaning.invariant for meaning in combination))))
    return tuple(sorted(meanings, key=Meaning.key))


def observe(kernel, meaning):
    return Observation(kernel, goal_yes=meaning.goal, goal_no=(), always_yes=meaning.invariant)


def decide(request, vocabulary, start, milestones=None):
    try:
        meanings = parse(request, vocabulary, milestones)
    except ValueError as error:
        return {"status": "unsupported", "explanation": str(error)}
    kernel = vocabulary.kernel

    def shortest(meaning):
        observed = observe(kernel, meaning)
        return search(kernel, observed, start, Quotient(kernel, observed))

    alternatives = [shortest(meaning) for meaning in meanings]
    descriptions = [meaning.key() for meaning in meanings]
    if any(result["status"] == "budget-exhausted" for result in alternatives):
        return {"status": "unverified", "meanings": descriptions}
    costs = [len(result["plan"]) if result["status"] == "resolved" else None for result in alternatives]
    joint = Meaning(frozenset().union(*(meaning.goal for meaning in meanings)),
                    frozenset().union(*(meaning.invariant for meaning in meanings)))
    if len(meanings) == 1:
        combined = alternatives[0]
        failure = "infeasible"
    else:
        combined = shortest(joint)
        failure = "clarify"
    if combined["status"] == "budget-exhausted":
        return {"status": "unverified", "meanings": descriptions}
    joint_cost = len(combined["plan"]) if combined["status"] == "resolved" else None
    if joint_cost is None or any(cost != joint_cost for cost in costs):
        result = {"status": failure, "meanings": descriptions, "costs": costs, "joint_cost": joint_cost}
        if failure == "clarify":
            common = frozenset.intersection(*(meaning.invariant for meaning in meanings))
            _, subject, place = min(joint.invariant - common)
            result["question"] = f"Must {subject} stay in {place} throughout, or only be there at the end?"
        return result
    return {
        "status": "resolved", "meanings": descriptions, "costs": costs,
        "joint": joint, "solution": combined,
    }


def prepare(host, request, vocabulary, milestones=None):
    state = host.snapshot()
    result = decide(request, vocabulary, bits_from_state(state), milestones)
    if result["status"] != "resolved":
        return result, None
    proof = issue(host, observe(vocabulary.kernel, result["joint"]), result["solution"])
    if proof["epoch"] != state["epoch"]:
        return {"status": "stale"}, None
    proof["request"] = digest(request)
    proof["meanings"] = digest(result["meanings"])
    return result, proof


def execute(host, request, proof, key, vocabulary, milestones=None):
    if proof["request"] != digest(request):
        return {"status": "refused"}
    state = host.snapshot()
    if prior := state["receipts"].get(key):
        return prior["result"] if prior["fingerprint"] == digest(proof) else {"status": "key-conflict"}
    if proof["epoch"] != state["epoch"] or proof["checkpoint"] != state["checkpoint"]:
        return {"status": "stale"}
    result = decide(request, vocabulary, bits_from_state(state), milestones)
    if (result["status"] != "resolved" or proof["meanings"] != digest(result["meanings"])
            or proof["plan"] != result["solution"]["plan"] or proof["final"] != result["solution"]["end"]):
        return {"status": "refused"}
    return commit(host, observe(vocabulary.kernel, result["joint"]), proof, key)
