"""Todo lists, English requests and reference intent programs for the TodoMVC Deck.

Training and scored tasks share no todo title and no request wording. Scored wordings are looser
than the training ones. All of it is authored for this study. None of it was collected from people.
"""

import random

from deck import Deck
from intent import compile_program, outcome, parse

TITLES = {
    "train": {
        "shopping": ["buy milk", "get bread", "order printer ink", "pick up eggs", "buy birthday candles",
                     "get dog food", "order new headphones", "buy laundry detergent", "pick up coffee beans"],
        "work": ["send the quarterly report", "email Dana about the contract", "prepare slides for Monday",
                 "review Tom's pull request", "schedule the team offsite", "update the project plan",
                 "call the client back", "submit the expense claim", "write meeting notes"],
        "home": ["water the plants", "fix the kitchen tap", "vacuum the living room", "take out the recycling",
                 "clean the windows", "change the bed sheets", "descale the kettle", "mow the lawn"],
        "health": ["book a dentist appointment", "go for a run", "refill the prescription", "do yoga",
                   "schedule an eye test", "take vitamins", "book a physio session"],
        "travel": ["renew my passport", "book flights to Lisbon", "reserve the rental car", "pack the suitcase",
                   "print the boarding passes", "buy travel insurance", "book the airport shuttle"],
        "money": ["pay the rent", "file the tax return", "cancel the gym membership", "check the bank statement",
                  "pay the electricity bill", "transfer savings", "renew the car insurance"],
    },
    "test": {
        "shopping": ["grab oat milk", "buy tomatoes", "order a phone charger", "get cat litter", "buy wrapping paper"],
        "work": ["finish the budget forecast", "reply to Priya's email", "book a room for the workshop",
                 "draft the job posting", "send the invoice to Acme"],
        "home": ["unclog the shower drain", "dust the bookshelves", "replace the hallway bulb",
                 "defrost the freezer", "sweep the balcony"],
        "health": ["book a blood test", "go swimming", "order new contact lenses", "stretch for ten minutes",
                   "call the doctor's office"],
        "travel": ["apply for the visa", "book a hotel in Kyoto", "check the train times", "charge the camera",
                   "exchange some currency"],
        "money": ["pay the water bill", "dispute the card charge", "top up the pension", "send the deposit",
                  "review the phone contract"],
    },
}
CATEGORY_WORDS = {
    "train": {"shopping": ["shopping", "things to buy"], "work": ["work", "job"], "home": ["household", "chores"],
              "health": ["health"], "travel": ["travel", "trip"], "money": ["money", "finance"]},
    "test": {"shopping": ["groceries and purchases", "errands at the shops"], "work": ["office"],
             "home": ["housework", "around the house"], "health": ["medical and fitness"],
             "travel": ["holiday"], "money": ["bills and banking"]},
}
ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh"]

