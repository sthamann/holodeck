"""Generate bounded application contracts, English requests and exhaustive shortest-plan labels."""

from collections import deque
from itertools import product
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "model-applications" / "vertical"))

from compiler import Model, compile_contract  # noqa: E402

WORDS = {
    "train": {
        "fields": ("stock quota budget credits tokens seats slots units hours points badges tickets drafts "
                   "copies parcels crates pallets batches orders claims refunds invoices samples alerts flags "
                   "locks holds leases grants passes keys tags notes votes bids offers shares coins stamps "
                   "vouchers coupons lanes docks bays shelves bins trays racks carts jobs tasks runs").split(),
        "actions": ("approve submit cancel release reserve settle archive publish revoke grant assign transfer "
                    "refill drain open close seal stamp merge split promote demote escalate dismiss").split(),
        "actors": "clerk manager agent auditor operator owner".split(),
    },
    "test": {
        "fields": ("backlog ledger margin ballast cargo freight permits licences warrants pledges deposits "
                   "rations doses vials probes sensors beacons signals queues shifts rosters valves gates "
                   "meters panels cells rails hooks reels").split(),
        "actions": ("ratify dispatch withdraw audit certify suspend resume rebalance allocate recall forward "
                    "retire inspect restock unlock").split(),
        "actors": "steward warden broker registrar".split(),
    },
}

# Request phrasings. The last entry of every list is reserved for the test split.
ATOMS = {
    "eq": ["{f} equals {v}", "{f} is exactly {v}", "get {f} to {v}", "{f} ends up at {v}"],
    "ge": ["{f} is at least {v}", "{f} reaches {v} or more", "there are {v} or more {f}", "{f} is no lower than {v}"],
    "le": ["{f} is at most {v}", "{f} drops to {v} or below", "there are {v} or fewer {f}", "{f} is no higher than {v}"],
    "on": ["{f} is on", "{f} is active", "{f} is switched on", "{f} is enabled"],
    "off": ["{f} is off", "{f} is inactive", "{f} is switched off", "{f} is disabled"],
}
FRAMES = ["Please make sure {a}.", "I need a state where {a}.", "Goal: {a}.", "Arrange things so that {a}.",
          "Could you see to it that {a}?"]
JOINS = ["{a} and {b}", "{a}, and also {b}", "{a} while {b}"]

MAX_FIELDS, MAX_CANDIDATES, MAX_DEPTH = 8, 16, 4
COMPARATORS = ("eq", "ge", "le")


def evaluate(expression, env):
    if type(expression) is int:
        return expression
    if isinstance(expression, str):
        return env[expression]
    op, *operands = expression
    x = evaluate(operands[0], env)
    if op == "not":
        return 1 - x
    y = evaluate(operands[1], env)
    return {"add": x + y, "sub": x - y, "le": int(x <= y), "eq": int(x == y),
            "and": x & y, "or": x | y}[op]


def interpret(spec, fields, action, actor, args, revision=1):
    """Reference semantics read straight from the definition. The compiled graph is never consulted."""
    rule = spec["actions"][action]
    pin = rule.get("pin")
    revision = fields[pin] if pin and fields[pin] else revision
    env = {**fields, **args, "role": spec["actors"][actor], "revision": revision,
           "policy": spec["revisions"][str(revision)]}
    if evaluate(rule["guard"], env) != 1:
        return None
    return {"writes": {name: evaluate(expr, env) for name, expr in rule["writes"].items()},
            "effects": [[name, evaluate(expr, env)] for name, expr in rule["effects"]]}


def conjunction(atoms):
    if len(atoms) == 1:
        return atoms[0]
    middle = len(atoms) // 2
    return ["and", conjunction(atoms[:middle]), conjunction(atoms[middle:])]


def footprint(expression):
    if isinstance(expression, str):
        return {expression}
    if isinstance(expression, list):
        return set().union(*(footprint(item) for item in expression[1:]))
    return set()


