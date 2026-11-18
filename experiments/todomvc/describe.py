"""The Deck in plain English: every program it could run now, and what each one would do.

Nothing here is learned. The list of candidates comes from the state, and each description is
rendered from the exact actions. A model that has never seen this application can therefore judge
candidates by reading.
"""

import re

from intent import compile_program, parse

VIEW = {"all": "all todos", "active": "only the unfinished todos", "completed": "only the finished todos"}


def names(titles):
    quoted = [f'"{title}"' for title in titles]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def describe(deck, state, program):
    """One sentence per statement, from the records it touches. None when the Deck refuses the program."""
    statements = parse(program)
    if statements is None or compile_program(deck, state, statements) is None:
        return None
    if not statements:
        return "Changed nothing, because the request did not say what to change."
    title = {record["id"]: record["title"] for record in state["todos"]}
    out = []
    for statement in statements:
        kind = statement[0]
        if kind == "add":
            out.append(f'Added a new todo "{statement[1].strip()}".')
        elif kind in ("complete", "reopen"):
            out.append(f"Marked {names([title[i] for i in statement[1]])} as {'done' if kind == 'complete' else 'not done'}.")
        elif kind == "delete":
            out.append(f"Deleted {names([title[i] for i in statement[1]])}.")
        elif kind == "rename":
            out.append(f'Renamed "{title[statement[1]]}" to "{statement[2].strip()}".')
        elif kind == "complete all":
            out.append("Marked every todo as done.")
        elif kind == "reopen all":
            out.append("Marked every todo as not done.")
        elif kind == "delete completed":
            out.append("Deleted all finished todos.")
        else:
            out.append(f"Switched the view to show {VIEW[statement[1]]}.")
    return " ".join(out)


def spans(request):
    """Pieces of the request that could be the text of a todo: quoted strings and word-aligned tails."""
    quoted = re.findall(r'"([^"]+)"', request)
    words = request.rstrip(".!? ").split()
    tails = [" ".join(words[start:]).strip('":,') for start in range(len(words))]
    seen, out = set(), []
    for text in quoted + tails:
        if text and text not in seen and '"' not in text:
            seen.add(text)
            out.append(text)
    return quoted, out


def enumerate_programs(deck, state, request):
    """Single statements the Deck would accept now. Text arguments can only come from the request."""
    visible = deck.view(state)
    programs = ["unclear"]
    for record in visible:
        programs.append(f'{"reopen" if record["completed"] else "complete"} #{record["id"]}')
        programs.append(f'delete #{record["id"]}')
    programs += ["complete all", "reopen all"]
    if any(record["completed"] for record in state["todos"]):
        programs.append("delete completed")
    programs += [f"show {name}" for name in VIEW if name != state["filter"]]
    quoted, texts = spans(request)
    programs += [f'add "{text}"' for text in texts[:12]]
    titles = {record["title"] for record in state["todos"]}
    programs += [f'rename #{record["id"]} "{text}"' for text in quoted if text not in titles for record in visible]
    return [program for program in programs if compile_program(deck, state, parse(program)) is not None]
