"""TodoMVC as a Deck with entities: a declarative definition and a small exact machine that runs it.

The definition below was written from the published TodoMVC specification. It is checked against
the original application in a real browser, action by action. State is one scalar and one
collection of records with identity, so this is the first Deck that is not a fixed vector of
bounded integers. The durable Host is reused unchanged.
"""

import hashlib
import json

DEFINITION = {
    "name": "todomvc",
    "scalars": {"filter": {"values": ["all", "active", "completed"], "initial": "all"},
                # Found by replaying the original: its "mark all" box is a flip-flop that single items never update.
                "all_box": {"values": [False, True], "initial": False}},
    "collections": {"todos": {"title": "text", "completed": "flag"}},
    # Rows a person can point at. Row parameters are positions in this view.
    "views": {"visible": {"of": "todos", "where": ["or", ["eq", "@filter", "all"],
                                                   ["eq", ["eq", "@filter", "completed"], ".completed"]]}},
    "actions": {
        "add": {
            "params": {"text": "text"}, "let": {"title": ["trim", "$text"]},
            "guard": ["ne", "$title", ""],
            "do": [["insert", "todos", {"title": "$title", "completed": False}]]},
        "toggle": {
            "params": {"row": "visible"},
            "do": [["update", "todos", ["is", "$row"], {"completed": ["not", ".completed"]}]]},
        "destroy": {
            "params": {"row": "visible"},
            "do": [["delete", "todos", ["is", "$row"]]]},
        "edit": {
            "params": {"row": "visible", "text": "text"}, "let": {"title": ["trim", "$text"]},
            "do": [["when", ["eq", "$title", ""],
                    [["delete", "todos", ["is", "$row"]]],
                    [["update", "todos", ["is", "$row"], {"title": "$title"}]]]]},
        "toggle_all": {
            "guard": ["gt", ["count", "todos", True], 0],
            "let": {"target": ["not", "@all_box"]},
            "do": [["set", "all_box", "$target"], ["update", "todos", True, {"completed": "$target"}]]},
        "clear_completed": {
            "guard": ["gt", ["count", "todos", ".completed"], 0],
            "do": [["delete", "todos", ".completed"]]},
        "show": {
            "params": {"filter": "filter"},
            "do": [["set", "filter", "$filter"]]},
    },
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def trim(text):
    """JavaScript String.prototype.trim: white space and line terminators of ECMAScript."""
    space = " \t\n\v\f\r                 　﻿"
    return text.strip(space)


class Deck:
    """Exact interpreter for a definition. Every action returns a whole new state or None."""

    def __init__(self, definition=DEFINITION):
        self.definition = definition
        self.checkpoint = {
            "definition": definition, "initial_revision": 1, "policies": {"1": 0}, "actors": {"user": 0},
            "fields": {**{name: [None, None, scalar["initial"]] for name, scalar in definition["scalars"].items()},
                       **{name: [None, None, []] for name in definition["collections"]},
                       "next": [None, None, 1]}}

    def initial(self):
        return {name: json.loads(json.dumps(bounds[2])) for name, bounds in self.checkpoint["fields"].items()}

    def value(self, expression, state, env, record=None):
        if isinstance(expression, str):
            if expression.startswith("$"):
                return env[expression[1:]]
            if expression.startswith("@"):
                return state[expression[1:]]
            if expression.startswith(".") and record is not None:
                return record[expression[1:]]
            return expression
        if not isinstance(expression, list):
            return expression
        op, *rest = expression
        if op == "is":
            return record["id"] == self.value(rest[0], state, env, record)
        if op == "count":
            return sum(bool(self.value(rest[1], state, env, row)) for row in state[rest[0]])
        values = [self.value(item, state, env, record) for item in rest]
        if op == "trim":
            return trim(values[0])
        if op == "not":
            return not values[0]
        return {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "gt": lambda a, b: a > b,
                "and": lambda a, b: bool(a and b), "or": lambda a, b: bool(a or b)}[op](*values)

    def view(self, state, name="visible"):
        spec = self.definition["views"][name]
        return [row for row in state[spec["of"]] if self.value(spec["where"], state, {}, row)]

    def bind(self, state, action, args):
        """Typed arguments. A row is a position in the visible list and resolves to an identity."""
        rule, env = self.definition["actions"][action], {}
        if set(args) != set(rule.get("params", {})):
            return None
        for name, kind in rule.get("params", {}).items():
            value = args[name]
            if kind == "text":
                if not isinstance(value, str):
                    return None
            elif kind in self.definition["views"]:
                rows = self.view(state, kind)
                if type(value) is not int or not 0 <= value < len(rows):
                    return None
                value = rows[value]["id"]
            elif value not in self.definition["scalars"][kind]["values"]:
                return None
            env[name] = value
        for name, expression in rule.get("let", {}).items():
            env[name] = self.value(expression, state, env)
        return env

    def run(self, state, statements, env):
        for statement in statements:
            op = statement[0]
            if op == "insert":
                record = {key: self.value(expr, state, env) for key, expr in statement[2].items()}
                state[statement[1]].append({"id": state["next"], **record})
                state["next"] += 1
            elif op == "delete":
                state[statement[1]] = [row for row in state[statement[1]]
                                       if not self.value(statement[2], state, env, row)]
            elif op == "update":
                for row in state[statement[1]]:
                    if self.value(statement[2], state, env, row):
                        row.update({key: self.value(expr, state, env, row) for key, expr in statement[3].items()})
            elif op == "set":
                state[statement[1]] = self.value(statement[2], state, env)
            elif op == "when":
                self.run(state, statement[2] if self.value(statement[1], state, env) else statement[3], env)

    def step(self, state, action, args):
        """The state after one action, or None when the action is not available."""
        if action not in self.definition["actions"]:
            return None
        env = self.bind(state, action, args)
        rule = self.definition["actions"][action]
        if env is None or ("guard" in rule and not self.value(rule["guard"], state, env)):
            return None
        after = json.loads(json.dumps(state))
        self.run(after, rule["do"], env)
        return after

    def propose(self, snapshot, action, actor, args):
        """The interface the durable Host expects from a compiled model."""
        if actor not in self.checkpoint["actors"]:
            return None
        after = self.step(snapshot["fields"], action, args)
        if after is None:
            return None
        return {"writes": {key: value for key, value in after.items() if snapshot["fields"][key] != value},
                "effects": []}

    def observe(self, state):
        """Everything the original application shows a person."""
        todos = state["todos"]
        left = sum(not row["completed"] for row in todos)
        return {"items": [[row["title"], row["completed"]] for row in self.view(state)],
                "chrome": bool(todos), "count": f"{left} item{'' if left == 1 else 's'} left",
                "clear": any(row["completed"] for row in todos),
                "all_checked": state["all_box"], "filter": state["filter"]}
