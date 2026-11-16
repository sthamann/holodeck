"""Verified interchangeable-object quotients with concrete plan witnesses."""

from collections import Counter, deque
from math import factorial, prod
import operator

from pddl import Operator, require


def swap_operator(op, transposition):
    def symbols(items):
        return (items[0], *(transposition.get(value, value) for value in items[1:]))

    def facts(items):
        return frozenset(map(symbols, items))

    return Operator(symbols(op.signature), facts(op.yes), facts(op.no), facts(op.adds), facts(op.deletes))


class Kernel:
    def __init__(self, task):
        self.task = task
        data = [op.yes | op.no | op.adds | op.deletes for op in task.operators]
        self.atoms = tuple(sorted(set.union(set(task.initial | task.goal_yes | task.goal_no), *data)))
        self.index = {atom: i for i, atom in enumerate(self.atoms)}
        self.ops = [(self.mask(op.yes), self.mask(op.no), self.mask(op.adds), self.mask(op.deletes))
                    for op in task.operators]
        self.start = self.mask(task.initial)

    def mask(self, atoms):
        return sum(1 << self.index[atom] for atom in atoms)

    def facts(self, state):
        present = set()
        while state:
            bit = state & -state
            present.add(self.atoms[bit.bit_length() - 1])
            state ^= bit
        return frozenset(present)

    def next(self, state, index):
        yes, no, adds, deletes = self.ops[index]
        if state & yes == yes and not state & no:
            return (state & ~deletes) | adds
        return None


class Observation:
    def __init__(self, kernel, goal_yes=None, goal_no=None, always_yes=(), always_no=()):
        task = kernel.task
        self.sets = tuple(map(frozenset, (task.goal_yes if goal_yes is None else goal_yes,
                                         task.goal_no if goal_no is None else goal_no, always_yes, always_no)))
        self.positions = tuple(kernel.mask(facts) for facts in self.sets)
        self.impossible = task.immutable_goal_failure and goal_yes is None and goal_no is None

    def allowed(self, state):
        needed, forbidden = self.positions[2:]
        return state & needed == needed and not state & forbidden

    def achieved(self, state):
        needed, forbidden = self.positions[:2]
        return not self.impossible and state & needed == needed and not state & forbidden


def exchangeable(kernel, observation):
    """Prove each generator on the full grounded program and all observer sets."""
    operators = set(kernel.task.operators)
    remaining = list(kernel.task.objects)
    blocks = []
    while remaining:
        first, *rest = remaining
        mates, others = [], []
        for candidate in rest:
            swap = {first: candidate, candidate: first}
            def renamed(atom):
                return (atom[0], *(swap.get(symbol, symbol) for symbol in atom[1:]))
            preserves = all(frozenset(map(renamed, scope)) == scope for scope in observation.sets)
            preserves = preserves and all(swap_operator(op, swap) in operators for op in operators)
            (mates if preserves else others).append(candidate)
        if mates:
            blocks.append((first, *mates))
        remaining = others
    return tuple(sorted(blocks, key=lambda block: (-len(block), block)))


class Quotient:
    """Choose disjoint exchange blocks with at most one flexible object per atom."""

    def __init__(self, kernel, observation):
        self.exchangeable = exchangeable(kernel, observation)
        remaining = []
        flexible = set()
        for block in self.exchangeable:
            with_block = flexible | set(block)
            if all(len(with_block.intersection(atom[1:])) <= 1 for atom in kernel.atoms):
                flexible = with_block
                remaining.append(block)
        self.blocks = tuple(remaining)
        fixed, columns = 0, []
        for bit, atom in enumerate(kernel.atoms):
            if not flexible.intersection(atom[1:]):
                fixed |= (1 << bit)
        for block in self.blocks:
            pivot = block[0]
            templates = [atom for atom in kernel.atoms if pivot in atom[1:]]
            columns.append([
                tuple(1 << kernel.index[(template[0], *(obj if x == pivot else x for x in template[1:]))]
                      for template in templates)
                for obj in block
            ])
        self.fixed = fixed
        self.columns = columns

    def signatures(self, state):
        return tuple(tuple(sum(1 << offset for offset, bit in enumerate(column) if state & bit)
                           for column in block) for block in self.columns)

    def key(self, state):
        return (state & self.fixed, *(tuple(sorted(values)) for values in self.signatures(state)))

    def stabilizer(self, state):
        return {obj: (block_id, sample)
                for block_id, (block, values) in enumerate(zip(self.blocks, self.signatures(state), strict=True))
                for obj, sample in zip(block, values, strict=True)}


