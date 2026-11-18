"""Replay the exact parts of the TodoMVC experiment with the standard library alone."""

from collections import Counter
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from compose import analyse
from deck import Deck, digest
from original import sequences
from tasks import build

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "model-applications" / "vertical"))

from runtime import Host  # noqa: E402
from storage import Journal  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check_original():
    """Every recorded step of the original application against the Deck: availability and what is shown."""
    record = json.loads((ROOT / "traces.json").read_text())
    deck, plan = Deck(), sequences()
    require(len(plan) == len(record["traces"]), "recorded traces do not match the generated sequences")
    steps, kinds, different, longest = 0, Counter(), [], 0
    for number, (sequence, trace) in enumerate(zip(plan, record["traces"])):
        state = deck.initial()
        require(deck.observe(state) == trace[0]["seen"], "initial screens differ")
        for index, ((kind, args), seen) in enumerate(zip(sequence, trace[1:])):
            # Leaving an edit with Escape changes nothing. It is available exactly when the row exists.
            after = (state if deck.bind(state, "edit", args) is not None else None) if kind == "edit_cancel" \
                else deck.step(state, kind, args)
            available = after is not None
            state = after if available else state
            # Typing into the new-todo field and choosing a filter are always possible in the original.
            same = (kind in ("add", "show") or available == seen["available"]) and deck.observe(state) == seen["seen"]
            steps += 1
            kinds[kind] += 1
            longest = max(longest, len(state["todos"]))
            if not same:
                different.append([number, index, kind])
                break
    require(not different, f"the Deck differs from the original: {different[:5]}")
    return {"source": record["source"], "commit": record["commit"], "example": record["example"],
            "sequences": len(plan), "steps_equal_to_original": steps, "steps_by_action": dict(sorted(kinds.items())),
            "most_todos_at_once": longest, "definition_sha256": digest(deck.definition)}


def check_host():
    """The unchanged durable Host runs the entity Deck: commits, atomic plans, stale epochs, replay from the journal."""
    deck = Deck()
    with TemporaryDirectory() as root:
        path = Path(root) / "journal"
        host = Host(Journal(path), deck)
        for number, text in enumerate(["buy milk", "call mum", "pay rent"]):
            receipt = host.dispatch("add", "user", {"text": text}, f"add-{number}", number)
            require(receipt["status"] == "committed", "add was not committed")
        require(host.dispatch("add", "user", {"text": "   "}, "blank", 3) == {"status": "refused"}, "blank todo accepted")
        require(host.dispatch("toggle", "user", {"row": 0}, "late", 0) == {"status": "stale"}, "stale epoch accepted")
        require(host.dispatch("toggle", "stranger", {"row": 0}, "who", 3) == {"status": "refused"}, "unknown actor")
        plan = [{"action": "toggle", "args": {"row": 0}}, {"action": "toggle", "args": {"row": 2}},
                {"action": "clear_completed", "args": {}}, {"action": "destroy", "args": {"row": 7}}]
        require(host.dispatch_plan("user", plan, "bad-plan", 3) == {"status": "refused"}, "half a plan was committed")
        require(host.snapshot()["epoch"] == 3, "a refused plan changed the state")
        receipt = host.dispatch_plan("user", plan[:3], "plan", 3)
        require(receipt["status"] == "committed" and host.dispatch_plan("user", plan[:3], "plan", 3) == receipt,
                "plan was not committed exactly once")
        fields = host.snapshot()["fields"]
        require([row["title"] for row in fields["todos"]] == ["call mum"], "plan had the wrong result")
        require(Host(Journal(path), Deck()).snapshot()["fields"] == fields, "journal replay diverged")
    return {"commits": 4, "refused": ["blank title", "unknown actor", "plan with an impossible step"],
            "stale_epoch": "refused", "atomic_plan_steps": 3, "retry": "same receipt", "journal_replay": "equal"}


def check_composition():
    """Recompute the outcome posterior from recorded candidates. No model is loaded."""
    record = json.loads((ROOT / "results" / "candidates.json").read_text())
    seed = record["seed"]
    cohorts = {"scored": build(seed, record["tasks"]["scored"], "test"),
               "weight selection": build(seed + 5, record["tasks"]["weight selection"], "test")}
    return {"samples_per_labelling": record["samples_per_view"], **analyse(Deck(), seed, cohorts, record["candidates"])}


def measured():
    """Recorded runs. These numbers come from `train.py` and `generalist.py` and are not recomputed here."""
    runs = {}
    for path in sorted((ROOT / "results").glob("*.json")):
        run = json.loads(path.read_text())
        if "cohorts" in run:
            runs[run["arm"]] = {
                "backbone": run["backbone"], "training_examples": run["training"]["examples"],
                "cohorts": {name: {key: value for key, value in cohort.items() if key != "by_kind"}
                            for name, cohort in run["cohorts"].items()},
                "looser_wording_by_kind": {kind: [row.get("right", 0), row["tasks"]] for kind, row in
                                           run["cohorts"]["unseen titles, looser wording"]["by_kind"].items()}}
        elif path.name == "generalist-probe.json":
            runs["frozen model scoring the Deck's descriptions"] = {
                name: {key: value for key, value in row.items() if key != "by_kind"} for name, row in run.items()}
    return runs


def main():
    print(json.dumps({"deck_equals_original_todomvc": check_original(),
                      "entity_deck_on_durable_host": check_host(),
                      "experts_composed_over_exact_outcomes": check_composition(),
                      "recorded_runs": measured()}, indent=1))


if __name__ == "__main__":
    main()
