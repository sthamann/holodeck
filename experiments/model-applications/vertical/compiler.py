"""Compile a bounded application contract into an integer forward graph."""

import hashlib
import json
from math import prod


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Graph:
    def __init__(self, variables, boolean_inputs):
        self.variables = variables
        self.boolean_inputs = boolean_inputs
        self.nodes = []
        self.interned = {}
        self.booleans = set()

    def node(self, operation, *args):
        key = (operation, *args)
        if key not in self.interned:
            self.interned[key] = len(self.nodes)
            self.nodes.append(key)
        return self.interned[key]

    def affine(self, bias, *terms):
        return self.node("affine", bias, terms)

    def boolean(self, index):
        self.booleans.add(index)
        return index

    def threshold(self, index):
        return self.boolean(self.node("threshold", index))

    def expression(self, syntax):
        if type(syntax) is int and abs(syntax) <= 4096:
            index = self.node("constant", syntax)
            return self.boolean(index) if syntax in (0, 1) else index
        if isinstance(syntax, str):
            require(syntax in self.variables, f"unknown variable {syntax}")
            index = self.node("input", syntax)
            return self.boolean(index) if syntax in self.boolean_inputs else index
        require(isinstance(syntax, list) and bool(syntax), "invalid expression")
        op, *operands = syntax
        arity = {"not": 1, "add": 2, "sub": 2, "le": 2, "eq": 2, "and": 2, "or": 2}
        require(isinstance(op, str) and arity.get(op) == len(operands), f"invalid operation {op}")
        args = [self.expression(arg) for arg in operands]
        if op in ("and", "or", "not"):
            require(all(index in self.booleans for index in args), f"{op} expects Boolean operands")
        x = args[0]
        if op == "not":
            return self.threshold(self.affine(0, (-1, x)))
        y = args[1]
        if op in ("add", "sub"):
            return self.affine(0, (1, x), (-1 if op == "sub" else 1, y))
        if op in ("le", "eq"):
            lower = self.threshold(self.affine(0, (-1, x), (1, y)))
            if op == "le":
                return lower
            upper = self.threshold(self.affine(0, (1, x), (-1, y)))
            x, y = lower, upper
        return self.threshold(self.affine(-1 if op == "or" else -2, (1, x), (1, y)))


def compile_contract(spec):
    fields = spec["fields"]
    actions = spec["actions"]
    revisions = spec["revisions"]
    require(bool(revisions) and all(str(int(key)) == key and int(key) > 0 for key in revisions),
            "revision IDs must be canonical positive integers")
    for name, bounds in fields.items():
        require(name.isidentifier() and name not in {"role", "revision", "policy"},
                f"reserved field {name}")
        low, high, initial = bounds
        require(all(type(n) is int for n in bounds) and -4096 <= low <= initial <= high <= 4096,
                f"invalid field bounds for {name}")
    require(set(spec["public"]) <= fields.keys(), "unknown public field")
    require(type(spec["initial_revision"]) is int and str(spec["initial_revision"]) in revisions,
            "initial revision missing")
    require(all(type(role) is int for role in spec["actors"].values()), "invalid actor role")

    modules = {}
    for revision, policy in revisions.items():
        require(type(policy) is int and abs(policy) <= 4096, "invalid policy parameter")
        modules[revision] = {}
        for action, rule in actions.items():
            args = rule.get("args", {})
            require(not (args.keys() & (fields.keys() | {"role", "revision", "policy"})),
                    f"input shadows state in {action}")
            require(all(len(values) <= 8 and values and all(type(n) is int and abs(n) <= 4096
                                                           for n in values) for values in args.values()),
                    "only bounded integer inputs are supported")
            require(prod(map(len, args.values())) <= 512, "argument surface exceeds fixture budget")
            require(all(len(set(values)) == len(values) for values in args.values()),
                    "duplicate argument options")
            require(isinstance(rule["label"], str) and bool(rule["label"]), "missing action label")
            successor = rule.get("successor")
            if successor is not None:
                require(successor in actions and not actions[successor].get("args"),
                        "projection successor must be a zero-argument action")
            pin = rule.get("pin")
            require(pin is None or pin in fields, f"unknown revision pin in {action}")
            booleans = {name for name, (low, high, _) in fields.items() if 0 <= low <= high <= 1}
            booleans.update(name for name, values in args.items() if set(values) <= {0, 1})
            graph = Graph(fields.keys() | args.keys() | {"role", "revision", "policy"}, booleans)
            require(set(rule["writes"]) <= fields.keys(), f"unknown field in {action}")
            guard = graph.expression(rule["guard"])
            require(guard in graph.booleans, f"guard for {action} is not Boolean")
            writes = {name: graph.expression(expr) for name, expr in rule["writes"].items()}
            effects = [[name, graph.expression(expr)] for name, expr in rule["effects"]]
            modules[revision][action] = {
                "nodes": graph.nodes, "guard": guard, "writes": writes, "effects": effects,
            }

    return {
        "format": "holodeck.integer-forward/1",
        "source": digest(spec),
        "fields": fields,
        "public": spec["public"],
        "actors": spec["actors"],
        "initial_revision": spec["initial_revision"],
        "policies": revisions,
        "actions": {
            name: {key: rule[key] for key in ("args", "pin", "label", "successor") if key in rule}
            for name, rule in actions.items()
        },
        "modules": modules,
    }


def integer_forward(module, inputs):
    values = []
    for operation, *args in module["nodes"]:
        if operation == "input":
            result = inputs[args[0]]
        elif operation == "constant":
            result = args[0]
        elif operation == "affine":
            bias, terms = args
            result = bias + sum(weight * values[index] for weight, index in terms)
        elif operation == "threshold":
            result = int(values[args[0]] >= 0)
        else:
            raise ValueError(f"unknown forward operator {operation}")
        values.append(result)

    if values[module["guard"]] != 1:
        return None
    return {
        "writes": {name: values[index] for name, index in module["writes"].items()},
        "effects": [[name, values[index]] for name, index in module["effects"]],
    }


class Model:
    def __init__(self, checkpoint):
        require(checkpoint["format"] == "holodeck.integer-forward/1", "unsupported checkpoint")
        self.checkpoint = checkpoint

    def propose(self, state, action, actor, args):
        contract = self.checkpoint
        rule = contract["actions"].get(action)
        role = contract["actors"].get(actor)
        if rule is None or role is None:
            return None
        arguments = rule.get("args", {})
        if args.keys() != arguments.keys() or any(
            type(value) is not int or value not in arguments[key] for key, value in args.items()
        ):
            return None
        fields = state["fields"]
        pin = rule.get("pin")
        revision = str(fields[pin] if pin and fields[pin] else state["revision"])
        policy = contract["policies"].get(revision)
        if policy is None:
            raise ValueError("unknown policy revision")
        result = integer_forward(contract["modules"][revision][action], {
            **fields, **args, "revision": int(revision), "policy": policy, "role": role,
        })
        if result is not None:
            for name, value in result["writes"].items():
                minimum, maximum, _ = contract["fields"][name]
                require(minimum <= value <= maximum, f"state write outside contract: {name}")
        return result
