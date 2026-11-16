"""Ground a bounded, conjunctive STRIPS fragment without domain-specific rules."""

from dataclasses import dataclass
from itertools import product
import re


def require(condition, message):
    if not condition:
        raise ValueError(message)


def expression(source):
    stack = []
    roots = []
    for token in re.findall(r"[^\s()]+|[()]", re.sub(r";[^\n]*", "", source).lower()):
        if token == "(":
            item = []
            (stack[-1] if stack else roots).append(item)
            stack.append(item)
        elif token == ")":
            require(bool(stack), "unmatched closing parenthesis")
            stack.pop()
        else:
            require(bool(stack), "token outside s-expression")
            stack[-1].append(token)
    require(not stack and len(roots) == 1, "expected exactly one complete s-expression")
    return roots[0]


def named_types(items):
    pending, result = [], {}
    iterator = iter(items)
    for token in iterator:
        require(isinstance(token, str), "union types are outside the supported fragment")
        if token == "-":
            sort = next(iterator, None)
            require(bool(pending) and isinstance(sort, str) and sort != "-", "invalid typed list")
            for name in pending:
                require(name not in result, f"duplicate identifier: {name}")
                result[name] = sort
            pending.clear()
        else:
            pending.append(token)
    for name in pending:
        require(name not in result, f"duplicate identifier: {name}")
        result[name] = "object"
    return result


def literals(term):
    require(isinstance(term, list) and term, "expected literal or conjunction")
    if term[0] == "and":
        return tuple(literal for child in term[1:] for literal in literals(child))
    positive = term[0] != "not"
    atom = term if positive else term[1] if len(term) == 2 else ()
    require(bool(atom) and isinstance(atom, list) and all(isinstance(x, str) for x in atom),
            "unsupported non-literal formula")
    require(atom[0] not in {"and", "or", "not", "imply", "forall", "exists", "when", "increase", "decrease"},
            "unsupported formula or effect")
    return ((positive, tuple(atom)),)


def sections(source, kind, permitted):
    head, declaration, *sections = expression(source)
    require(head == "define" and len(declaration) == 2 and declaration[0] == kind,
            f"expected {kind} declaration")
    entries = {}
    for name, *values in sections:
        require(name in permitted, f"unsupported {kind} section {name}")
        if name == ":action":
            entries.setdefault(name, []).append(values)
        else:
            require(name not in entries, f"duplicate section {name}")
            entries[name] = values
    return declaration[1], entries


@dataclass(frozen=True)
class Operator:
    signature: tuple
    yes: frozenset
    no: frozenset
    adds: frozenset
    deletes: frozenset


@dataclass
class Task:
    objects: tuple
    initial: frozenset
    goal_yes: frozenset
    goal_no: frozenset
    operators: tuple
    reference: dict
    static_initial: frozenset
    immutable_goal_failure: bool


def split(lits):
    yes, no = set(), set()
    for sign, atom in lits:
        (yes if sign else no).add(atom)
    return frozenset(yes), frozenset(no)


