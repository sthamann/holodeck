"""Scene tokens for generated Holos. A control token exists only for an authorized handle."""

from worlds import footprint

STRUCT = ["END", "CARD", "ROW", "CLOSE", "PRIMARY", "NOTICE"]
END, CARD, ROW, CLOSE, PRIMARY, NOTICE = (("s", index) for index in range(len(STRUCT)))
MAX_TOKENS = 20


def allowed(prefix, facts, controls):
    """Every token that keeps the scene well formed and bound to currently authorized handles.

    `facts` are the fields this actor may see and `controls` the candidates the Deck enables now.
    The decoder can only choose from the returned set, so no scene can carry a foreign control.
    """
    inside, filled, pending, closed, primary = False, False, False, 0, False
    used, shown = set(), set()
    for kind, value in prefix:
        token = (kind, value)
        if token in (CARD, ROW):
            inside, filled, shown = True, False, set()
        elif token == CLOSE:
            inside, closed = False, closed + 1
        elif token == PRIMARY:
            pending = primary = True
        elif kind == "c":
            used.add(value)
            pending, filled = False, True
        elif kind == "f":
            shown.add(value)
            filled = True
    free = [("c", control) for control in sorted(set(controls) - used)]
    room = MAX_TOKENS - len(prefix)
    if pending:
        return free
    if not inside:
        # Opening a container needs room for one item, CLOSE and END.
        options = [END] if closed else []
        if room >= 4:
            options += [CARD, ROW]
        if not prefix:
            options.append(NOTICE)
        return options
    if room <= 2:
        return [CLOSE]
    options = [("f", fact) for fact in sorted(set(facts) - shown)] + free
    if free and not primary and room >= 4:
        options.append(PRIMARY)
    if filled:
        options.append(CLOSE)
    return options


def wellformed(tokens, facts, controls):
    return bool(tokens) and tokens[-1] == END and all(
        tuple(token) in allowed(tokens[:index], facts, controls) for index, token in enumerate(tokens))


def reference(world, task):
    """Scene derived from exact semantics: the step that starts a shortest plan, then what it depends on."""
    best = task["optimal"][:1]
    goal_fields = [field for field, _, _ in task["goal"]]
    tokens = [] if best else [NOTICE]
    tokens.append(CARD)
    facts = list(dict.fromkeys(goal_fields))
    if best:
        tokens += [PRIMARY, ("c", best[0])]
        guard = footprint(world.spec["actions"][world.candidates[best[0]][0]]["guard"])
        facts += [index for index, name in enumerate(world.names) if name in guard and index not in facts]
    tokens += [("f", field) for field in facts[:3]] + [CLOSE]
    distance = world.distance(task["actor"], task["goal"])
    row = world.table(task["actor"])[world.index[tuple(task["state"])]]
    others = sorted((99 if distance[row[c]] is None else distance[row[c]], c)
                    for c, legal in enumerate(task["legal"]) if legal and c not in best)
    if others:
        tokens += [ROW] + [("c", candidate) for _, candidate in others[:4]] + [CLOSE]
    return tokens + [END]


def unclear(task):
    """Scene for a request that names no goal: say so, then offer what is possible now."""
    legal = [index for index, ok in enumerate(task["legal"]) if ok]
    return [NOTICE, ROW] + [("c", index) for index in legal[:4]] + [CLOSE, END] if legal else [NOTICE, END]


def tree(tokens, world, task, epoch=0, notice="No sequence of allowed actions fulfils this request."):
    """Scene tree with revision-bound handles, ready for the renderer and for `Host.invoke`."""
    state = world.fields(task["state"])
    root, current, emphasis = [], None, False
    for kind, value in tokens:
        token = (kind, value)
        if token == NOTICE:
            root.append({"kind": "notice", "text": notice})
        elif token in (CARD, ROW):
            current = {"kind": STRUCT[value].lower(), "children": []}
            root.append(current)
        elif token == CLOSE:
            current = None
        elif token == PRIMARY:
            emphasis = True
        elif kind == "f":
            name = world.names[value]
            current["children"].append({"kind": "fact", "label": name, "value": state[name]})
        elif kind == "c":
            action, args = world.candidates[value]
            label = world.spec["actions"][action]["label"] + "".join(f" {v}" for v in args.values())
            current["children"].append({
                "kind": "control", "label": label, "primary": emphasis,
                "handle": {"actor": task["actor"], "action": action, "args": args, "epoch": epoch}})
            emphasis = False
    return root


def patches(scene, epoch=0):
    """One typed patch per top-level node, in stream order, then a completion marker."""
    for index, node in enumerate(scene):
        yield {"epoch": epoch, "put": [index, node]}
    yield {"epoch": epoch, "complete": len(scene)}


def controls(tokens):
    return [value for kind, value in tokens if kind == "c"]