def draw_contract(rng, split, fields=(3, 5), actions=(3, 5)):
    words = WORDS[split]
    while True:
        names = rng.sample(words["fields"], rng.randint(*fields))
        bounds = {}
        for name in names:
            high = 1 if rng.random() < 0.4 else rng.choice((2, 3, 3, 4, 4, 6, 9))
            bounds[name] = [0, high, rng.randint(0, high)]
        size = 1
        for _, high, _ in bounds.values():
            size *= high + 1
        if size <= 2500 and any(high > 1 for _, high, _ in bounds.values()):
            break
    flags = [name for name in names if bounds[name][1] == 1]
    counts = [name for name in names if bounds[name][1] > 1]
    actors = rng.sample(words["actors"], 2)
    spec = {"initial_revision": 1, "revisions": {"1": rng.randint(0, 2)}, "fields": bounds,
            "public": names, "actors": {actors[0]: 0, actors[1]: 1}, "actions": {}}
    emitted = rng.sample(words["fields"], 4)
    for label in rng.sample(words["actions"], rng.randint(*actions)):
        guard = [["eq", "role", 0 if rng.random() < 0.75 else 1]]
        writes, args = {}, {}
        kind = rng.choice(("flag", "up", "up", "down", "down", "set", "move") if flags else
                          ("up", "up", "down", "down", "set", "move"))
        if kind == "move" and len(counts) < 2:
            kind = "up"
        if kind == "flag":
            target = rng.choice(flags)
            value = rng.randint(0, 1)
            guard.append(["not", target] if value else target)
            writes[target] = value
        elif kind == "set":
            target = rng.choice(counts)
            writes[target] = rng.randint(0, bounds[target][1])
        else:
            target = rng.choice(counts)
            high = bounds[target][1]
            step = 1
            if rng.random() < 0.35:
                args["amount"] = list(range(1, min(high, 3) + 1))[:rng.choice((2, 3))]
                if len(args["amount"]) < 2:
                    args = {}
                else:
                    step = "amount"
            if kind in ("down", "move"):
                guard.append(["le", step, target])
                writes[target] = ["sub", target, step]
            if kind == "up":
                guard.append(["le", ["add", target, step], high])
                writes[target] = ["add", target, step]
            if kind == "move":
                other = rng.choice([name for name in counts if name != target])
                guard.append(["le", ["add", other, step], bounds[other][1]])
                writes[other] = ["add", other, step]
        for _ in range(rng.choice((0, 0, 1, 1, 2))):
            name = rng.choice(names)
            high = bounds[name][1]
            if high == 1:
                guard.append(rng.choice((name, ["not", name])))
            else:
                limit = rng.randint(1, high - 1) if high > 1 else 1
                guard.append(rng.choice((["le", limit, name], ["le", name, limit], ["le", "policy", name])))
        effects = [[rng.choice(emitted), rng.choice([target, 1])]] if rng.random() < 0.5 else []
        spec["actions"][label] = {"label": label.capitalize(), "guard": conjunction(guard),
                                  "writes": writes, "effects": effects, **({"args": args} if args else {})}
    return spec


