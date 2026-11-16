"""Compile a formal application into shared exact model weights and commit certified plans."""

from hashlib import sha256
from itertools import product
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "vertical"))

from compiler import Graph, canonical, compile_contract, digest, integer_forward
from pddl import interpret, require
from runtime import Host, preview_plan
from storage import Journal, Relational, migrate


ACTOR = "operator"


def layout(op):
    """Order grounded facts by their computation role, then their display identity."""
    all_atoms = op.yes | op.no | op.adds | op.deletes
    entries = [(int(atom in op.yes) - int(atom in op.no),
                int(atom in op.adds) - int(atom in op.deletes), atom)
               for atom in all_atoms]
    return sorted(entries)


def balanced_and(expressions):
    if not expressions:
        return 1
    if len(expressions) == 1:
        return expressions[0]
    middle = len(expressions) // 2
    return ["and", balanced_and(expressions[:middle]), balanced_and(expressions[middle:])]


def precondition(inputs, roles):
    return balanced_and([name if guard > 0 else ["not", name]
                         for name, (guard, _) in zip(inputs, roles, strict=True) if guard])


def compile_shared(kernel, source_hash, library=None, frozen=False):
    """Encode executable structure once per slot-role pattern."""
    fields = {f"f{i}": [0, 1, int(atom in kernel.task.initial)] for i, atom in enumerate(kernel.atoms)}
    modules, ids, actions = {}, {}, {}
    library = {} if library is None else library
    reused = 0
    for index, op in enumerate(kernel.task.operators):
        entries = layout(op)
        names = [f"v{i}" for i in range(len(entries))]
        roles = tuple((guard, write) for guard, write, _ in entries)
        if roles not in ids:
            module_id = str(len(ids))
            if roles in library:
                modules[module_id] = library[roles]
            else:
                require(not frozen, "operator shape was absent from the frozen module library")
                graph = Graph(set(names) | {"opid"}, set(names))
                modules[module_id] = {
                    "guard": graph.expression(precondition(names, roles)),
                    "writes": {name: graph.expression(int(write > 0))
                               for name, (_, write) in zip(names, roles, strict=True) if write},
                    "effects": [["transition", graph.expression("opid")]],
                    "nodes": graph.nodes,
                    "roles": roles,
                }
            ids[roles] = module_id
        reused += int(roles in library)
        actions[f"a{index}"] = {
            "label": " ".join(op.signature), "module": ids[roles],
            "binding": [f"f{kernel.index[atom]}" for _, _, atom in entries],
            "id": index, "signature": op.signature,
        }
    return {
        "format": "holodeck.shared-affine/1", "source": source_hash,
        "fields": fields, "public": list(fields), "actors": {ACTOR: 0},
        "initial_revision": 1, "policies": {"1": 0, "2": 0},
        "modules": modules, "actions": actions, "from_prior_library": reused,
    }


def compile_flat(kernel, shared):
    actions = {}
    for index, op in enumerate(kernel.task.operators):
        entries = layout(op)
        names = shared["actions"][f"a{index}"]["binding"]
        roles = tuple((guard, write) for guard, write, _ in entries)
        actions[f"a{index}"] = {
            "label": " ".join(op.signature), "guard": precondition(names, roles),
            "writes": {name: int(write > 0) for name, (_, write) in zip(names, roles, strict=True) if write},
            "effects": [["transition", index]],
        }
    return compile_contract({
        "fields": shared["fields"], "public": shared["public"], "actors": shared["actors"],
        "initial_revision": 1, "revisions": {"1": 0}, "actions": actions,
    })


class SharedModel:
    def __init__(self, checkpoint):
        require(checkpoint["format"] == "holodeck.shared-affine/1", "unsupported artifact")
        self.checkpoint = checkpoint

    def propose(self, state, action, actor, args):
        if actor not in self.checkpoint["actors"] or args or action not in self.checkpoint["actions"]:
            return None
        if str(state["revision"]) not in self.checkpoint["policies"]:
            return None
        head = self.checkpoint["actions"][action]
        binding = dict(zip((f"v{i}" for i in range(len(head["binding"]))), head["binding"], strict=True))
        inputs = {slot: state["fields"][field] for slot, field in binding.items()}
        output = integer_forward(self.checkpoint["modules"][head["module"]], {**inputs, "opid": head["id"]})
        if output is None:
            return None
        return {
            "writes": {binding[slot]: value for slot, value in output["writes"].items()},
            "effects": output["effects"],
        }


def field_values(kernel, state):
    return {f"f{i}": int(bool(state & (1 << i))) for i in range(len(kernel.atoms))}


def bits_from_state(state):
    return sum(value << int(key[1:]) for key, value in state["fields"].items())


def verify_source(kernel, observation, initial, plan, final):
    state = kernel.facts(initial)
    for index in plan:
        answer = interpret(kernel.task, state, kernel.task.operators[index].signature)
        require(answer is not None and observation.allowed(kernel.mask(answer)), "source rejected plan step")
        state = answer
    require(kernel.mask(state) == final and observation.achieved(final), "source rejected plan outcome")


