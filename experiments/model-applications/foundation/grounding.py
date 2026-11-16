"""Fit only a readout over frozen activations. Select all hyperparameters on development text."""

import numpy as np


GOAL_NAMES = ("time", "price", "unknown")
LAYERS = (4, 8, 12, 16, 22)
TRAIN = {
    "time": (
        "Pick the quicker one.", "Speed is the only thing I care about.", "Meet the earliest possible finish.",
        "Minimize the duration in hours.", "I am willing to spend more to get this right away.",
        "Ignore the charge, this is urgent.", "Choose for the fewest minutes of waiting.",
        "Take whichever alternative is fastest.", "I do not care about money, but I need the earliest end.",
        "Get it there in the smallest amount of time.", "The main objective is low latency.",
        "I am in a rush. Finish promptly, regardless of the bill.",
    ),
    "price": (
        "Pick the cheaper one.", "Money is the only thing I care about.", "Find the lowest quote.",
        "Minimize the transaction charge.", "I can wait longer if the bill is lower.",
        "Ignore the schedule; this is a tight budget.", "Choose for the least money paid.",
        "Take whichever alternative is cheapest.", "I do not care about waiting, but keep the fee low.",
        "Get it done for the smallest amount of money.", "The main objective is low expense.",
        "There is no rush. Do it frugally, regardless of lead time.",
    ),
    "unknown": (
        "I have not decided what matters most.", "I care about both fee and speed.",
        "Give me the tradeoff and let me decide.", "Find an option, and ask if the options conflict.",
        "I haven't said my priorities yet.", "Do whatever is reasonable, but ask about a compromise.",
        "Quicker and cheaper would both be nice, with no preference for either.",
        "Tell me the difference between the alternatives before committing.",
        "Show me the two possible outcomes so I can pick.",
        "Neither objective takes priority until I know the details.",
        "My preference here is undecided.", "Choose only if a single option wins under either priority.",
    ),
}

# Opened only for scoring after the readout and development selection are set.
TRANSFER = {
    "time": (
        "Have it back in the fewest hours you can.", "Getting this over with takes precedence over the fee.",
        "No need to economize on this one. Cut the wait.", "The first finish is worth the extra charge.",
        "I would trade additional money for a shorter turnaround.", "Just finish it at the earliest opportunity.",
        "Even if the inexpensive route is available, don't make me wait.", "The only thing that counts is when it will be ready.",
    ),
    "price": (
        "Keep the spend down and give it as long as it needs.", "I'd rather wait than add to the invoice.",
        "The lowest outlay takes precedence here.", "Don't pay a premium for a quicker result.",
        "Can you get this done without spending any more than necessary?", "Every penny counts on this request.",
        "A later completion is acceptable if we trim the bill.", "Make the charge as small as possible.",
    ),
    "unknown": (
        "I could go either way; show me where the choice changes.", "Present the tradeoff if neither is an obvious winner.",
        "I want something quick and economical, with neither first.", "I didn't give you a rule for resolving the tradeoff.",
        "Let me decide if you would have to sacrifice speed for money.", "I am not sure which criterion should win.",
        "Show me one best choice only if it is the best by either measure.", "If one comes sooner for a higher charge, check with me.",
    ),
}


def features(model, utterances, batch_size=6):
    available = tuple(layer for layer in LAYERS if layer <= len(model.layers))
    values = {(layer, pool): [] for layer in available for pool in ("mean", "last")}
    for offset in range(0, len(utterances), batch_size):
        batch = utterances[offset:offset + batch_size]
        ids = [model.tokenizer.encode(text) for text in batch]
        length = max(map(len, ids))
        padded = [row + [2] * (length - len(row)) for row in ids]

        def observe(layer, x):
            if layer in available:
                body = [states[1:len(row)] for states, row in zip(x, ids)]
                values[layer, "mean"].extend(np.mean(states, axis=0) for states in body)
                values[layer, "last"].extend(states[-1].copy() for states in body)

        model.forward(padded, [2], edit=observe)
    return {key: np.stack(rows).astype(np.float64) for key, rows in values.items()}


def normalize(x):
    return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-9)


def fit(x, labels, penalty):
    mean = x.mean(axis=0)
    x = normalize(x - mean)
    y = np.eye(len(GOAL_NAMES))[labels]
    coefficient = np.linalg.solve(x @ x.T + penalty * np.eye(len(x)), y)
    return mean, x.T @ coefficient


def score(data, model):
    mean, coefficient = model
    return normalize(data - mean) @ coefficient


def train_readout(model, development=TRAIN):
    examples = [(goal, text) for goal in GOAL_NAMES for text in development[goal]]
    observed = features(model, [text for _, text in examples])
    labels = np.array([GOAL_NAMES.index(goal) for goal, _ in examples])
    folds = np.tile(np.arange(len(development["time"])) % 4, len(GOAL_NAMES))
    scored = []
    for key, matrix in observed.items():
        for penalty in (0.01, 0.1, 1.0):
            losses = 0
            for fold in range(4):
                train = folds != fold
                learned = fit(matrix[train], labels[train], penalty)
                predicted = np.argmax(score(matrix[~train], learned), axis=1)
                losses += int(np.sum(predicted != labels[~train]))
            scored.append((losses, key[0], key[1], penalty))
    losses, layer, pool, penalty = min(scored)
    key = (layer, pool)
    learned = fit(observed[key], labels, penalty)
    return learned, key, {"layer": layer, "pool": pool, "penalty": penalty,
                          "development_cv_exact": len(examples) - losses, "development_examples": len(examples)}


def readout_scores(model, readout, key, utterances):
    return score(features(model, utterances)[key], readout)