# {q} quoted title, {k} a word that only this todo has, {o} position word, {c} category, {t} new title
WORDING = {
    "train": {
        "add": ['Add "{t}".', 'Put "{t}" on the list.', 'New todo: {t}', 'I also need to {t}.', 'Please add {t}.'],
        "complete": ['Mark "{q}" as done.', 'Complete the {k} one.', 'Check off the {o} item.', '"{q}" is done.',
                     'Tick the {o} one.', 'I finished the {k} task.'],
        "reopen": ['Mark "{q}" as not done.', 'Reopen the {k} one.', 'Uncheck the {o} item.',
                   '"{q}" is not finished after all.', 'Untick the {k} task.'],
        "delete": ['Delete "{q}".', 'Remove the {k} one.', 'Delete the {o} item.', 'Get rid of "{q}".',
                   'Drop the {k} task.'],
        "rename": ['Rename "{q}" to "{t}".', 'Change the {k} one to "{t}".', 'Edit the {o} item to say {t}.',
                   'Replace "{q}" with "{t}".'],
        "complete all": ["Mark everything as done.", "Complete all todos.", "Check off all of them."],
        "reopen all": ["Mark everything as not done.", "Reopen all todos.", "Uncheck all of them."],
        "delete completed": ["Clear the completed ones.", "Delete everything that is done.", "Remove finished todos."],
        "complete category": ["Mark all the {c} todos as done.", "Complete everything about {c}.",
                              "Check off the {c} items."],
        "delete category": ["Delete all the {c} todos.", "Remove everything about {c}.", "Drop the {c} items."],
        "show": ["Show only {f} todos.", "Switch to the {f} view.", "Filter: {f}."],
        "missing": ['Mark "{t}" as done.', 'Delete "{t}".', 'Rename "{t}" to "something else".'],
        "empty": ["Hello.", "What is this list for?", "Make it nicer.", "Do the usual.", "Thanks!"],
        "join": ["{a} Then {b}", "{a} Also, {b}", "First: {a} Second: {b}"],
    },
    "test": {
        "add": ['Remind me to {t}.', 'Stick "{t}" on there.', 'one more thing for the list: {t}',
                "Don't let me forget to {t}."],
        "complete": ['I already did "{q}".', 'The {k} thing is sorted.', 'Cross out the {o} entry.',
                     'done with "{q}"', 'You can tick the {k} one, that got handled.'],
        "reopen": ['Actually "{q}" still needs doing.', 'The {k} thing is not sorted yet.',
                   'Put the {o} entry back to open.', 'I was wrong, {k} is still pending.'],
        "delete": ['Forget about "{q}".', 'Scrap the {k} thing.', 'Take the {o} entry off the list.',
                   "I don't need \"{q}\" anymore."],
        "rename": ['"{q}" should really be "{t}".', 'Reword the {k} thing as "{t}".',
                   'Make the {o} entry read {t} instead.'],
        "complete all": ["I got through the whole list.", "Tick the lot.", "All of it is finished."],
        "reopen all": ["Nothing here is actually finished, reset it.", "Untick the lot.", "Start the list over, all open."],
        "delete completed": ["Tidy away what's finished.", "Bin the ticked ones.", "I don't need to see the finished stuff, remove it."],
        "complete category": ["Everything to do with {c} is finished.", "Cross out the {c} bits.",
                              "I took care of all the {c} entries."],
        "delete category": ["Scrap everything to do with {c}.", "Take the {c} bits off the list.",
                            "I'm not doing any of the {c} entries, bin them."],
        "show": ["Let me see just the {f} ones.", "Hide everything that isn't {f}.", "I only care about {f} right now."],
        "missing": ['I already did "{t}".', 'Forget about "{t}".', '"{t}" should really be "something else".'],
        "empty": ["Good morning!", "How many lists can I have?", "Sort this out.", "You know what to do.", "ok"],
        "join": ["{a} Oh, and {b}", "{a} While you're at it: {b}", "Two things. {a} {b}"],
    },
}
FILTER_WORDS = {"train": {"all": "all", "active": "active", "completed": "completed"},
                "test": {"all": "all", "active": "unfinished", "completed": "finished"}}


def category_of(title, split):
    return next(name for name, titles in TITLES[split[1]].items() if title in titles)


def draw_state(rng, split, deck):
    """A todo list as the original could hold it, including a stale flip-flop and a non-default view."""
    pool = [title for titles in TITLES[split[1]].values() for title in titles]
    count = rng.randint(2, 7)
    numbers = rng.sample(range(1, 60), count)
    todos = [{"id": number, "title": title, "completed": rng.random() < 0.35}
             for number, title in zip(numbers, rng.sample(pool, count))]
    return {"filter": rng.choices(["all", "active", "completed"], [6, 1, 1])[0], "all_box": rng.random() < 0.2,
            "todos": todos, "next": 60}


def refer(rng, deck, state, record, wording):
    """Fill one reference to a visible todo: by quoted title, by a word only it has, or by position."""
    visible = deck.view(state)
    words = [word for word in record["title"].split() if len(word) > 3 and not any(
        word in other["title"].split() for other in state["todos"] if other is not record)]
    position = visible.index(record)
    choices = [template for template in wording if ("{k}" not in template or words)
               and ("{o}" not in template or position < len(ORDINALS))]
    template = rng.choice(choices)
    ordinal = "last" if position == len(visible) - 1 and position > 0 and rng.random() < 0.4 else ORDINALS[position]
    return template, {"q": record["title"], "k": rng.choice(words) if words else "", "o": ordinal}