def certify_weights(kernel, model, flat):
    """Exhaust all local inputs of each grounded operator against three independent paths."""
    checked = 0
    for index, op in enumerate(kernel.task.operators):
        footprint = sorted(op.yes | op.no | op.adds | op.deletes)
        require(len(footprint) <= 12, "operator exceeds local truth-table qualification")
        ids = [kernel.index[atom] for atom in footprint]
        for assignment in product((0, 1), repeat=len(footprint)):
            source = frozenset(atom for atom, value in zip(footprint, assignment, strict=True) if value)
            state = sum(value << bit for bit, value in zip(ids, assignment, strict=True))
            snapshot = {"revision": 1, "fields": {f"f{bit}": value
                        for bit, value in zip(ids, assignment, strict=True)}}
            reference = interpret(kernel.task, source, op.signature)
            expected = None if reference is None else kernel.mask(reference)
            require(expected == kernel.next(state, index), "ground transition diverged from source")
            proposal = model.propose(snapshot, f"a{index}", ACTOR, {})
            require(proposal == flat.propose(snapshot, f"a{index}", ACTOR, {}),
                    "shared forward differs from flat compiler")
            if proposal is None:
                require(expected is None, "shared model blocked a source action")
            else:
                updates = {"fields": {**snapshot["fields"], **proposal["writes"]}}
                require(bits_from_state(updates) == expected and proposal["effects"] == [["transition", index]],
                        "shared model produced incorrect state or receipt")
            checked += 1
    return checked


def observer_digest(observer):
    return digest([list(observer.positions), observer.impossible])


def issue(host, observer, solution):
    state = host.snapshot()
    return {
        "format": 1, "epoch": state["epoch"], "checkpoint": state["checkpoint"],
        "observer": observer_digest(observer), "plan": solution["plan"], "final": solution["end"],
    }


def commit(host, observer, certificate, key):
    def proposed(state):
        if (state["checkpoint"] != certificate["checkpoint"] or
                certificate["observer"] != observer_digest(observer) or not observer.allowed(bits_from_state(state))):
            return None
        plan = certificate["plan"]
        if len(plan) > 1024 or any(type(index) is not int or index < 0 for index in plan):
            return None
        working = {**state, "fields": dict(state["fields"])}
        effects = []
        for index in plan:
            proposal = preview_plan(host.propose, working, ACTOR, [{"action": f"a{index}", "args": {}}])
            if proposal is None:
                return None
            working["fields"].update(proposal["writes"])
            if not observer.allowed(bits_from_state(working)):
                return None
            effects.extend(proposal["effects"])
        reached = bits_from_state(working)
        if reached != certificate["final"] or not observer.achieved(reached):
            return None
        return {
            "writes": {name: value for name, value in working["fields"].items() if state["fields"][name] != value},
            "effects": effects,
        }
    return host.commit(digest(certificate), key, certificate["epoch"], proposed, None)


def replay_store(root, model, observer, solution, prefix=()):
    source = Host(Journal(root / "journal"), model)
    if prefix:
        steps = [{"action": f"a{index}", "args": {}} for index in prefix]
        advanced = source.dispatch_plan(ACTOR, steps, "earlier-observed-work", 0)
        require(advanced["status"] == "committed", "starting prefix was refused")
    old = issue(source, observer, solution)
    source.activate(2, old["epoch"])
    target = Relational(root / "sqlite")
    migrate(source.store, target)
    restored = Host(target, model)
    require(commit(restored, observer, old, "old") == {"status": "stale"},
            "previous revision certificate survived migration")
    proof = issue(restored, observer, solution)
    before = restored.snapshot()
    wrong = {**proof, "final": proof["final"] ^ 1}
    require(commit(restored, observer, wrong, "wrong") == {"status": "refused"} and restored.snapshot() == before,
            "misstated completion changed durable state")
    changed_context = {**proof, "observer": "wrong"}
    require(commit(restored, observer, changed_context, "context") == {"status": "refused"} and
            restored.snapshot() == before, "mismatched observer changed durable state")
    receipt = commit(restored, observer, proof, "certified")
    reread = Host(Relational(root / "sqlite"), model)
    require(receipt["status"] == "committed" and
            [entry["value"] for entry in receipt["effects"]] == proof["plan"] and
            commit(reread, observer, proof, "certified") == receipt and
            bits_from_state(reread.snapshot()) == solution["end"],
            "concrete transaction or idempotent restart receipt diverged")
    return {"one_transaction": True, "revision_invalidates_proof": True,
            "ordered_named_actions": len(receipt["effects"]), "restarted": True}


def sizes(shared, flat):
    return {
        "weight_modules": len(shared["modules"]), "grounded_operators": len(shared["actions"]),
        "groundings_reusing_a_prior_module": shared["from_prior_library"],
        "shared_graph_nodes": sum(len(x["nodes"]) for x in shared["modules"].values()),
        "flat_graph_nodes": sum(len(x["nodes"]) for x in flat["modules"]["1"].values()),
        "shared_bytes": len(canonical(shared).encode()), "flat_bytes": len(canonical(flat).encode()),
    }


def source_digest(domain, problem):
    return sha256(domain + b"\0" + problem).hexdigest()
