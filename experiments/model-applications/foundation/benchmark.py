"""Causal placement and executable plan acceptance for a frozen foundation checkpoint."""

import argparse
from dataclasses import dataclass
import hashlib
from itertools import product
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np

from llama import TinyLlama


HERE = Path(__file__).resolve().parent
VERTICAL = HERE.parent / "vertical"
sys.path.insert(0, str(VERTICAL))

from compiler import Model, compile_contract, require
from replay import reference
from runtime import Host, preview_plan
from storage import Relational


LAYERS = (1, 6, 12)
REVISION = "fe8a4ea1ffedaf415f4da2f062534de366a451e6"
TINYLLAMA_SHA = "6e6001da2106d4757498752a021df6c2bdc332c650aae4bae6b0c004dcf14933"
LFM2_SHA = "112fba7db6c4544d9944002fcfe1c21e23e03d8fc0a0c7e2b6655de6c738583d"
PARAPHRASES = {
    "capacity": (
        "Book and finish the largest allowed reservation.",
        "Use as much of the capacity as the rules permit, and settle it.",
        "I want the greater amount, provided the entire plan can complete.",
    ),
    "approval": (
        "Archive it now if the full plan is allowed, otherwise wait.",
        "Try to close the record. Leave it alone if closure would fail.",
        "Prefer a finished archive when authorized, with no change otherwise.",
    ),
    "alert": (
        "Archive the case and clear its notification if possible.",
        "Close the case and leave the alert switched off.",
        "I'd like it archived without a lasting notification.",
    ),
    "deadline": (
        "Finish the existing hold now if permitted, otherwise make no changes.",
        "Settle this reservation if it is still valid. Wait if it is expired.",
        "Try to complete the hold, and leave it alone if the deadline prevents that.",
    ),
}


@dataclass(frozen=True)
class Option:
    description: str
    utility: int
    steps: list


@dataclass(frozen=True)
class Case:
    key: str
    family: str
    contract: str
    checkpoint: dict
    state: dict
    actor: str
    request: str
    choices: tuple
    pair: str


def action(name, **args):
    return {"action": name, "args": args}


def cases(checkpoints):
    capacity = lambda units: Option(f"Hold and settle {units} units", units, [
        action("hold", units=units), action("settle"),
    ])
    wait = Option("Make no changes", 0, [])
    seal = Option("Archive and keep the alert active", 1, [action("seal")])
    silence = Option("Archive and clear the alert", 2, [action("seal"), action("dismiss")])
    finish = Option("Settle the existing hold", 1, [action("settle")])
    scenarios = [
        ("capacity", "allocation", "member", {}, (capacity(1), capacity(3)), "revision"),
        ("capacity", "allocation", "member", {}, (capacity(2), capacity(3)), "revision"),
        ("approval", "quarantine", "reviewer", {"samples": 1}, (seal, wait), "revision"),
        ("approval", "quarantine", "reviewer", {"samples": 2}, (seal, wait), "revision"),
        ("alert", "quarantine", "reviewer", {"samples": 2}, (seal, silence), "revision"),
        ("deadline", "allocation", "member",
         {"available": 2, "pending": 2, "pin": 1, "expires": 2}, (finish, wait), "clock"),
    ]
    for number, config in enumerate(scenarios):
        family, contract, actor, fields, options, intervention = config
        checkpoint = checkpoints[contract]
        initial = {name: triplet[2] for name, triplet in checkpoint["fields"].items()}
        for (variant, reverse, wording) in product((1, 2), (False, True), range(3)):
            pair = f"{number}:{int(reverse)}:{wording}"
            yield Case(
                f"{pair}:{variant}", family, contract, checkpoint,
                {"revision": variant if intervention == "revision" else 1,
                 "fields": {**initial, **fields, **({"clock": 1 if variant == 1 else 3}
                                                   if intervention == "clock" else {})}},
                actor, PARAPHRASES[family][wording], options[::-1] if reverse else options, pair,
            )


def expected_outputs(case):
    def hand_reference(state, action, actor, args):
        return reference(case.contract, state, action, actor, args)
    return [preview_plan(hand_reference, case.state, case.actor, option.steps) for option in case.choices]


def exact_outputs(case):
    model = Model(case.checkpoint)
    return [preview_plan(model.propose, case.state, case.actor, option.steps) for option in case.choices]


def token_id(tokenizer, string):
    ids = tokenizer.encode(string, bos=False)
    require(len(ids) == 1, f"wanted one token for {string}: {ids}")
    return ids[0]


