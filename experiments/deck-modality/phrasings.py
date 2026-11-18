"""Requests in a looser register than the generator's templates, and requests that have no goal.

Written once, before the trained reader saw any of them, and not edited after scoring.
They are authored for this study. They are not requests collected from people.
"""

import random

NUMBER = "zero one two three four five six seven eight nine".split()

# {f} field name, {v} digit, {w} number word
LOOSE = {
    "eq": ["I want {f} at {v}, not more and not less.", "Bring {f} to {w}.",
           "{f} should read {v} when you're done.", "set things up so we have precisely {w} {f}",
           "can {f} be {v}?", "We need {f} = {v}."],
    "ge": ["We're short on {f}, I need {v} or more.", "{f} must not fall below {v}.",
           "Don't leave me with fewer than {w} {f}.", "minimum of {v} for {f} please",
           "I'd like {f} up to {v} at the very least.", "{f}: {v}+"],
    "le": ["Too many {f}. Cap it at {v}.", "{f} must not exceed {v}.", "Don't let {f} go above {w}.",
           "keep {f} down to {v} max", "I can't have more than {v} {f}.",
           "{f} needs to come down to {v} or less."],
    "on": ["Turn {f} on.", "Switch on {f} for me.", "I need {f} enabled, thanks.",
           "{f} has to be active by the end.", "activate {f}", "Why is {f} off? Fix that."],
    "off": ["Turn {f} off.", "Kill {f}.", "{f} shouldn't be on anymore.", "deactivate {f} please",
            "Make sure nobody leaves {f} on.", "I want {f} disabled."],
}
BOTH = ["{a} Also: {b}", "{a} And one more thing. {b}", "Two things. {a} {b}"]

# No commit is right for any of these. {f} and {g} are fields of the contract, {x} is not.
NO_GOAL = {
    "field the application does not have": [
        "Please make sure {x} is at least 2.", "Get {x} to 1.", "Turn {x} off.", "I need three or more {x}."],
    "not a request for a state": [
        "What does this application do?", "Thanks, that's all for now.", "Who approved the last change?",
        "Why is {f} so low?"],
    "too vague to pin down": [
        "Make it better.", "Fix the numbers.", "Do whatever is usual here.", "Sort out {f}."],
    "outside the goal language": [
        "Make sure {f} is an even number.", "{f} should end up higher than {g}.",
        "Keep {f} anything but {v}.", "Double {f}."],
}
FOREIGN = "widgets gizmos tokens credits vouchers pallets".split()


# Training counterparts. No template is shared with the scored lists above.
TRAIN_LOOSE = {
    "eq": ["{f} has to be {v}.", "Set {f} to {w}.", "we should land on {v} {f}", "Leave {f} at {v} in the end.",
           "I'm after exactly {w} {f}.", "{f} -> {v}", "make {f} {v} please", "Target for {f} is {v}."],
    "ge": ["I need {v} {f} minimum.", "{f} can't be under {v}.", "Give me no less than {w} {f}.",
           "raise {f} to {v} or beyond", "At least {w} {f}, please.", "{f} >= {v}",
           "we want {f} at {v} or higher", "Not enough {f}. {v} is the floor."],
    "le": ["{f} can't be over {v}.", "No more than {w} {f}.", "bring {f} down to {v} or lower",
           "Limit {f} to {v}.", "{f} <= {v}", "we want {f} at {v} or under",
           "There's too much {f}, {v} is the ceiling.", "{w} {f} tops."],
    "on": ["Enable {f}.", "{f} should be running.", "get {f} switched on", "I want {f} active.",
           "Please have {f} on.", "{f} must not stay off."],
    "off": ["Disable {f}.", "{f} should be stopped.", "get {f} switched off", "I want {f} inactive.",
            "Please have {f} off.", "{f} must not stay on.", "Shut {f} down."],
}
TRAIN_BOTH = ["{a} Then {b}", "{a} Besides that, {b}", "First: {a} Second: {b}", "{a} {b}"]
TRAIN_NO_GOAL = [
    "Make sure {x} is at most 3.", "Set {x} to 2.", "Enable {x}.", "We need at least 2 {x}.",
    "How does {f} work?", "Good morning.", "Is anyone there?", "When was {f} last changed?", "Never mind.",
    "Improve things.", "Do the right thing.", "Handle {f}.", "Clean this up.", "Something is off, fix it.",
    "Make {f} an odd number.", "{f} should match {g}.", "{f} must differ from {v}.", "Halve {f}.",
    "Make {f} bigger than {g}."]
TRAIN_FOREIGN = "gadgets sprockets barrels lanterns ribbons anchors".split()


def fill(rng, world, template, foreign):
    first, second = rng.sample(world.names, 2) if len(world.names) > 1 else (world.names[0],) * 2
    return template.format(f=first, g=second, v=rng.randint(0, 3),
                           x=rng.choice([name for name in foreign if name not in world.names]))


def augment(rng, world, task):
    """Training mix: generator templates, looser wordings of the same goal, and requests without a goal."""
    roll = rng.random()
    if roll < 0.4:
        return task
    if roll < 0.85:
        return {**task, "request": loose(rng, world, task["goal"], TRAIN_LOOSE, TRAIN_BOTH)}
    return {**task, "request": fill(rng, world, rng.choice(TRAIN_NO_GOAL), TRAIN_FOREIGN),
            "unclear": True, "optimal": [], "depth": 0}


def loose(rng, world, goal, atoms=LOOSE, joins=BOTH):
    parts = []
    for field, comparator, value in goal:
        kind = comparator if world.bounds[field][1] > 1 else "on" if value else "off"
        parts.append(rng.choice(atoms[kind]).format(f=world.names[field], v=value, w=NUMBER[value]))
    return parts[0] if len(parts) == 1 else rng.choice(joins).format(a=parts[0], b=parts[1])


def restate(cohort, seed=1):
    """The same tasks with every request rewritten. Goals, states and labels are untouched."""
    rng = random.Random(f"loose:{seed}")
    return [(world, {**task, "request": loose(rng, world, task["goal"])}) for world, task in cohort]


def without_goal(cohort, seed=1, limit=240):
    """Tasks whose request names no reachable goal of this contract, grouped by the reason."""
    rng = random.Random(f"no-goal:{seed}")
    kinds = list(NO_GOAL)
    out = []
    for index, (world, task) in enumerate(cohort[:limit]):
        kind = kinds[index % len(kinds)]
        # Draw order kept from the first scored run so the set stays identical.
        first, second = rng.sample(world.names, 2) if len(world.names) > 1 else (world.names[0],) * 2
        text = rng.choice(NO_GOAL[kind]).format(
            f=first, g=second, v=rng.randint(0, 3), x=rng.choice([name for name in FOREIGN if name not in world.names]))
        out.append((world, {**task, "request": text, "kind": kind}))
    return out
