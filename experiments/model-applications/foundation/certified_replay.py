"""Exercise plan certification, effect parity and lossless migration without model weights."""

from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "vertical"))

from compiler import Model, compile_contract, require
from preferences import board, invoke_certified, resolve, typed_projection
from runtime import Host
from storage import Journal, Relational, migrate


def experiment(root):
    definition = json.loads((HERE / "effect_contracts.json").read_text())["compute"]
    compiled = Model(compile_contract(definition))
    journal = Host(Journal(root / "journal"), compiled)
    plans = {
        "interactive": [{"action": "warm", "args": {}}, {"action": "run", "args": {}}],
        "batch": [{"action": "batch", "args": {}}],
    }
    actor = "operator"
    earliest = (1, 1)

    def prepare(host, interval):
        state = host.snapshot()
        effects = board(compiled, state, actor, plans)
        return typed_projection({"actor": actor, "epoch": state["epoch"], "interval": interval},
                                resolve(effects, interval), effects)

    old = prepare(journal, earliest)
    require(old["name"] == "interactive", "unexpected initial fastest option")
    require(prepare(journal, (0, 1))["kind"] == "question", "missing clarification before a tradeoff")
    journal.activate(2, 0)
    target = Relational(root / "db")
    migrate(journal.store, target)
    host = Host(target, compiled)
    require(invoke_certified(host, actor, plans, earliest, old, "old") == {"status": "stale"},
            "obsolete certificate accepted on new state")
    fresh = prepare(host, earliest)
    require(fresh["name"] == "batch", "rule change did not reverse the preferred plan")
    for mutation in ("time", "steps", "proof"):
        forged = deepcopy(fresh)
        if mutation == "time":
            forged["time"] += 1
        elif mutation == "steps":
            forged["handle"]["steps"] = plans["interactive"]
        else:
            forged["proof"]["choice"] = "interactive"
        state = host.snapshot()
        require(invoke_certified(host, actor, plans, earliest, forged, mutation) == {"status": "refused"}
                and host.snapshot() == state, "forged certificate reached durable state")
    declined = [{"action": "warm", "args": {}}, {"action": "warm", "args": {}}]
    state = host.snapshot()
    require(host.dispatch_plan(actor, declined, "partial", state["epoch"]) == {"status": "refused"}
            and host.snapshot() == state, "valid prefix of a refused plan leaked a write")
    receipt = invoke_certified(host, actor, plans, earliest, fresh, "certified")
    require(receipt["status"] == "committed", "valid certificate did not produce a durable receipt")
    have = [(value["kind"], value["value"]) for value in receipt["effects"]]
    require(have == [("time", fresh["time"]), ("price", fresh["price"])], "shown facts changed during commit")
    return {"old_selected": old["name"], "new_selected": fresh["name"],
            "retained_effect_order": [kind for kind, _ in have], "forged_cases_rejected": 3,
            "stale_after_migration": True, "late_refusal_atomic": True}


if __name__ == "__main__":
    with TemporaryDirectory() as directory:
        print(json.dumps(experiment(Path(directory)), indent=2, sort_keys=True))
