"""Fit the recurrent transition and readout heads from native observations."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recurrent_fit import fit_recurrent

STATE_COUNT = 13
INPUT_COUNT = 7

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("transitions", type=Path)
parser.add_argument("outcomes", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()


def state_id(sample):
    level, count = sample["level"], sample["count"]
    return 0 if not count else 1 + (level - 1) * 2 + int(count >= 2)


transitions = json.loads(args.transitions.read_text())
outcomes = json.loads(args.outcomes.read_text())
observed_inputs = {(state_id(row["before"]), row["token"]) for row in transitions}
assert len(observed_inputs) == STATE_COUNT * (INPUT_COUNT - 1) == len(transitions)
refusals = {row["refusal"] for row in outcomes if not row["allowed"]}
inherited = {row["inherited"] for row in outcomes if row["allowed"]}
assert len(refusals) == len(inherited) == 1
inheritance = inherited.pop()
assert isinstance(inheritance, bool)
outcome_states = [state_id(row) for row in outcomes]
assert set(outcome_states) == set(range(STATE_COUNT))
readouts = [
    (outcome_states, list(range(INPUT_COUNT)), [row["level"] for row in outcomes]),
    (outcome_states, [False, True], [int(row["allowed"]) for row in outcomes]),
    (outcome_states, [False, True], [int(inheritance)] * len(outcomes)),
]
export, steps = fit_recurrent(
    [(state_id(row["before"]), row["token"], state_id(row["after"])) for row in transitions],
    STATE_COUNT, INPUT_COUNT, readouts, [refusals.pop()],
)
args.output.write_text(json.dumps(export, separators=(",", ":")) + "\n")
print(f"Fit {len(transitions)} native transitions and {len(outcomes)} terminal decisions in {steps} optimizer steps")