class World:
    """One contract with its compiled Deck, grounded candidates and exhaustive transition tables."""

    def __init__(self, spec):
        self.spec = spec
        self.names = list(spec["fields"])
        self.bounds = [spec["fields"][name] for name in self.names]
        self.checkpoint = compile_contract(spec)
        self.model = Model(self.checkpoint)
        self.candidates = []
        for action, rule in spec["actions"].items():
            arguments = sorted(rule.get("args", {}).items())
            for values in product(*(allowed for _, allowed in arguments)):
                self.candidates.append((action, dict(zip((key for key, _ in arguments), values))))
        self.states = list(product(*(range(low, high + 1) for low, high, _ in self.bounds)))
        self.index = {state: number for number, state in enumerate(self.states)}
        self.tables, self.distances = {}, {}

    def usable(self):
        return len(self.names) <= MAX_FIELDS and 2 <= len(self.candidates) <= MAX_CANDIDATES

    def fields(self, state):
        return dict(zip(self.names, state))

    def step(self, state, candidate, actor):
        action, args = self.candidates[candidate]
        result = interpret(self.spec, self.fields(state), action, actor, args)
        if result is None:
            return None
        return tuple(result["writes"].get(name, value) for name, value in zip(self.names, state))

    def table(self, actor):
        if actor not in self.tables:
            self.tables[actor] = [[-1 if (after := self.step(state, candidate, actor)) is None
                                   else self.index.get(after, -1) for candidate in range(len(self.candidates))]
                                  for state in self.states]
        return self.tables[actor]

    def satisfied(self, state, goal):
        return all({"eq": value == target, "ge": value >= target, "le": value <= target}[comparator]
                   for field, comparator, target in goal for value in (state[field],))

    def distance(self, actor, goal):
        """Shortest number of steps from every state to the goal for one acting role."""
        key = (actor, tuple(map(tuple, goal)))
        if key not in self.distances:
            table = self.table(actor)
            parents = [[] for _ in self.states]
            for source, row in enumerate(table):
                for target in row:
                    if target >= 0:
                        parents[target].append(source)
            distance = [None] * len(self.states)
            queue = deque()
            for number, state in enumerate(self.states):
                if self.satisfied(state, goal):
                    distance[number] = 0
                    queue.append(number)
            while queue:
                current = queue.popleft()
                for parent in parents[current]:
                    if distance[parent] is None:
                        distance[parent] = distance[current] + 1
                        queue.append(parent)
            self.distances[key] = distance
        return self.distances[key]

    def reachable(self):
        """States the Host can actually be in, starting from the declared initial values."""
        start = self.index[tuple(initial for _, _, initial in self.bounds)]
        seen, queue = {start}, deque([start])
        while queue:
            current = queue.popleft()
            for actor in self.spec["actors"]:
                for target in self.table(actor)[current]:
                    if target >= 0 and target not in seen:
                        seen.add(target)
                        queue.append(target)
        return seen

    def legal(self, state, actor):
        return [target >= 0 for target in self.table(actor)[self.index[tuple(state)]]]

    def optimal(self, state, actor, goal):
        """Candidates that start a shortest plan. Empty when the goal holds or cannot be reached."""
        distance = self.distance(actor, goal)
        here = distance[self.index[tuple(state)]]
        if not here:
            return []
        return [candidate for candidate, target in enumerate(self.table(actor)[self.index[tuple(state)]])
                if target >= 0 and distance[target] == here - 1]

    def plan(self, state, actor, goal):
        """One shortest sequence of candidates to the goal. Empty when it holds, None when it is out of reach."""
        distance, table = self.distance(actor, goal), self.table(actor)
        current = self.index[tuple(state)]
        if distance[current] is None:
            return None
        steps = []
        while distance[current]:
            candidate = next(index for index, target in enumerate(table[current])
                             if target >= 0 and distance[target] == distance[current] - 1)
            steps.append(candidate)
            current = table[current][candidate]
        return steps


def phrase(rng, world, goal, split):
    def pick(options):
        return options[-1] if split == "test" else rng.choice(options[:-1])

    parts = []
    for field, comparator, value in goal:
        kind = comparator
        if world.bounds[field][1] == 1:
            kind = "on" if value else "off"
        parts.append(pick(ATOMS[kind]).format(f=world.names[field], v=value))
    text = parts[0] if len(parts) == 1 else pick(JOINS).format(a=parts[0], b=parts[1])
    return pick(FRAMES).format(a=text)


def draw_goal(rng, world):
    goal = []
    for field in rng.sample(range(len(world.names)), 1 if rng.random() < 0.7 else min(2, len(world.names))):
        high = world.bounds[field][1]
        if high == 1:
            goal.append((field, "eq", rng.randint(0, 1)))
        else:
            comparator = rng.choice(COMPARATORS)
            low, top = {"eq": (0, high), "ge": (1, high), "le": (0, high - 1)}[comparator]
            goal.append((field, comparator, rng.randint(low, top)))
    return goal


def draw_tasks(rng, world, split, per_world=4, within=None):
    """Sample start states stratified by the length of the shortest plan, including unreachable goals."""
    tasks, wanted = [], [1, 2, 3, 4, 0, 1, 2, 3, 4]
    rng.shuffle(wanted)
    for _ in range(24):
        if len(tasks) == per_world:
            break
        actor = rng.choice(list(world.spec["actors"]))
        goal = draw_goal(rng, world)
        distance = world.distance(actor, goal)
        depth = wanted[len(tasks)]
        pool = [number for number, value in enumerate(distance)
                if (value is None if depth == 0 else value == depth)]
        if within is not None:
            pool = [number for number in pool if number in within]
        # Keep states where legality alone does not reveal the answer.
        table = world.table(actor)
        hard = [number for number in pool if sum(target >= 0 for target in table[number]) >= 3 and (
            depth == 0 or any(target >= 0 and distance[target] != depth - 1 for target in table[number]))]
        pool = hard or ([] if within is None else [
            number for number in pool if sum(target >= 0 for target in table[number]) >= 2])
        if not pool:
            continue
        state = world.states[rng.choice(pool)]
        tasks.append({"actor": actor, "state": list(state), "goal": [list(atom) for atom in goal],
                      "request": phrase(rng, world, goal, split), "depth": depth,
                      "optimal": world.optimal(state, actor, goal), "legal": world.legal(state, actor)})
    return tasks