def parse(domain_source, problem_source):
    domain, d = sections(domain_source, "domain", {
        ":requirements", ":types", ":constants", ":predicates", ":action",
    })
    _, p = sections(problem_source, "problem", {":domain", ":objects", ":init", ":goal"})
    require(p[":domain"] == [domain], "problem names another domain")
    require(set(d.get(":requirements", [])) <= {":strips", ":typing", ":negative-preconditions", ":equality"},
            "unsupported PDDL requirement")
    sorts = named_types(d.get(":types", []))
    require("object" not in sorts, "object is reserved as the root type")

    def ancestry(sort):
        visited = set()
        while sort != "object":
            require(sort not in visited and sort in sorts, f"invalid or cyclic type {sort}")
            visited.add(sort)
            sort = sorts[sort]
        return {*visited, "object"}

    for sort in sorts:
        ancestry(sort)
    const = named_types(d.get(":constants", []))
    objects = named_types(p.get(":objects", []))
    require(not (const.keys() & objects.keys()), "duplicate constant/object")
    objects = {**const, **objects}
    object_types = {name: ancestry(sort) for name, sort in objects.items()}
    predicates = {}
    for name, *arguments in d[":predicates"]:
        require(name not in predicates and name != "=", "duplicate or reserved predicate")
        predicates[name] = tuple(named_types(arguments).values())
        for sort in predicates[name]:
            ancestry(sort)
    initial, negatives = split(literal for term in p[":init"] for literal in literals(term))
    require(len(p[":goal"]) == 1 and not (initial & negatives), "malformed goal or initial contradiction")
    goal, goal_neg = split(literals(p[":goal"][0]))

    def validate(atom, bound_types, equality=False):
        name, *args = atom
        available = predicates.get(name)
        if name == "=" and equality:
            available = ("object", "object")
        require(available is not None and len(args) == len(available), f"unknown predicate or arity: {atom}")
        for arg, expected in zip(args, available, strict=True):
            require(arg in bound_types and expected in bound_types[arg], f"unbound or ill-typed token {arg}")

    for atom in initial | negatives:
        validate(atom, object_types)
    for atom in goal | goal_neg:
        validate(atom, object_types, equality=True)
    schemas = {}
    changed = set()
    for action, *definition in d.get(":action", []):
        require(len(definition) % 2 == 0 and action not in schemas, "malformed or duplicate action")
        parts = dict(zip(definition[::2], definition[1::2], strict=True))
        require(len(definition) == 6 and set(parts) == {":parameters", ":precondition", ":effect"},
                f"unsupported action definition for {action}")
        params = named_types(parts[":parameters"])
        require(all(name.startswith("?") for name in params), "parameter must be a variable")
        types = {**object_types, **{name: ancestry(sort) for name, sort in params.items()}}
        conditions = literals(parts[":precondition"])
        effects = literals(parts[":effect"])
        for _, atom in conditions:
            validate(atom, types, equality=True)
        for _, atom in effects:
            validate(atom, types)
            changed.add(atom[0])
        schemas[action] = (params, conditions, effects)

    static = frozenset(atom for atom in initial if atom[0] not in changed)

    def holds(atom):
        return atom[1] == atom[2] if atom[0] == "=" else atom in static

    ground = []
    for name, (params, conditions, effects) in sorted(schemas.items()):
        fixed = [(sign, atom) for sign, atom in conditions if atom[0] not in changed]
        runtime = [(sign, atom) for sign, atom in conditions if atom[0] in changed]
        choices = [[obj for obj, types in sorted(object_types.items()) if sort in types and all(
            holds((atom[0], obj)) == sign for sign, atom in fixed if len(atom) == 2 and atom[1] == variable
        )] for variable, sort in params.items()]
        for arguments in product(*choices):
            bindings = dict(zip(params, arguments, strict=True))

            def subst(atom):
                return (atom[0], *(bindings.get(arg, arg) for arg in atom[1:]))

            if not all(holds(subst(atom)) == sign for sign, atom in fixed):
                continue
            yes, no = split((sign, subst(atom)) for sign, atom in runtime)
            adds, deletes = split((sign, subst(atom)) for sign, atom in effects)
            if not yes & no:
                ground.append(Operator((name, *arguments), yes, no, adds, deletes - adds))
    immutable_failure = any(sign != holds(atom) for sign, atoms in ((True, goal), (False, goal_neg))
                            for atom in atoms if atom[0] not in changed)
    dynamic = lambda atoms: frozenset(atom for atom in atoms if atom[0] in changed)
    return Task(tuple(sorted(objects)), dynamic(initial), dynamic(goal), dynamic(goal_neg), tuple(ground),
                schemas, static, immutable_failure)


def interpret(task, state, signature):
    """Interpret schema literals on sets, independently of grounded bit transitions."""
    action, *arguments = signature
    params, conditions, effects = task.reference[action]
    bindings = dict(zip(params, arguments, strict=True))
    visible = state | task.static_initial

    def subst(term):
        predicate, *args = term
        return (predicate, *(bindings.get(value, value) for value in args))

    for positive, item in conditions:
        term = subst(item)
        actual = term[1] == term[2] if term[0] == "=" else term in visible
        if positive != actual:
            return None
    adds = {subst(atom) for positive, atom in effects if positive}
    clears = {subst(atom) for positive, atom in effects if not positive}
    return frozenset((state - clears) | adds)