def prompt(case, evidence):
    first, second = case.choices
    a, b = evidence
    return (
        "Choose one plan for a user. An allowed plan is one the application can complete. "
        "Among allowed plans choose the one that best answers the request.\n"
        f"Request: {case.request}\n"
        f"A: {first.description}. Allowed: {a}.\n"
        f"B: {second.description}. Allowed: {b}.\n"
        "Answer A or B. Answer:"
    )


def prepare(model, group):
    tok = model.tokenizer
    maybe = token_id(tok, "maybe")
    flag_ids = (token_id(tok, "no"), token_id(tok, "yes"))
    rows, filled, offsets, bits, positions = [], [], [], [], []
    for case in group:
        valid = tuple(result is not None for result in exact_outputs(case))
        markers = tok.encode(prompt(case, ("maybe", "maybe")))
        slots = [index for index, value in enumerate(markers) if value == maybe]
        require(len(slots) == 2, "semantic placeholders did not map to two tokens")
        actual = tok.encode(prompt(case, tuple("yes" if v else "no" for v in valid)))
        rebuilt = markers.copy()
        for index, bit in zip(slots, valid):
            rebuilt[index] = flag_ids[bit]
        require(actual == rebuilt, "tool rendering changed token boundaries or positions")
        rows.append(markers)
        filled.append(actual)
        offsets.append(slots)
        bits.append(valid)
        positions.append(len(markers) - 1)
    width = max(map(len, rows))
    right_pad = lambda seqs: np.array([row + [2] * (width - len(row)) for row in seqs], dtype=np.int32)
    return right_pad(rows), right_pad(filled), offsets, bits, positions, flag_ids


def learn_directions(model, targets):
    """Measure yes/no replacement vectors in unrelated calibration contexts."""
    examples = (
        "An operator checked the gate. Passed:",
        "Meter report. Connection is available:",
        "A record has a valid signature. Confirmed:",
    )
    tok = model.tokenizer
    vectors = {layer: {False: [], True: []} for layer in LAYERS}
    for example in examples:
        ids = [tok.encode(example + " " + value) for value in ("maybe", "no", "yes")]
        require(len({len(row) for row in ids}) == 1 and all(row[:-1] == ids[0][:-1] for row in ids),
                "calibration prefix drift")

        def capture(layer, residual):
            if layer in vectors:
                for position, bit in ((1, False), (2, True)):
                    vectors[layer][bit].append(residual[position, -1] - residual[0, -1])

        model.forward(ids, targets, edit=capture)
    return {layer: {bit: np.mean(rows, axis=0) for bit, rows in direction.items()}
            for layer, direction in vectors.items()}


def run_foundation(model, suite, batch_size):
    targets = [token_id(model.tokenizer, symbol) for symbol in ("A", "B")]
    directions = learn_directions(model, targets)
    outputs = []
    max_delta = 0.0
    for offset in range(0, len(suite), batch_size):
        group = suite[offset:offset + batch_size]
        base, external, slots, bits, read, token_ids = prepare(model, group)
        scores = {
            "external": model.forward(external, targets, read=read),
            "blind": model.forward(base, targets, read=read),
        }

        def replace_from_model(residual):
            for row, case in enumerate(group):
                proposals = exact_outputs(case)
                for index, proposal in zip(slots[row], proposals):
                    residual[row, index] = model.embed[token_ids[proposal is not None]]

        scores["internal_input"] = model.forward(base, targets, replacement=replace_from_model, read=read)
        max_delta = max(max_delta, float(np.max(np.abs(scores["external"] - scores["internal_input"]))))
        for depth in LAYERS:
            def edit(layer, x):
                if layer == depth:
                    for row, case in enumerate(group):
                        proposals = exact_outputs(case)
                        for index, proposal in zip(slots[row], proposals):
                            x[row, index] += directions[depth][proposal is not None]
            scores[f"residual_{depth}"] = model.forward(base, targets, edit=edit, read=read)

        for row, case in enumerate(group):
            expected = expected_outputs(case)
            require(expected == exact_outputs(case), f"plan compiler divergence in {case.key}")
            winner = max((i for i, result in enumerate(expected) if result is not None),
                         key=lambda i: case.choices[i].utility)
            output = {"case": case.key, "family": case.family, "pair": case.pair,
                      "valid": bits[row], "winner": winner, "arms": {}}
            for arm, board in scores.items():
                logits = board[row]
                raw = int(np.argmax(logits))
                masked = int(np.argmax(np.where(bits[row], logits, -np.inf)))
                output["arms"][arm] = {
                    "selected": raw, "masked": masked, "margin": round(float(logits[1] - logits[0]), 5),
                }
            outputs.append(output)
        print(f"scored {offset + len(group)}/{len(suite)}", file=sys.stderr, flush=True)
    require(max_delta == 0, "matched embeddings diverged numerically")
    return outputs, max_delta


