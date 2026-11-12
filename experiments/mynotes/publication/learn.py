"""Infer a predictive partition and executable labels, then fit the shared recurrence."""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recurrent_fit import fit_recurrent

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("traces", type=Path)
parser.add_argument("browser", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("report", type=Path)
args = parser.parse_args()
corpus = json.loads(args.traces.read_text())
browser = json.loads(args.browser.read_text())
alphabet = corpus["alphabet"]
observations = {}
edges = {}
puts = []


def observe(row, anchor):
    private = row["private"]
    public = row["public"]
    current = private["body"].get("updated_at")
    return (private["status"], public["status"], None if anchor is None else current == anchor)


def consistent(table, key, value):
    previous = table.setdefault(key, value)
    if previous != value:
        raise ValueError(f"Different observations for the same history: {key}")


for trace in corpus["traces"]:
    prefix = ()
    anchor = None
    before = trace["initial"]
    consistent(observations, prefix, observe(before, anchor))
    for step in trace["steps"]:
        event = step["event"]
        after = step["after"]
        response = step["result"]
        changes = step["changes"]
        if len(changes) > 1:
            raise ValueError("The learned effect vocabulary allows one write per publication")
        effect = changes[0]["op"] if changes else None
        if effect == "put":
            anchor = before["private"]["body"]["updated_at"]
            puts.append((before, step, changes[0]))
        elif effect == "delete":
            anchor = None
        error = response["body"].get("error") if isinstance(response["body"], dict) else None
        result = (response["status"], effect, error)
        consistent(edges, (prefix, event), result)
        prefix += (event,)
        consistent(observations, prefix, observe(after, anchor))
        before = after


def signature(prefix):
    # Group by today's observed output and all observed one-step continuations.
    return (
        observations[prefix],
        tuple((edges[(prefix, event)], observations[prefix + (event,)]) for event in alphabet),
    )


complete = sorted(
    (prefix for prefix in observations if all((prefix, event) in edges for event in alphabet)),
    key=lambda prefix: (len(prefix), prefix),
)
representatives = {}
by_prefix = {}
for prefix in complete:
    key = signature(prefix)
    if key not in representatives:
        representatives[key] = (len(representatives), prefix)
    by_prefix[prefix] = representatives[key][0]
states = [prefix for _, prefix in representatives.values()]
transitions = []
transition_table = {}
for state, prefix in enumerate(states):
    for token, event in enumerate(alphabet):
        target = by_prefix.get(prefix + (event,))
        if target is None:
            raise ValueError("The observation table needs another suffix for this transition")
        transitions.append((state, token, target))
        transition_table[state, event] = target

# Validate merging against all native prefixes, including the last column.
for trace in corpus["traces"]:
    state = 0
    prefix = ()
    for step in trace["steps"]:
        event = step["event"]
        representative = states[state]
        assert edges[prefix, event] == edges[representative, event]
        state = transition_table[state, event]
        prefix += (event,)
        assert observations[prefix] == observations[states[state]]
        if prefix in by_prefix:
            assert state == by_prefix[prefix]
        if step["result"]["status"] >= 400:
            assert state == by_prefix.get(prefix[:-1], state), "Refusal would need durable event replay"

# Identify data origins by matching complete native write rows across examples.
fields = {}
for field in ("title", "html", "published_at"):
    candidates = {
        "private.title": lambda before, step: before["private"]["body"]["title"],
        "private.content": lambda before, step: before["private"]["body"]["content"],
        "request.body": lambda before, step: step["body"],
        "response.published_at": lambda before, step: step["result"]["body"]["published_at"],
    }
    matching = [name for name, source in candidates.items()
                if all(row[field] == source(before, step) for before, step, row in puts)]
    if len(matching) != 1:
        raise ValueError(f"Ambiguous durable field binding {field}: {matching}")
    fields[field] = matching[0]

# Record the actual browser's available actions for the inferred states.
projections = {}
for view in browser["views"]:
    state = 0
    for event in view["history"]:
        state = transition_table[state, event]
    consistent(projections, state, {key: view[key] for key in ("trigger", "heading", "controls")})
if set(projections) != set(range(len(states))):
    raise ValueError("Missing native browser observation for an inferred state")

def readout(values):
    labels = []
    indices = []
    for value in values:
        if value not in labels:
            labels.append(value)
        indices.append(labels.index(value))
    return list(range(len(states))), labels, indices

readouts = [
    readout([{
        "privateStatus": observations[prefix][0],
        "publicStatus": observations[prefix][1],
        "matchesRevision": observations[prefix][2],
        **projections[state],
    } for state, prefix in enumerate(states)]),
]
for event in alphabet:
    readouts.append(readout([
        {"status": status, "snapshotEffect": effect, "error": error}
        for status, effect, error in (edges[prefix, event] for prefix in states)
    ]))
weights, steps = fit_recurrent(transitions, len(states), len(alphabet), readouts, [])
missing = next(prefix for prefix in states if observations[prefix][0] == 404)
artifact = {"alphabet": alphabet, "missingHistory": [alphabet.index(event) for event in missing],
            "bindings": fields, "controls": browser["bindings"], "weights": weights}
args.output.write_text(json.dumps(artifact, separators=(",", ":")) + "\n")
args.report.write_text(json.dumps({
    "trainingJourneys": len(corpus["traces"]), "prefixes": len(observations),
    "fitSteps": steps, "states": [{"prefix": prefix, "observable": observations[prefix], "view": projections[i]}
                                  for i, prefix in enumerate(states)],
    "transitions": len(transitions), "nativeSnapshotWrites": len(puts), "bindings": fields,
}, indent=2) + "\n")
print(f"Inferred {len(states)} states from {len(observations)} prefixes, fit {len(transitions)} recurrent transitions")
