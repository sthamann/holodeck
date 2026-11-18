"""Compose independent experts into a distribution over exact outcomes.

A request is read by several experts that were trained separately:

    reader      p(program | screen, request), asked once per labelling of the rows
    explainer   p(request | screen, program), the reverse direction
    Deck        an exact, hard factor: a program that does not compile has no mass

Their product is not sampled by decoding one model greedily. Candidates are drawn from the reader,
the Deck maps every candidate to the state it leads to, and programs with the same outcome pool
their mass. That quotient is exact and is what a purely neural composition cannot compute. The
result is a posterior over outcomes, which gives a decision, a calibrated reason to ask, and the
alternatives worth showing.
"""

import json
import math
import random

from intent import compile_program, outcome, parse
from tasks import relabel

FLOOR = 1e-6
WEIGHTS = [0.0, 0.1, 0.25, 0.5, 1.0]


def key(deck, state, program):
    """The exact outcome a program leads to, or None when the Deck refuses it."""
    statements = parse(program)
    compiled = compile_program(deck, state, statements) if statements is not None else None
    return None if compiled is None else json.dumps(outcome(compiled[1]), ensure_ascii=False)


def posterior(deck, views, candidates, weight=0.0, use_views=None):
    """Outcome posterior from recorded candidates.

    `views` are the same list under different row labels. `candidates[v]` is a list of
    [program, reader log probability, explainer log likelihood or None] sampled in view v.
    """
    use_views = range(len(views)) if use_views is None else use_views
    masses, explained = [], {}
    for index in use_views:
        mass = {}
        for program, forward, backward in candidates[index]:
            result = key(deck, views[index]["state"], program)
            if result is None:
                continue
            mass[result] = mass.get(result, 0.0) + math.exp(forward)
            if backward is not None:
                explained[result] = max(explained.get(result, -math.inf), backward)
        total = sum(mass.values())
        masses.append({result: value / total for result, value in mass.items()} if total else {})
    outcomes = sorted({result for mass in masses for result in mass})
    if not outcomes:
        return {}
    score = {}
    for result in outcomes:
        score[result] = sum(math.log(mass.get(result, 0.0) + FLOOR) for mass in masses) / len(masses)
        if weight:
            score[result] += weight * explained.get(result, min(explained.values(), default=0.0))
    top = max(score.values())
    norm = sum(math.exp(value - top) for value in score.values())
    return {result: math.exp(value - top) / norm for result, value in score.items()}


def decide(deck, task, views, candidates, **options):
    """Top outcome, its probability, whether it is right, whether it changes anything, and the top three."""
    belief = posterior(deck, views, candidates, **options)
    if not belief:
        return {"right": False, "confidence": 0.0, "changes": False, "top3": False, "refused": True}
    ranked = sorted(belief, key=lambda result: (-belief[result], result))
    wanted = json.dumps(task["outcome"], ensure_ascii=False)
    return {"right": ranked[0] == wanted, "confidence": belief[ranked[0]], "refused": False,
            "changes": ranked[0] != json.dumps(outcome(task["state"]), ensure_ascii=False),
            "top3": wanted in ranked[:3]}


def risk(decisions, commits):
    """Wrong commits among the `commits` most confident decisions that change the list."""
    changing = sorted((d for d in decisions if d["changes"]), key=lambda d: -d["confidence"])[:commits]
    return [sum(not d["right"] for d in changing), len(changing)]


def views_of(tasks, seed, count=3):
    rng = random.Random(f"views:{seed}")
    return [tasks] + [[relabel(rng, task) for task in tasks] for _ in range(count - 1)]


def summarize(deck, tasks, views, table, weight):
    """Every way of deciding, on the same recorded candidates."""
    per_task = lambda index: [cohort[index] for cohort in views]
    ways = {
        # One greedy program has no distribution, so its confidence is the probability the reader gave it.
        "greedy reader": lambda i: {**decide(deck, tasks[i], per_task(i), [table[i][0][:1]], use_views=[0]),
                                    "confidence": math.exp(table[i][0][0][1])},
        "reader, mass over outcomes": lambda i: decide(deck, tasks[i], per_task(i), table[i], use_views=[0]),
        "reader under three labellings": lambda i: decide(deck, tasks[i], per_task(i), table[i]),
        "reader, labellings and explainer": lambda i: decide(deck, tasks[i], per_task(i), table[i], weight=weight),
    }
    report = {}
    for name, way in ways.items():
        decisions = [way(index) for index in range(len(tasks))]
        changing = sum(d["changes"] for d in decisions)
        report[name] = {
            "top outcome right": sum(d["right"] for d in decisions),
            "right outcome among top three": sum(d["top3"] for d in decisions),
            "refused by the Deck": sum(d["refused"] for d in decisions),
            "decisions that change the list": changing,
            "wrong among the most confident changes": {
                f"{share}%": risk(decisions, round(changing * share / 100)) for share in (50, 75, 90, 100)}}
    return report


def analyse(deck, seed, cohorts, recorded):
    """Pick the explainer weight on separate tasks, then report every way of deciding on the scored ones."""
    held = cohorts["weight selection"]
    held_views = views_of(held, seed)
    right = {weight: summarize(deck, held, held_views, recorded["weight selection"], weight)[
        "reader, labellings and explainer"]["top outcome right"] for weight in WEIGHTS}
    weight = max(WEIGHTS, key=lambda value: (right[value], -value))
    scored = cohorts["scored"]
    return {"explainer_weight": weight, "weight_selection_right_of_200": {str(k): v for k, v in right.items()},
            "tasks": len(scored), "ways": summarize(deck, scored, views_of(scored, seed), recorded["scored"], weight),
            "explainer_weight_sweep_on_scored_tasks": {
                str(value): summarize(deck, scored, views_of(scored, seed), recorded["scored"], value)[
                    "reader, labellings and explainer"]["top outcome right"] for value in WEIGHTS}}