def verify_commit(case, selected, root):
    expected = expected_outputs(case)[selected]
    model = Model(case.checkpoint)
    store = Relational(root)
    host = Host(store, model)
    snapshot = host.snapshot()
    snapshot.update(case.state)
    store.save(0, snapshot)
    result = host.dispatch_plan(case.actor, case.choices[selected].steps, "decision", 0)
    if expected is None:
        require(result == {"status": "refused"} and host.snapshot() == snapshot,
                "a refused plan leaked an effect")
        return "refused"
    require(result["status"] == "committed", "valid plan refused")
    effects = [[effect["kind"], effect["value"]] for effect in result["effects"]]
    require(effects == expected["effects"], "incorrect durable effects")
    require(host.snapshot()["fields"] == {**snapshot["fields"], **expected["writes"]}, "durable state diverged")
    retry = Host(Relational(root), model).dispatch_plan(case.actor, case.choices[selected].steps, "decision", 0)
    require(retry == result, "atomic plan retry returned a different receipt")
    return "committed"


def summarize(suite, rows):
    summaries = {}
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for arm in rows[0]["arms"]:
            summary = {}
            for mode in ("selected", "masked"):
                accepted = correct = 0
                per_family = {}
                for case, output in zip(suite, rows):
                    chosen = output["arms"][arm][mode]
                    durable = verify_commit(case, chosen, root / f"{arm}-{mode}-{case.key}.sqlite")
                    accepted += int(durable == "committed")
                    hit = chosen == output["winner"]
                    correct += int(hit)
                    per_family[case.family] = per_family.get(case.family, 0) + int(hit)
                summary[mode] = {"exact": correct, "committed": accepted, "by_family": per_family}

            pairs = {}
            for output in rows:
                pairs.setdefault(output["pair"], []).append(output)
            informative = [pair for pair in pairs.values() if len({r["winner"] for r in pair}) == 2]
            summary["counterfactual_pairs"] = {
                "total": len(informative),
                **{mode: int(sum(all(r["arms"][arm][mode] == r["winner"] for r in pair)
                                 for pair in informative)) for mode in ("selected", "masked")},
            }
            summaries[arm] = summary
    return summaries


def verify_atomic_negative(case):
    with TemporaryDirectory() as directory:
        host = Host(Relational(Path(directory) / "store"), Model(case.checkpoint))
        state = host.snapshot()
        plan = [action("hold", units=2), action("settle"), action("settle")]
        require(host.dispatch_plan(case.actor, plan, "partial", 0) == {"status": "refused"}
                and host.snapshot() == state, "a late refusal leaked an earlier valid effect")


def trace_sha(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--batch", type=int, default=6)
    parser.add_argument("--limit", type=int)
    arguments = parser.parse_args()
    require(arguments.batch > 0, "invalid batch size")
    contracts = json.loads((VERTICAL / "contracts.json").read_text())
    compiled = {name: compile_contract(spec) for name, spec in contracts.items()}
    suite = list(cases(compiled))[:arguments.limit]
    require(bool(suite), "empty suite")
    verify_atomic_negative(suite[0])

    weights_hash = trace_sha(arguments.snapshot / "model.safetensors")
    require(weights_hash == TINYLLAMA_SHA, "expected the pinned TinyLlama artifact")
    model = TinyLlama(arguments.snapshot)
    tok = model.tokenizer
    sanity_prompt = tok.encode("The capital of France is")
    sanity_targets = [token_id(tok, word) for word in ("Paris", "London")]
    dense = model.forward([sanity_prompt], sanity_targets)
    _, prefix = model.forward([sanity_prompt[:-2]], sanity_targets, keep_cache=True)
    cached = model.forward([sanity_prompt[-2:]], sanity_targets, cache=prefix)
    cache_delta = float(np.max(np.abs(cached - dense)))
    require(cache_delta < 1e-4 and dense[0, 0] > dense[0, 1], "checkpoint or cached forward sanity failed")
    print(f"loaded pretrained checkpoint; {len(suite)} cases", file=sys.stderr, flush=True)
    rows, delta = run_foundation(model, suite, arguments.batch)
    report = {
        "checkpoint": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        "snapshot": REVISION,
        "weights_sha256": weights_hash,
        "cases": len(rows), "same_embedding_max_logit_delta": delta,
        "cached_dense_sanity_max_delta": cache_delta,
        "atomic_refusal_negative_control": "passed",
        "results": summarize(suite, rows),
        "rows": rows,
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
