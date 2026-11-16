"""Compile an exact decision-only quotient of effect tables for affine preferences."""

from fractions import Fraction
from itertools import combinations, product
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vertical"))
from compiler import require
from preferences import minima, resolve, verify_selection


def envelope(result):
    """The optimal-plan set on each point and open cell of the weight interval."""
    if not result:
        return (), ()
    cuts = {Fraction(0), Fraction(1)}
    for first, second in combinations(result.values(), 2):
        intercept = first["price"] - second["price"]
        slope = first["time"] - second["time"] - intercept
        if slope:
            crossing = Fraction(-intercept, slope)
            if 0 < crossing < 1:
                cuts.add(crossing)
    knots = sorted(cuts)
    points = [tuple(sorted(minima(result, knot))) for knot in knots]
    opens = [tuple(sorted(minima(result, (left + right) / 2))) for left, right in zip(knots, knots[1:])]
    retain = [0, *(i for i in range(1, len(knots) - 1) if opens[i - 1] != points[i] or points[i] != opens[i]),
              len(knots) - 1]
    return (tuple((knots[i], points[i]) for i in retain),
            tuple(opens[i] for i in retain[:-1]))


def query(compiled, weight):
    require(0 <= weight <= 1, "weight outside [0, 1]")
    points, opens = compiled
    if not points:
        return ()
    for index, (knot, options) in enumerate(points):
        if weight == knot:
            return options
        if weight < knot:
            return opens[index - 1]
    raise ValueError("weight outside [0, 1]")


def make_table(values):
    return {name: {"time": duration, "price": price, "steps": []}
            for name, (duration, price) in zip("ABC", zip(values[::2], values[1::2]))}


def main():
    signatures = set()
    checked = 0
    for values in product(range(4), repeat=6):
        result = make_table(values)
        compiled = envelope(result)
        points, _ = compiled
        signatures.add(compiled)
        probes = {*(Fraction(i, 12) for i in range(13)), *(weight for weight, _ in points)}
        for weight in probes:
            require(set(query(compiled, weight)) == minima(result, weight), "envelope lost an optimum")
            checked += 1

    first = make_table([1, 5, 5, 1])
    second = make_table([1, 10, 5, 1])
    ordinary_ranks = lambda table: tuple(tuple(sorted(table, key=lambda name: table[name][key]))
                                         for key in ("time", "price"))
    require(ordinary_ranks(first) == ordinary_ranks(second), "expected identical component ranks")
    weight = Fraction(3, 5)
    require(query(envelope(first), weight) == ("A",) and query(envelope(second), weight) == ("B",),
            "rank-only collision did not change the weighted decision")

    shifted = {name: {**entry, "time": entry["time"] + 5, "price": entry["price"] + 7}
               for name, entry in first.items()}
    require(envelope(first) == envelope(shifted), "common offset altered decision semantics")
    for table in (first, second):
        interval = (weight, weight)
        result = resolve(table, interval)
        proof = result["proof"]
        require(verify_selection(table, interval, proof), "valid certificate rejected")
        require(not verify_selection(shifted, interval, proof), "certificate was accepted on another effect table")
        forged = {**proof, "choice": "B" if proof["choice"] == "A" else "A"}
        require(not verify_selection(table, interval, forged), "non-optimal proof accepted")

    print(json.dumps({
        "effect_tables": 4 ** 6,
        "decision_classes": len(signatures),
        "checked_weight_queries": checked,
        "identical_rank_witness": {"weight": str(weight), "first_optimum": "A", "second_optimum": "B",
                                   "first_crossing": "1/2", "second_crossing": "9/13"},
        "common_shift_preserves_choice": True,
        "altered_certificate_rejected": True,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