def build(seed, count, split, shape="train"):
    """Deterministic cohort of worlds with tasks. `shape` selects the structural size family."""
    rng = random.Random(f"{seed}:{split}:{shape}")
    sizes = {"train": ((3, 5), (3, 5)), "larger": ((6, 7), (6, 7))}[shape]
    cohort = []
    while len(cohort) < count:
        world = World(draw_contract(rng, split, *sizes))
        if not world.usable():
            continue
        tasks = draw_tasks(rng, world, split)
        if len(tasks) >= 2:
            cohort.append((world, tasks))
    return cohort


def authored():
    """The two vertical contracts, written before this generator existed."""
    contracts = json.loads((ROOT.parent / "model-applications" / "vertical" / "contracts.json").read_text())
    rng = random.Random("authored")
    cohort = []
    for name in sorted(contracts):
        world = World(contracts[name])
        tasks = []
        for _ in range(12):
            tasks.extend(draw_tasks(rng, world, "test", per_world=4, within=world.reachable()))
        cohort.append((world, tasks))
    return cohort


def render(expression):
    if not isinstance(expression, list):
        return str(expression)
    op, *operands = expression
    if op == "not":
        return f"not {render(operands[0])}"
    sign = {"add": "+", "sub": "-", "le": "<=", "eq": "==", "and": "and", "or": "or"}[op]
    left, right = map(render, operands)
    return f"({left} {sign} {right})" if op in ("add", "sub") else f"{left} {sign} {right}"


def letter(index):
    return "ABCDEFGHIJKLMNOP"[index]


def describe(world, task, previews=False, request_first=False):
    """The same application, state and request as plain text for the matched language arms."""
    state = world.fields(task["state"])
    spec = world.spec
    lines = ["Application state:"]
    lines += [f"- {name} = {state[name]} (range {low}..{high})" for name, (low, high, _) in spec["fields"].items()]
    lines.append(f"Policy value: {spec['revisions']['1']}. You act as {task['actor']} "
                 f"(role {spec['actors'][task['actor']]}).")
    lines.append("Rules:")
    for action, rule in spec["actions"].items():
        writes = ", ".join(f"{name} := {render(expr)}" for name, expr in rule["writes"].items())
        lines.append(f"- {action}: requires {render(rule['guard'])}; sets {writes}")
    if request_first:
        lines.append(f"Request: {task['request']}")
    lines.append("Options:")
    for number, (action, args) in enumerate(world.candidates):
        text = f"{letter(number)}. {action}" + "".join(f" {key}={value}" for key, value in args.items())
        if previews:
            after = world.step(tuple(task["state"]), number, task["actor"])
            changes = [] if after is None else [
                f"{name}={value}" for name, value, before in zip(world.names, after, task["state"])
                if value != before]
            text += " -> " + ("refused" if after is None else ", ".join(changes) or "no change")
        lines.append(text)
    lines.append("Z. nothing can achieve the request")
    if not request_first:
        lines.append(f"Request: {task['request']}")
    lines.append("Which option starts the shortest way to fulfil the request? Answer:")
    return "\n".join(lines)


SIGN = {"eq": "==", "ge": ">=", "le": "<="}
NAME = {sign: name for name, sign in SIGN.items()}
ATOM = re.compile(r"^(\S+) (==|>=|<=) (\d+)$")


def goal_text(world, goal):
    return " and ".join(f"{world.names[field]} {SIGN[comparator]} {value}" for field, comparator, value in goal)


def parse_goal(world, text):
    """Typed goal from the model's text, or None when it does not name this contract's fields."""
    goal = []
    for part in text.strip().split(" and "):
        match = ATOM.match(part.strip())
        if not match or match[1] not in world.names:
            return None
        goal.append((world.names.index(match[1]), NAME[match[2]], int(match[3])))
    return goal if 1 <= len(goal) <= 2 else None


def verdict(world, task, goal, choice):
    """Does exact search under the stated goal agree with the option the model chose?"""
    if goal is None:
        return False
    plans = world.optimal(task["state"], task["actor"], goal)
    return choice in plans if plans else choice == len(world.candidates)
