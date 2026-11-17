"""Generic serving, projection, commits and a matched exact tool boundary."""

from copy import deepcopy
from itertools import product
import json
from threading import Lock

from compiler import canonical, digest
from storage import Conflict


class Interrupted(Exception):
    pass


class ExternalTools:
    """Round-trip typed requests and results through a separate exact tool boundary."""

    def __init__(self, model):
        self.model = model
        self.calls = 0
        self.lock = Lock()

    def propose(self, state, action, actor, args):
        with self.lock:
            self.calls += 1
        request = json.loads(canonical([state, action, actor, args]))
        return json.loads(canonical(self.model.propose(*request)))


def preview_plan(propose, state, actor, steps):
    """Apply all steps to a private snapshot and return one atomic proposal."""
    if not isinstance(steps, list) or len(steps) > 8:
        raise ValueError("expected a plan of at most eight steps")
    working = {**state, "fields": dict(state["fields"])}
    effects = []
    for step in steps:
        if not isinstance(step, dict) or step.keys() != {"action", "args"}:
            raise ValueError("invalid plan step")
        proposal = propose(working, step["action"], actor, step["args"])
        if proposal is None:
            return None
        working["fields"].update(proposal["writes"])
        effects.extend(proposal["effects"])
    return {
        "writes": {key: value for key, value in working["fields"].items() if state["fields"][key] != value},
        "effects": effects,
    }


class Host:
    def __init__(self, store, model, tool=None):
        self.store = store
        self.model = model
        self.propose = (tool or model).propose
        self.checkpoint = digest(model.checkpoint)
        if store.load() is None:
            contract = model.checkpoint
            try:
                store.save(None, {
                    "epoch": 0, "revision": contract["initial_revision"], "checkpoint": self.checkpoint,
                    "fields": {key: bounds[2] for key, bounds in contract["fields"].items()},
                    "receipts": {}, "outbox": [],
                })
            except Conflict:
                pass
        self.snapshot()

    def snapshot(self):
        state = self.store.load()
        if state["checkpoint"] != self.checkpoint:
            raise ValueError("durable state belongs to a different checkpoint")
        return state

    def activate(self, revision, epoch):
        if type(revision) is not int or str(revision) not in self.model.checkpoint["policies"]:
            raise ValueError("unknown revision")
        state = self.snapshot()
        if state["epoch"] != epoch:
            raise Conflict("stale activation")
        state["revision"] = revision
        state["epoch"] += 1
        self.store.save(epoch, state)

    def dispatch(self, action, actor, args, key, epoch, fault=None):
        return self.commit(digest([action, actor, args]), key, epoch,
                           lambda state: self.propose(state, action, actor, args), fault)

    def dispatch_plan(self, actor, steps, key, epoch, fault=None):
        if actor not in self.model.checkpoint["actors"]:
            return {"status": "refused"}
        return self.commit(digest(["plan", actor, steps, "sequence"]), key, epoch,
                           lambda state: preview_plan(self.propose, state, actor, steps), fault)

    def commit(self, fingerprint, key, epoch, transition, fault):
        if type(epoch) is not int or not isinstance(key, str) or not key or len(key) > 128:
            raise ValueError("invalid commit envelope")
        state = self.snapshot()
        prior = state["receipts"].get(key)
        if prior is not None:
            if prior["fingerprint"] != fingerprint:
                return {"status": "key-conflict"}
            return prior["result"]
        if state["epoch"] != epoch:
            return {"status": "stale"}
        proposal = transition(state)
        if proposal is None:
            return {"status": "refused"}
        state["fields"].update(proposal["writes"])
        state["epoch"] += 1
        effects = [
            {"id": f"{key}:{index}", "kind": name, "value": value}
            for index, (name, value) in enumerate(proposal["effects"])
        ]
        state["outbox"].extend(effects)
        result = {"status": "committed", "epoch": state["epoch"], "effects": effects}
        state["receipts"][key] = {"fingerprint": fingerprint, "result": result}
        if fault == "before":
            raise Interrupted("before durable commit")
        try:
            self.store.save(epoch, state)
        except Conflict:
            return {"status": "stale"}
        if fault == "after":
            raise Interrupted("after durable commit")
        return result

    def scene(self, actor):
        state = self.snapshot()
        contract = self.model.checkpoint
        scene = {f"field:{name}": {"kind": "fact", "label": name, "value": state["fields"][name]}
                 for name in contract["public"]}
        for action, rule in contract["actions"].items():
            arguments = sorted(rule.get("args", {}).items())
            for values in product(*(allowed for _, allowed in arguments)):
                args = dict(zip((key for key, _ in arguments), values))
                proposal = self.propose(state, action, actor, args)
                if proposal is None:
                    continue
                followup = rule.get("successor")
                safe = None
                if followup:
                    next_state = {**state, "fields": {**state["fields"], **proposal["writes"]}}
                    safe = self.propose(next_state, followup, actor, {}) is not None
                    if not safe:
                        continue
                label = rule["label"] + (" " + ", ".join(map(str, values)) if values else "")
                handle = {"actor": actor, "action": action, "args": args, "epoch": state["epoch"]}
                scene[f"action:{action}:{canonical(args)}"] = {
                    "kind": "action", "label": label, "handle": handle, "forecast": safe,
                }
        return scene, state["epoch"]

    def patches(self, actor, previous=None, base=None):
        scene, epoch = self.scene(actor)
        previous = previous or {}
        for key in sorted(previous.keys() - scene.keys()):
            yield {"base": base, "epoch": epoch, "drop": key}
        for key in sorted(scene):
            if previous.get(key) != scene[key]:
                yield {"base": base, "epoch": epoch, "put": [key, scene[key]]}
        yield {"base": base, "epoch": epoch, "complete": True}

    def invoke(self, actor, handle, key):
        if actor != handle["actor"]:
            return {"status": "refused"}
        return self.dispatch(handle["action"], actor, handle["args"], key, handle["epoch"])

    def agent_options(self, actor):
        scene, _ = self.scene(actor)
        return [deepcopy(node["handle"]) for node in scene.values() if node["kind"] == "action"]