def action_key(signature, stabilizer):
    aliases = {}
    occurrences = {}
    result = [signature[0]]
    for obj in signature[1:]:
        signature_class = stabilizer.get(obj)
        if signature_class is None:
            result.append(obj)
            continue
        if obj not in aliases:
            aliases[obj] = occurrences.get(signature_class, 0)
            occurrences[signature_class] = aliases[obj] + 1
        result.append((*signature_class, aliases[obj]))
    return tuple(result)


def successors(kernel, observation, state, quotient=None):
    local = quotient.stabilizer(state) if quotient and quotient.blocks else {}
    groups = {}
    for index, (yes, no, adds, deletes) in enumerate(kernel.ops):
        if state & yes != yes or state & no:
            continue
        after = (state & ~deletes) | adds
        if not observation.allowed(after):
            continue
        if local:
            key = action_key(kernel.task.operators[index].signature, local)
            if key in groups:
                groups[key][2] += 1
            else:
                groups[key] = [index, after, 1]
        else:
            yield index, after, 1
    yield from groups.values()


def orbit_size(key):
    return prod(factorial(len(block)) // prod(factorial(n) for n in Counter(block).values())
                for block in key[1:])


def inventory(kernel, observation, quotient):
    """Recover exact named-state cardinality from all orbits of an invariant initial state."""
    root = quotient.key(kernel.start)
    require(observation.allowed(kernel.start) and orbit_size(root) == 1, "initial orbit is not invariant")
    seen = {root}
    queue = deque([kernel.start])
    concrete = 1
    tested_edges = named_edges = representative_edges = 0
    while queue:
        state = queue.popleft()
        multiplicity = orbit_size(quotient.key(state))
        for _, after, equivalent in successors(kernel, observation, state, quotient):
            tested_edges += 1
            representative_edges += equivalent
            named_edges += equivalent * multiplicity
            key = quotient.key(after)
            if key not in seen:
                seen.add(key)
                queue.append(after)
                concrete += orbit_size(key)
    return {"named_states": concrete, "orbit_states": len(seen), "representative_edges": representative_edges,
            "action_orbit_edges": tested_edges, "named_edges": named_edges}


def search(kernel, observation, start, quotient=None, state_budget=300000):
    """BFS over concrete representatives. Parent edges retain the original names."""
    if not observation.allowed(start):
        return {"status": "invariant-violated", "states": 0}
    key = quotient.key if quotient else operator.index
    origin = key(start)
    prev = {origin: (None, None)}
    queue = deque([(start, origin)])
    transitions = 0
    while queue:
        state, current = queue.popleft()
        if observation.achieved(state):
            plans = []
            end = state
            while current != origin:
                current, op = prev[current]
                plans.append(op)
            return {"status": "resolved", "states": len(prev), "transitions": transitions,
                    "end": end, "plan": plans[::-1]}
        for index, successor, _ in successors(kernel, observation, state, quotient):
            transitions += 1
            candidate = key(successor)
            if candidate not in prev:
                if len(prev) >= state_budget:
                    return {"status": "budget-exhausted", "states": len(prev), "transitions": transitions}
                prev[candidate] = (current, index)
                queue.append((successor, candidate))
    return {"status": "unreachable", "states": len(prev), "transitions": transitions}


def verify_partition(kernel, observation, quotient, budget=10000):
    """Check the full reachable quotient relation on small tasks, including edge lifting."""
    seen = {kernel.start}
    queue = deque(seen)
    signatures = {}
    count = 0
    while queue:
        state = queue.popleft()
        downstream = set()
        for index in range(len(kernel.ops)):
            after = kernel.next(state, index)
            if after is None or not observation.allowed(after):
                continue
            downstream.add(quotient.key(after))
            count += 1
            if after not in seen:
                require(len(seen) < budget, "independent partition check exhausted its state budget")
                seen.add(after)
                queue.append(after)
        grouped = {quotient.key(after) for _, after, _ in successors(kernel, observation, state, quotient)}
        require(grouped == downstream, "action stabilizer removed a distinct successor")
        key = quotient.key(state)
        signature = (observation.achieved(state), frozenset(downstream))
        require(signatures.setdefault(key, signature) == signature, "invalid transition or goal quotient")
    return {"concrete": len(seen), "orbits": len(signatures), "edges": count}
