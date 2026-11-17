"""Exact program selection under a set or interval of language-grounded objectives."""

from fractions import Fraction

from compiler import digest
from runtime import preview_plan


def board(model, state, actor, plans):
    result = {}
    for name, steps in plans.items():
        proposal = preview_plan(model.propose, state, actor, steps)
        if proposal is not None:
            result[name] = {
                "time": sum(value for kind, value in proposal["effects"] if kind == "time"),
                "price": sum(value for kind, value in proposal["effects"] if kind == "price"),
                "steps": steps,
            }
    return result


def weighted(entry, weight):
    return weight * entry["time"] + (1 - weight) * entry["price"]


def minima(result, weight):
    best = min(weighted(entry, weight) for entry in result.values())
    return {name for name, entry in result.items() if weighted(entry, weight) == best}


def resolve(result, interval):
    """Endpoint intersection is exact for an affine preference on a closed interval."""
    lower, upper = map(Fraction, interval)
    if not 0 <= lower <= upper <= 1:
        raise ValueError("invalid preference interval")
    if not result:
        return {"status": "infeasible"}
    at_lower = minima(result, lower)
    at_upper = minima(result, upper)
    stable = at_lower & at_upper
    if stable:
        chosen = min(stable)
        proof = {"board": digest(result), "interval": [str(lower), str(upper)], "choice": chosen}
        return {"status": "selected", "name": chosen, "steps": result[chosen]["steps"], "proof": proof}
    return {
        "status": "clarify", "lower_optima": sorted(at_lower), "upper_optima": sorted(at_upper),
        "question": "Should I prioritize shorter completion time or lower price?",
    }


def verify_selection(result, interval, proof):
    """Check two linear inequalities per competitor, including ties."""
    lower, upper = map(Fraction, interval)
    if not 0 <= lower <= upper <= 1:
        return False
    if proof["board"] != digest(result) or proof["interval"] != [str(lower), str(upper)]:
        return False
    candidate = result.get(proof["choice"])
    if candidate is None:
        return False
    for other in result.values():
        for weight in (lower, upper):
            difference = weight * (other["time"] - candidate["time"])
            difference += (1 - weight) * (other["price"] - candidate["price"])
            if difference < 0:
                return False
    return True


def typed_projection(case, outcome, result):
    if outcome["status"] != "selected":
        return {"kind": "question", "prompt": outcome.get("question", "No feasible plan")}
    name = outcome["name"]
    proof = outcome["proof"]
    if proof["choice"] != name or not verify_selection(result, case["interval"], proof):
        raise ValueError("invalid plan choice proof")
    return {
        "kind": "verified-plan",
        "name": name,
        "proof": proof,
        "time": result[name]["time"],
        "price": result[name]["price"],
        "handle": {"actor": case["actor"], "epoch": case["epoch"], "steps": outcome["steps"]},
    }


def invoke_certified(host, actor, plans, interval, projection, key):
    """Revalidate a plan Holo against the actual snapshot before the atomic commit."""
    handle = projection["handle"]
    if handle["actor"] != actor:
        return {"status": "refused"}
    state = host.snapshot()
    if state["epoch"] != handle["epoch"]:
        return {"status": "stale"}
    result = board(host.model, state, actor, plans)
    proof = projection["proof"]
    chosen = result.get(projection["name"])
    if (chosen is None or proof["choice"] != projection["name"] or
        handle["steps"] != chosen["steps"] or projection["time"] != chosen["time"] or
        projection["price"] != chosen["price"] or not verify_selection(result, interval, proof)):
        return {"status": "refused"}
    return host.dispatch_plan(actor, handle["steps"], key, handle["epoch"])
