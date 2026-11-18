"""Confirm the stated goal, then commit the whole verified plan atomically on the durable Host.

The model's reading of a request is shown back in the contract's own terms together with the exact
plan that reaches it. Nothing is committed before a person accepts that reading. The plan is bound
to the epoch it was computed for, so a state that moved in the meantime makes the confirmation stale.
"""

from worlds import goal_text

MAX_STEPS = 8  # the Host's bound for one atomic plan


def equivalent(world, first, second):
    """Two goals mean the same when they hold in exactly the same states of this contract."""
    return all(world.satisfied(state, first) == world.satisfied(state, second) for state in world.states)


def propose(world, snapshot, actor, goal):
    """What would be committed if the person accepts this goal. Computed by exact search, never by the model."""
    state = tuple(snapshot["fields"][name] for name in world.names)
    proposal = {"actor": actor, "goal": [list(atom) for atom in goal], "text": goal_text(world, goal),
                "epoch": snapshot["epoch"], "steps": [], "labels": []}
    if state not in world.index:
        return {**proposal, "status": "unreachable"}
    if world.satisfied(state, goal):
        return {**proposal, "status": "fulfilled"}
    plan = world.plan(state, actor, goal)
    if plan is None or len(plan) > MAX_STEPS:
        return {**proposal, "status": "unreachable"}
    for candidate in plan:
        action, args = world.candidates[candidate]
        proposal["steps"].append({"action": action, "args": args})
        proposal["labels"].append(world.spec["actions"][action]["label"] + "".join(f" {v}" for v in args.values()))
    return {**proposal, "status": "ready"}


def confirm(host, proposal, key):
    """Commit every step or none. A proposal for an earlier epoch is stale."""
    if proposal["status"] != "ready":
        return {"status": proposal["status"]}
    return host.dispatch_plan(proposal["actor"], proposal["steps"], key, proposal["epoch"])