def single(rng, split, deck, state, kind):
    """One request and its reference program, or None when this kind does not apply to the state."""
    wording, visible = WORDING[split[0]], deck.view(state)
    pool = [title for titles in TITLES[split[1]].values() for title in titles
            if title not in [record["title"] for record in state["todos"]]]
    if kind == "add":
        title = rng.choice(pool)
        return rng.choice(wording["add"]).format(t=title), f'add "{title}"'
    if kind in ("complete", "reopen", "delete", "rename"):
        fit = [record for record in visible if kind in ("delete", "rename")
               or record["completed"] == (kind == "reopen")]
        if not fit:
            return None
        record = rng.choice(fit)
        template, values = refer(rng, deck, state, record, wording[kind])
        if kind == "rename":
            title = rng.choice(pool)
            return template.format(t=title, **values), f'rename #{record["id"]} "{title}"'
        return template.format(**values), f'{kind} #{record["id"]}'
    if kind in ("complete all", "reopen all", "delete completed"):
        return rng.choice(wording[kind]), kind
    if kind in ("complete category", "delete category"):
        category = rng.choice(list(TITLES[split[1]]))
        members = [record for record in visible if category_of(record["title"], split) == category]
        if not members or len(members) == len(visible):
            return None
        text = rng.choice(wording[kind]).format(c=rng.choice(CATEGORY_WORDS[split[0]][category]))
        return text, kind.split()[0] + "".join(f' #{record["id"]}' for record in members)
    if kind == "show":
        target = rng.choice([name for name in ("all", "active", "completed") if name != state["filter"]])
        return rng.choice(wording["show"]).format(f=FILTER_WORDS[split[0]][target]), f"show {target}"
    if kind == "missing":
        return rng.choice(wording["missing"]).format(t=rng.choice(pool)), "unclear"
    return rng.choice(wording["empty"]), "unclear"


KINDS = ["add", "complete", "reopen", "delete", "rename", "complete all", "reopen all", "delete completed",
         "complete category", "delete category", "show", "missing", "empty"]
WEIGHTS = [3, 3, 2, 2, 2, 1, 1, 1, 3, 2, 1, 1, 1]


def draw_task(rng, split, deck):
    while True:
        state = draw_state(rng, split, deck)
        if rng.random() < 0.25:
            first, second = rng.sample(KINDS[:11], 2)
            # Both halves are phrased against the list the person is looking at.
            a, b = single(rng, split, deck, state, first), single(rng, split, deck, state, second)
            if not a or not b:
                continue
            request, program, kind = rng.choice(WORDING[split[0]]["join"]).format(a=a[0], b=b[0]), f"{a[1]}; {b[1]}", "two requests"
        else:
            kind = rng.choices(KINDS, WEIGHTS)[0]
            made = single(rng, split, deck, state, kind)
            if not made:
                continue
            request, program = made
        compiled = compile_program(deck, state, parse(program))
        if compiled is None:
            continue
        return {"state": state, "request": request, "program": program, "kind": kind,
                "actions": compiled[0], "outcome": outcome(compiled[1])}


def build(seed, count, split, titles=None):
    """`split` picks the request wording and, unless `titles` says otherwise, the todo titles."""
    rng, deck = random.Random(f"todomvc:{seed}:{split}:{titles}"), Deck()
    split = (split, titles or split)
    return [draw_task(rng, split, deck) for _ in range(count)]


def relabel(rng, task):
    """The same list with other handles. A reading of the request must not depend on them."""
    numbers = rng.sample(range(1, 60), len(task["state"]["todos"]))
    mapping = {record["id"]: number for record, number in zip(task["state"]["todos"], numbers)}
    state = {**task["state"], "todos": [{**record, "id": mapping[record["id"]]} for record in task["state"]["todos"]]}
    return {**task, "state": state}
