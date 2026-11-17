"""Finite counterexamples for context-sensitive application state."""

from collections import deque
from dataclasses import dataclass, replace
from itertools import combinations
import json


@dataclass(frozen=True)
class State:
    authority: bool = True
    revision: int = 1
    pending: int = 0
    label: int = 0
    layout: str = "rows"


EVENTS = ("stamp", "request", "finish", "revoke", "activate", "move")
CURRENT_EVENTS = tuple(event for event in EVENTS if event != "activate")


def screen(state):
    label = state.label if state.revision == 2 else None
    return state.revision, bool(state.pending), label


def observation(state):
    controls = ("request",) if state.authority and not state.pending else ()
    return screen(state), controls


def step(state, event):
    if event == "stamp" and state.revision == 1:
        return replace(state, label=1 - state.label), ("accepted",)
    if event == "request" and state.authority and not state.pending:
        return replace(state, pending=state.revision), ("accepted",)
    if event == "finish" and state.pending:
        effects = ("reserve", "notify") if state.pending == 1 else ("notify", "reserve")
        return replace(state, pending=0), ("accepted", *effects)
    if event == "revoke":
        return replace(state, authority=False), ("accepted",)
    if event == "activate":
        return replace(state, revision=2), ("accepted",)
    if event == "move":
        layout = "rows" if state.layout == "log" else "log"
        return replace(state, layout=layout), ("accepted",)
    return state, ("refused",)


def reachable(events):
    seen = {State(): None}
    queue = deque(seen)
    while queue:
        state = queue.popleft()
        for event in events:
            successor, _ = step(state, event)
            if successor not in seen:
                seen[successor] = None
                queue.append(successor)
    return tuple(seen)


def partition(states, events):
    classes = dict.fromkeys(states, 0)
    while True:
        signatures = {}
        refined = {}
        for state in states:
            outgoing = tuple(step(state, event) for event in events)
            signature = (
                observation(state),
                tuple((receipt, classes[next_state]) for next_state, receipt in outgoing),
            )
            refined[state] = signatures.setdefault(signature, len(signatures))
        if classes == refined:
            return classes
        classes = refined


def witness(left, right, events):
    """Independently find a shortest distinguishing event word in the product."""
    queue = deque([(left, right, (), False)])
    seen = {(left, right)}
    while queue:
        left, right, prefix, receipt_differs = queue.popleft()
        if receipt_differs or observation(left) != observation(right):
            return prefix
        for event in events:
            next_left, left_receipt = step(left, event)
            next_right, right_receipt = step(right, event)
            if left_receipt != right_receipt:
                queue.append((next_left, next_right, (*prefix, event), True))
            elif (next_left, next_right) not in seen:
                seen.add((next_left, next_right))
                queue.append((next_left, next_right, (*prefix, event), False))
    return None


def follow(*events):
    state = State()
    for event in events:
        state, _ = step(state, event)
    return state


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    current_states = reachable(CURRENT_EVENTS)
    future_states = reachable(EVENTS)
    current = partition(current_states, CURRENT_EVENTS)
    future = partition(future_states, EVENTS)
    pairs_checked = 0
    for classes, events in ((current, CURRENT_EVENTS), (future, EVENTS)):
        for left, right in combinations(classes, 2):
            equivalent = witness(left, right, events) is None
            check(equivalent == (classes[left] == classes[right]), "quotient disagrees with product")
            pairs_checked += 1

    v1 = State()
    hidden = follow("stamp")
    revoked = follow("revoke")
    old_pending = follow("request", "activate")
    new_pending = follow("activate", "request")
    moved = follow("request", "activate", "move")

    check(screen(v1) == screen(revoked), "authority fixture requires equal screen content")
    check(screen(old_pending) == screen(new_pending), "pin fixture requires equal screen content")
    check(witness(v1, hidden, CURRENT_EVENTS) is None, "label should be hidden under current contexts")
    activation = witness(v1, hidden, EVENTS)
    check(activation == ("activate",), "activation must be the shortest retention witness")
    authority = witness(v1, revoked, EVENTS)
    check(authority == (), "current authorized controls should expose a distinction")
    pinned = witness(old_pending, new_pending, EVENTS)
    check(pinned == ("finish",), "pending revision should change the ordered receipt")
    check(witness(old_pending, moved, EVENTS) is None, "faithful representation move changed behavior")
    lossy = replace(moved, pending=moved.revision)
    check(witness(old_pending, lossy, EVENTS) == ("finish",), "lossy move escaped qualification")

    fibers = {}
    for state in current_states:
        fibers.setdefault(current[state], set()).add(future[state])
    fiber_width = max(map(len, fibers.values()))
    return {
        "current": {"reachable_states": len(current), "classes": len(set(current.values()))},
        "with_activation": {"reachable_states": len(future), "classes": len(set(future.values()))},
        "independent_state_pairs_checked": pairs_checked,
        "max_future_classes_in_current_class": fiber_width,
        "minimum_fixed_retention_bits_beyond_current_class": (fiber_width - 1).bit_length(),
        "witnesses": {
            "hidden_label_under_current_version": None,
            "hidden_label_under_activation": activation,
            "authority_with_identical_screen_content": authority,
            "ordered_effects_with_identical_screen_content": pinned,
            "faithful_storage_move": None,
            "lossy_storage_move": pinned,
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
