"""A small exact oracle for bounded continuous linear preferences."""

from fractions import Fraction
from itertools import combinations, product
import json


def dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def plus(a, b):
    return tuple(x + y for x, y in zip(a, b, strict=True))


def minus(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def linear_system(rows):
    matrix = [[Fraction(x) for x in (*normal, rhs)] for normal, rhs in rows]
    width = len(rows)
    for col in range(width):
        pivot = next((row for row in range(col, width) if matrix[row][col]), None)
        if pivot is None:
            return None
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        row = matrix[col]
        matrix[col] = [value / row[col] for value in row]
        for below in range(width):
            if below != col and matrix[below][col]:
                multiplier = matrix[below][col]
                matrix[below] = [value - multiplier * basis
                                 for value, basis in zip(matrix[below], matrix[col], strict=True)]
    return tuple(row[-1] for row in matrix)


class VersionSpace:
    """Feasible weights in a signed cube subject to strict or tied comparisons."""

    def __init__(self, dimensions, comparisons):
        if not 1 <= dimensions <= 4:
            raise ValueError("exact vertex enumeration supports one to four dimensions")
        unit = [tuple(int(i == j) for i in range(dimensions)) for j in range(dimensions)]
        constraints = [(normal, -1) for u in unit for normal in (u, tuple(-x for x in u))]
        strict = []
        for difference, answer in comparisons:
            if len(difference) != dimensions or type(answer) is not int or answer not in (-1, 0, 1):
                raise ValueError("invalid typed comparison")
            if answer == 0:
                constraints.extend(((difference, 0), (tuple(-x for x in difference), 0)))
            else:
                normal = tuple(answer * x for x in difference)
                constraints.append((normal, 0))
                strict.append(normal)
        self.strict = tuple(strict)
        constraints = tuple(dict.fromkeys(constraints))
        vertices = set()
        for active in combinations(constraints, dimensions):
            point = linear_system(active)
            if point is not None and all(dot(normal, point) >= rhs for normal, rhs in constraints):
                vertices.add(point)
        self.vertices = tuple(sorted(vertices))
        self.anchor = self._anchor()

    def _anchor(self):
        if not self.vertices:
            return None
        if not self.strict:
            return self.vertices[0]
        witnesses = [max(self.vertices, key=lambda point: dot(normal, point)) for normal in self.strict]
        if any(dot(normal, point) <= 0 for normal, point in zip(self.strict, witnesses, strict=True)):
            return None
        return tuple(sum(axis) / len(witnesses) for axis in zip(*witnesses, strict=True))

    def counterexample(self, difference, vertex):
        gap = dot(difference, vertex)
        if gap >= 0:
            raise ValueError("vertex is not a witness")
        baseline = dot(difference, self.anchor)
        # The closure vertex can lie on a strict boundary. Move toward the strict anchor
        # by a rational amount that preserves the negative gap.
        step = min(Fraction(1, 2), -gap / (2 * (abs(baseline) + abs(gap))))
        witness = plus(tuple((1 - step) * value for value in vertex),
                       tuple(step * value for value in self.anchor))
        if dot(difference, witness) >= 0 or any(dot(normal, witness) <= 0 for normal in self.strict):
            raise AssertionError("witness is not in the strict feasible region")
        return witness

    def decision(self, options):
        if self.anchor is None:
            return {"status": "contradiction"}
        if not options:
            return {"status": "infeasible"}
        rejected = {}
        for label, point in sorted(options.items()):
            for competitor, other in sorted(options.items()):
                difference = minus(other, point)
                vertex = min(self.vertices, key=lambda weight: dot(difference, weight))
                if dot(difference, vertex) < 0:
                    rejected[label] = {
                        "competitor": competitor,
                        "weights": [str(value) for value in self.counterexample(difference, vertex)],
                    }
                    break
            else:
                return {"status": "resolved", "plan": label}
        return {"status": "underdetermined", "witness": rejected}


def tests():
    observations = [((1, 0, 0), 1), ((0, 1, 0), 1), ((0, 0, 1), 1),
                    ((-1, 1, 0), 1), ((1, 0, -1), 1), ((2, -1, -1), 1)]
    plans = {"A": (0, 4, 3), "B": (2, 1, 2), "C": (5, 0, 0)}
    initial = VersionSpace(3, observations[:3])
    later = VersionSpace(3, observations)
    first, second = initial.decision(plans), later.decision(plans)
    if first["status"] != "underdetermined" or second != {"status": "resolved", "plan": "B"}:
        raise AssertionError("continuum intent not correctly classified")
    for label, witness in first["witness"].items():
        weights = tuple(map(Fraction, witness["weights"]))
        if any(dot(difference, weights) <= 0 for difference, _ in observations[:3]):
            raise AssertionError("non-admissible alternative intent")
        if dot(plans[witness["competitor"]], weights) >= dot(plans[label], weights):
            raise AssertionError("alternative intent does not defeat the candidate")
    trials = 0
    for values in product(range(1, 12), repeat=3):
        weight = tuple(Fraction(value, 11) for value in values)
        if all(dot(diff, weight) > 0 for diff, _ in observations):
            if dot(plans["B"], weight) != min(dot(plan, weight) for plan in plans.values()):
                raise AssertionError("independent rational grid found divergence")
            trials += 1
    contradictory = VersionSpace(1, [((1,), 1), ((1,), -1)]).decision({"x": (0,)})
    if contradictory["status"] != "contradiction":
        raise AssertionError("strict comparisons admitted a closure-only point")
    return {"without_additional_answers": first, "with_additional_answers": second,
            "closed_vertices": len(later.vertices), "strict_grid_probes": trials,
            "contradictory_strict_answers_refused": True}


if __name__ == "__main__":
    print(json.dumps(tests(), indent=2, sort_keys=True))
