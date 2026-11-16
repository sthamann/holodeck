"""Bounded objective discovery directly from typed, ordered execution effects."""

from functools import lru_cache
from itertools import combinations, product


def effect_features(boards):
    """Infer the vocabulary from execution, including reducers for repeated events."""
    channels = {}
    for options in boards.values():
        for option in options.values():
            counts = {}
            for channel, _ in option["effects"]:
                counts[channel] = counts.get(channel, 0) + 1
            for channel, count in counts.items():
                channels[channel] = max(channels.get(channel, 0), count)
    return tuple((channel, reducer) for channel in sorted(channels)
                 for reducer in (("sum",) if channels[channel] == 1 else ("sum", "max", "last", "count")))


def vectors(boards, features):
    result = {}
    for context, options in boards.items():
        for label, option in options.items():
            channels = {}
            for channel, amount in option["effects"]:
                channels.setdefault(channel, []).append(amount)
            values = []
            for channel, reducer in features:
                observed = channels.get(channel, [])
                values.append({"sum": sum(observed), "max": max(observed, default=0),
                               "last": observed[-1] if observed else 0, "count": len(observed)}[reducer])
            result[context, label] = tuple(values)
    return result


def candidates(features):
    signed = tuple(product(range(len(features)), (-1, 1)))
    return ((first,) for first in signed), (
        (first, second) for first in signed for second in signed
        if features[first[0]][0] != features[second[0]][0]
    )


def score(values, rule):
    return tuple(values[column] * sign for column, sign in rule)


def bits(value):
    while value:
        bit = value & -value
        yield bit.bit_length() - 1
        value ^= bit


def effect_signature(board):
    return tuple((label, tuple(tuple(event) for event in plan["effects"]))
                 for label, plan in sorted(board.items()))


class Problem:
    """Compile hypotheses to their observable answers and target optima."""

    def __init__(self, boards, target, calibration):
        self.features = effect_features(boards)
        self.labels = tuple(sorted(boards[target]))
        self.target_effects = effect_signature(boards[target])
        questions = tuple((context, left, right) for context in sorted(calibration)
                          for left, right in combinations(sorted(boards[context]), 2))
        vec = vectors(boards, self.features)
        unique = {}
        for bucket in candidates(self.features):
            for rule in bucket:
                answers = []
                for context, left, right in questions:
                    a = score(vec[context, left], rule)
                    b = score(vec[context, right], rule)
                    answers.append((a > b) - (a < b))
                scores = [score(vec[target, option], rule) for option in self.labels]
                minimum = min(scores)
                optima = sum(1 << i for i, value in enumerate(scores) if value == minimum)
                unique.setdefault((tuple(answers), optima), []).append(rule)
        self.classes = tuple(unique)
        self.members = tuple(unique.values())
        self.raw = sum(map(len, self.members))
        self.questions = questions
        self.full = (1 << len(self.classes)) - 1
        self.answer_groups = []
        for index in range(len(questions)):
            answer = [0, 0, 0]
            for hypothesis, (columns, _) in enumerate(self.classes):
                answer[columns[index] + 1] |= 1 << hypothesis
            self.answer_groups.append(tuple(answer))
        # Memoization belongs to the finite compiled instance.
        self.common = lru_cache(None)(self.common)
        buckets = {}
        for index, (column, _) in enumerate(self.classes):
            buckets[column] = buckets.get(column, 0) | (1 << index)
        self.unresolvable = tuple(mask for mask in buckets.values() if not self.common(mask))
        self.impossible = sum(self.unresolvable)

    def common(self, mask):
        if not mask:
            return 0
        result = (1 << len(self.labels)) - 1
        for index in bits(mask):
            result &= self.classes[index][1]
            if not result:
                break
        return result

    def splits(self, mask):
        seen = set()
        for index, group in enumerate(self.answer_groups):
            parts = tuple(sorted(p & mask for p in group if p & mask))
            if len(parts) < 2 or parts in seen:
                continue
            seen.add(parts)
            yield index, parts

    def failure(self, mask):
        """Return an inclusion-minimal set with identical answers and no common plan."""
        active = tuple(bits(mask))
        if not active or self.common(mask):
            raise ValueError("expected nonempty decision conflict")
        for index in active:
            reduced = mask ^ (1 << index)
            if reduced and not self.common(reduced):
                mask = reduced
        return [
            {"rule": [[*self.features[feature], sign] for feature, sign in self.members[index][0]],
             "optima": [self.labels[i] for i in bits(self.classes[index][1])],
             "answers": list(self.classes[index][0])}
            for index in bits(mask)
        ]


class Solver:
    """Compute an exact minimax query table over the reduced version space."""

    def __init__(self, problem, max_states=100000, identify=False, prove_deferral=False):
        self.problem = problem
        if identify and prove_deferral:
            raise ValueError("identity and decision-or-deferral have different terminal conditions")
        self.identify_exactly = identify
        self.prove_deferral = prove_deferral
        self.max_states = max_states
        self.expanded = 0
        self.solve = lru_cache(None)(self.solve)

    def solve(self, mask):
        terminal = mask.bit_count() == 1 if self.identify_exactly else bool(self.problem.common(mask))
        if terminal or (self.prove_deferral and not (mask & ~self.problem.impossible)):
            return 0, None
        self.expanded += 1
        if self.expanded > self.max_states:
            raise ValueError("minimax search exhausted its declared state budget")
        best_depth, best_question = float("inf"), None
        # Small largest branch first for pruning. Every distinct split remains eligible.
        choices = sorted(self.problem.splits(mask), key=lambda item: (max(p.bit_count() for p in item[1]), item[0]))
        for index, parts in choices:
            worst = 0
            for part in sorted(parts, key=int.bit_count, reverse=True):
                depth, _ = self.solve(part)
                worst = max(worst, depth)
                if 1 + worst >= best_depth:
                    break
            if 1 + worst < best_depth:
                best_depth, best_question = 1 + worst, index
            if best_depth == 1:
                break
        return best_depth, best_question

    def identify(self, mask):
        if mask.bit_count() == 1:
            return None
        return min(self.problem.splits(mask), key=lambda item: (max(p.bit_count() for p in item[1]), item[0]),
                   default=(None, ()))[0]


def decide(problem, solver, get_answer, greedy=False, identify=False):
    mask = problem.full
    history = []
    while mask:
        preferred = problem.common(mask)
        if preferred and (not identify or mask.bit_count() == 1):
            return {"plan": problem.labels[next(bits(preferred))], "history": history, "mask": mask}
        if solver.prove_deferral and not (mask & ~problem.impossible):
            witnesses = [problem.failure(mask & group) for group in problem.unresolvable if mask & group]
            return {"deferral": witnesses, "history": history, "mask": mask}
        index = solver.identify(mask) if greedy else solver.solve(mask)[1]
        if index is None:
            # A global minimax failure can be caused by one branch. Continue to narrow
            # when there is still an available informative question on this run.
            index = solver.identify(mask)
        if index is None:
            if preferred:
                return {"plan": problem.labels[next(bits(preferred))], "history": history, "mask": mask}
            return {"conflict": problem.failure(mask), "history": history, "mask": mask}
        question = problem.questions[index]
        answer = get_answer(question)
        if type(answer) is not int or answer not in (-1, 0, 1):
            raise ValueError("answer must be left, tie, or right")
        history.append((index, answer))
        mask &= problem.answer_groups[index][answer + 1]
    return {"contradiction": True, "history": history, "mask": 0}
