"""Intent programs: what a request asks for, stated per entity, compiled to exact actions by the Deck.

A program is a short line such as

    complete #12 #5; delete completed; add "book flights"

Every statement says what should hold afterwards. The compiler simulates the Deck and emits only
the actions that are still needed, so a program is idempotent, needs no search over states and
absorbs quirks of the application such as the flip-flop behind "mark all as complete".
"""

import re

MAX_ACTIONS = 8  # the Host's bound for one atomic plan
STATEMENT = re.compile(
    r'^(?:(add) "([^"]*)"|(complete|reopen|delete)((?: #\d+)+)|(rename) #(\d+) "([^"]*)"'
    r'|(complete|reopen) (all)|(delete) (completed)|(show) (all|active|completed))$')


def parse(text):
    """Statements of a program, or None when the text is not a program. `unclear` and `nothing` are empty programs."""
    text = text.strip()
    if text in ("unclear", "nothing"):
        return []
    statements = []
    for part in text.split("; "):
        match = STATEMENT.match(part.strip())
        if not match:
            return None
        g = match.groups()
        if g[0]:
            statements.append(("add", g[1]))
        elif g[2]:
            statements.append((g[2], [int(number) for number in g[3].replace("#", "").split()]))
        elif g[4]:
            statements.append(("rename", int(g[5]), g[6]))
        elif g[7]:
            statements.append((g[7] + " all",))
        elif g[9]:
            statements.append(("delete completed",))
        else:
            statements.append(("show", g[12]))
    return statements or None


def compile_program(deck, state, statements):
    """Deck actions that make every statement hold, and the state after them. None when one cannot be done."""
    actions, state = [], deck.step(state, "show", {"filter": state["filter"]})  # private copy

    def do(action, args):
        nonlocal state
        after = deck.step(state, action, args)
        if after is None:
            return False
        actions.append({"action": action, "args": args})
        state = after
        return True

    def row(identity):
        visible = [record["id"] for record in deck.view(state)]
        return visible.index(identity) if identity in visible else None

    for statement in statements:
        kind = statement[0]
        if kind == "add":
            if not do("add", {"text": statement[1]}):
                return None
        elif kind in ("complete", "reopen", "delete"):
            for identity in statement[1]:
                position = row(identity)
                if position is None:
                    return None
                record = deck.view(state)[position]
                if kind == "delete":
                    do("destroy", {"row": position})
                elif record["completed"] != (kind == "complete"):
                    do("toggle", {"row": position})
        elif kind == "rename":
            position = row(statement[1])
            if position is None or not statement[2].strip():
                return None
            if deck.view(state)[position]["title"] != statement[2].strip():
                do("edit", {"row": position, "text": statement[2]})
        elif kind in ("complete all", "reopen all"):
            target = kind == "complete all"
            for _ in range(2):  # the original's box is a flip-flop, so the first click can go the wrong way
                if any(record["completed"] != target for record in state["todos"]):
                    do("toggle_all", {})
        elif kind == "delete completed":
            if any(record["completed"] for record in state["todos"]):
                do("clear_completed", {})
        elif kind == "show" and state["filter"] != statement[1]:
            do("show", {"filter": statement[1]})
    return (actions, state) if len(actions) <= MAX_ACTIONS else None


def outcome(state):
    """What a request is about: the todos in order and the chosen view. Identities and the flip-flop are not."""
    return [[record["title"], record["completed"]] for record in state["todos"]], state["filter"]


def screen(deck, state, numbered=False):
    """The application as a person sees it, with a stable handle or a position on every visible row."""
    seen = deck.observe(state)
    lines = [f"Todo list · showing {seen['filter']} · {seen['count']}"
             + (" · clear completed available" if seen["clear"] else "")]
    lines += [f"{f'{index + 1}.' if numbered else '#' + str(record['id'])} [{'x' if record['completed'] else ' '}] "
              f"{record['title']}" for index, record in enumerate(deck.view(state))]
    if not deck.view(state):
        lines.append("(no todos shown)")
    return "\n".join(lines)


# Control arm: the model writes the application's own actions, with rows as positions at the time of each step.
ACTION = re.compile(r'^(?:(add) "([^"]*)"|(toggle|destroy) (\d+)|(edit) (\d+) "([^"]*)"|(toggle_all|clear_completed)'
                    r'|(show) (all|active|completed))$')


def render_actions(actions):
    parts = []
    for step in actions:
        action, args = step["action"], step["args"]
        parts.append(action + (f' {args["row"] + 1}' if "row" in args else "")
                     + (f' "{args["text"]}"' if "text" in args else "") + (f' {args["filter"]}' if "filter" in args else ""))
    return "; ".join(parts) or "nothing"


def run_actions(deck, state, text):
    """Apply written actions one by one. None when the text is not a list of actions or one is unavailable."""
    text = text.strip()
    if text in ("nothing", "unclear"):
        return [], state
    actions = []
    for part in text.split("; "):
        match = ACTION.match(part.strip())
        if not match:
            return None
        g = match.groups()
        if g[0]:
            step = {"action": "add", "args": {"text": g[1]}}
        elif g[2]:
            step = {"action": g[2], "args": {"row": int(g[3]) - 1}}
        elif g[4]:
            step = {"action": "edit", "args": {"row": int(g[5]) - 1, "text": g[6]}}
        elif g[7]:
            step = {"action": g[7], "args": {}}
        else:
            step = {"action": "show", "args": {"filter": g[9]}}
        state = deck.step(state, step["action"], step["args"])
        if state is None:
            return None
        actions.append(step)
    return (actions, state) if len(actions) <= MAX_ACTIONS else None
