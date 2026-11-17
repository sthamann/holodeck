"""Independent contract checks and end-to-end fault and migration replay."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from itertools import product
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Thread

from compiler import Model, compile_contract, canonical, require
from runtime import ExternalTools, Host, Interrupted
from serve import application, create_server
from storage import Journal, Receiver, Relational, migrate


ROOT = Path(__file__).parent
ADAPTERS = {"journal": Journal, "sqlite": Relational}


def reference(name, state, action, actor, args):
    data = state["fields"]
    revision = state["revision"]
    result = None
    effects = []
    if name == "allocation":
        available, pending, clock = (data[key] for key in ("available", "pending", "clock"))
        expires, allowed, pinned = (data[key] for key in ("expires", "authorized", "pin"))
        member = actor == "member"
        if action == "hold" and member and allowed and not pending and clock <= 7:
            units = args["units"]
            if available - units >= revision:
                result = dict(available=available - units, pending=units, pin=revision, expires=clock + 2)
                effects = [["held", units]]
        if action == "settle" and member and allowed and pending and clock <= expires and available >= pinned:
            result = dict(pending=0, pin=0, expires=0)
            effects = [["reserve", pending], ["notify", pending]]
        if action == "release" and not member and pending and clock > expires:
            result = dict(available=available + pending, pending=0, pin=0, expires=0)
            effects = [["released", pending]]
        if action == "tick" and not member and clock < 9:
            result = dict(clock=clock + 1)
        if action == "revoke" and not member and allowed:
            result = dict(authorized=0)
    if name == "quarantine":
        samples, sealed, alert = (data[key] for key in ("samples", "sealed", "alert"))
        reviewer = actor == "reviewer"
        if action == "sample" and not reviewer and not sealed and samples < 3:
            result = dict(samples=samples + 1)
        if action == "seal" and reviewer and not sealed and samples >= revision:
            result = dict(sealed=1, alert=1)
            effects = [["archive", samples]]
        if action == "dismiss" and reviewer and alert:
            result = dict(alert=0)
            effects = [["dismissed", samples]]
    return None if result is None else {"writes": result, "effects": effects}


def check_reachable(name, model):
    checkpoint = model.checkpoint
    names = sorted(checkpoint["fields"])
    initial = (checkpoint["initial_revision"], tuple(checkpoint["fields"][key][2] for key in names))
    seen = {initial}
    frontier = [initial]
    count = 0

    def add(next_revision, next_fields):
        pair = (next_revision, tuple(next_fields[key] for key in names))
        if pair not in seen:
            seen.add(pair)
            frontier.append(pair)

    for revision, values in frontier:
        fields = dict(zip(names, values))
        state = {"revision": revision, "fields": fields}
        add(2, fields)
        for actor, (action, rule) in product(checkpoint["actors"], checkpoint["actions"].items()):
            items = sorted(rule.get("args", {}).items())
            for args in product(*(values for _, values in items)):
                parameters = dict(zip((key for key, _ in items), args))
                expected = reference(name, state, action, actor, parameters)
                actual = model.propose(state, action, actor, parameters)
                require(expected == actual, f"observable divergence: {name} {state} {actor} {action} {parameters}")
                count += 1
                if expected is not None:
                    add(revision, {**fields, **expected["writes"]})
    return {"reachable_states": len(seen), "checked_transitions": count}


def patches(host, actor, previous=None, base=None):
    scene = dict(previous or {})
    events = list(application(host, "/scene", {"actor": actor, "previous": scene, "base": base}))
    for event in events:
        require(event["base"] == base and event["epoch"] == events[-1]["epoch"], "broken patch sequence")
        if "drop" in event:
            del scene[event["drop"]]
        if "put" in event:
            key, node = event["put"]
            scene[key] = node
    require(events[-1].get("complete"), "missing patch completion")
    return scene, events[-1]["epoch"], len(events)


def holds(scene):
    return sorted(node["handle"]["args"]["units"] for node in scene.values()
                  if node["kind"] == "action" and node["handle"]["action"] == "hold")


def lost_process(checkpoint_path, adapter, path, mode, epoch):
    process = subprocess.run([sys.executable, __file__, "--crash", str(checkpoint_path), adapter,
                              str(path), mode, str(epoch)], capture_output=True, timeout=20)
    require(process.returncode == 37, f"did not reach crash point: {process.stderr.decode()}")


def run_case(root, checkpoint_path, storage_order, activation_first, via_tool):
    with checkpoint_path.open() as file:
        model = Model(json.load(file))
    tools = ExternalTools(model) if via_tool else None
    first, second = storage_order
    source_path = root / first
    target_path = root / second
    source = ADAPTERS[first](source_path)
    host = Host(source, model, tools)
    scene, epoch, initial_patches = patches(host, "member")
    require(holds(scene) == [1, 2, 3], "initial safe plans")
    old_handle = next(node["handle"] for node in scene.values()
                      if node.get("handle", {}).get("args") == {"units": 3})

    lost_process(checkpoint_path, first, source_path, "before", epoch)
    host = Host(ADAPTERS[first](source_path), model, tools)
    require(host.snapshot()["epoch"] == epoch and not host.snapshot()["outbox"], "partial commit survived")
    if first == "journal":
        with source_path.open("ab") as file:
            file.write(b'{"partial"')

    first_receipt = application(host, "/invoke", {"actor": "member", "handle": old_handle, "key": "hold"})
    require(first_receipt["status"] == "committed", "Holo hold failed")
    require(host.invoke("controller", old_handle, "impersonation") == {"status": "refused"}, "actor escaped")
    require(host.invoke("member", old_handle, "different-key") == {"status": "stale"}, "old Holo handle accepted")
    after_hold, after_epoch, delta_patches = patches(host, "member", scene, epoch)
    require(not holds(after_hold), "old hold controls survived")
    require(any(options["action"] == "settle" for options in host.agent_options("member")), "agent cannot settle")

    if activation_first:
        host.activate(2, after_epoch)
    source_state = host.snapshot()
    migrate(host.store, ADAPTERS[second](target_path))
    host = Host(ADAPTERS[second](target_path), model, tools)
    require(host.snapshot() == source_state, "migration changed observable state")
    if not activation_first:
        host.activate(2, after_epoch)
    saved = host.snapshot()
    require(saved["fields"]["pin"] == 1 and saved["revision"] == 2, "pending promise lost its pinned rule")

    lost_process(checkpoint_path, second, target_path, "after", saved["epoch"])
    host = Host(ADAPTERS[second](target_path), model, tools)
    second_receipt = application(host, "/act", {
        "action": "settle", "actor": "member", "args": {}, "key": "settle", "epoch": saved["epoch"],
    })
    require([effect["kind"] for effect in second_receipt["effects"]] == ["reserve", "notify"],
            "pinned effect order changed")
    require(host.dispatch("settle", "controller", {}, "settle", saved["epoch"]) == {"status": "key-conflict"},
            "idempotence key rebound")
    receiver = Receiver(root / "receiver.db")
    receiver.deliver(host.snapshot()["outbox"])
    Receiver(root / "receiver.db").deliver(host.snapshot()["outbox"])
    require(receiver.records() == host.snapshot()["outbox"], "redelivery duplicated an external effect")

    race_epoch = host.snapshot()["epoch"]
    barrier = Barrier(2)
    original_propose = host.propose

    def racing_propose(*args):
        result = original_propose(*args)
        barrier.wait(timeout=10)
        return result

    host.propose = racing_propose
    with ThreadPoolExecutor(2) as pool:
        races = list(pool.map(lambda key: host.dispatch("tick", "controller", {}, key, race_epoch)["status"],
                              ("race-1", "race-2")))
    host.propose = original_propose
    require(sorted(races) == ["committed", "stale"] and host.snapshot()["fields"]["clock"] == 1,
            "compare-and-swap did not prevent a lost update")

    new_store = ADAPTERS[second](root / "fresh")
    fresh = Host(new_store, model, tools)
    initial_options = holds(patches(fresh, "member")[0])
    fresh.activate(2, 0)
    changed_options = holds(patches(fresh, "member")[0])
    require(initial_options == [1, 2, 3] and changed_options == [1, 2], "single policy cell did not change Holo")
    require(fresh.dispatch("hold", "member", {"units": 3}, "impossible", 1) == {"status": "refused"},
            "API diverged from new policy")
    choice = max(fresh.agent_options("member"), key=lambda handle: handle["args"]["units"])
    require(choice["args"] == {"units": 2}, "agent diverged from new policy")
    require(fresh.invoke("member", choice, "new-hold")["effects"][0]["value"] == 2,
            "policy intervention did not produce the predicted effect")
    require(fresh.dispatch("revoke", "controller", {}, "revoke", 2)["status"] == "committed", "revoke failed")
    require(fresh.agent_options("member") == [], "revoked control remains available")

    return {
        "initial_patches": initial_patches, "hold_delta_patches": delta_patches,
        "old_options": initial_options, "new_options": changed_options,
        "effect_order": [effect["kind"] for effect in receiver.records()],
        "lost_commit_recovered": True, "duplicate_delivery_suppressed": True, "concurrent_writers": sorted(races),
        "external_tool_calls": tools.calls if tools else 0,
    }


def second_contract(root, model):
    host = Host(Relational(root / "quarantine.db"), model)
    observer = host.agent_options("observer")
    require([action["action"] for action in observer] == ["sample"], "wrong observer actions")
    host.invoke("observer", observer[0], "sample-1")
    first = host.agent_options("reviewer")
    require([action["action"] for action in first] == ["seal"], "single sample should satisfy policy 1")
    host.activate(2, 1)
    require(host.agent_options("reviewer") == [], "policy 2 should require another sample")
    require(host.invoke("reviewer", first[0], "stale-seal") == {"status": "stale"}, "accepted stale role control")
    host.invoke("observer", host.agent_options("observer")[0], "sample-2")
    second = host.agent_options("reviewer")
    receipt = host.invoke("reviewer", second[0], "seal")
    require(receipt["effects"] == [{"id": "seal:0", "kind": "archive", "value": 2}],
            "second contract emitted an incorrect effect")
    return True


def check_negative_controls(contracts, compiled):
    malformed = deepcopy(contracts["allocation"])
    malformed["actions"]["hold"]["guard"] = ["and", "available", "authorized"]
    try:
        compile_contract(malformed)
    except ValueError:
        pass
    else:
        raise AssertionError("integer operand accepted as Boolean")

    mutated = deepcopy(compiled["allocation"])
    mutated["policies"]["1"] = 2
    try:
        check_reachable("allocation", Model(mutated))
    except ValueError as error:
        require(str(error).startswith("observable divergence"), "mutation did not reach reference comparison")
    else:
        raise AssertionError("reference checker accepted a changed policy")


def browser_run(browser, model, root):
    host = Host(Journal(root / "browser-journal"), model)
    http = create_server(host, (ROOT / "browser_fixture.js").read_bytes())
    server = Thread(target=http.serve_forever, daemon=True)
    server.start()
    try:
        page = f"http://127.0.0.1:{http.server_port}/?actor=member&verify=1"
        completed = subprocess.run([
            browser, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
            "--dump-dom", "--virtual-time-budget=10000", f"--user-data-dir={root / 'chrome'}", page,
        ], capture_output=True, text=True, timeout=45, check=True)
        require('data-replay="passed"' in completed.stdout, "browser did not apply the streamed patch and commit")
        require(host.snapshot()["fields"]["pending"] > 0, "browser mutation was not committed")
    finally:
        http.shutdown()
        http.server_close()
        server.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Chrome/Chromium binary for the filesystem and streamed browser replay")
    parser.add_argument("--crash", nargs=5, metavar=("ARTIFACT", "ADAPTER", "PATH", "FAULT", "EPOCH"),
                        help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.crash:
        artifact, adapter, path, mode, epoch = options.crash
        host = Host(ADAPTERS[adapter](path), Model(json.loads(Path(artifact).read_text())))
        try:
            action, args = ("hold", {"units": 3}) if mode == "before" else ("settle", {})
            host.dispatch(action, "member", args, action, int(epoch), fault=mode)
        except Interrupted:
            os._exit(37)
        raise RuntimeError("fault point not reached")

    contracts = json.loads((ROOT / "contracts.json").read_text())
    compiled = {name: compile_contract(contract) for name, contract in contracts.items()}
    models = {name: Model(artifact) for name, artifact in compiled.items()}
    check_negative_controls(contracts, compiled)
    evidence = {"negative_controls": {"invalid_boolean_rejected": True, "changed_policy_refuted": True},
                "contract_checks": {name: check_reachable(name, model) for name, model in models.items()},
                "scenarios": {}}
    with TemporaryDirectory() as temp:
        root = Path(temp)
        checkpoint_path = root / "allocation.deck.json"
        checkpoint_path.write_text(canonical(compiled["allocation"]))
        for order, activation_first in product((("journal", "sqlite"), ("sqlite", "journal")), (False, True)):
            scenario = f"{order[0]}-to-{order[1]}-{'activate-first' if activation_first else 'migrate-first'}"
            cases = {}
            for via_tool in (False, True):
                key = "tool" if via_tool else "direct"
                case_root = root / f"{scenario}-{key}"
                case_root.mkdir()
                cases[key] = run_case(case_root, checkpoint_path, order, activation_first, via_tool)
            require({key: value for key, value in cases["tool"].items() if key != "external_tool_calls"} ==
                    {key: value for key, value in cases["direct"].items() if key != "external_tool_calls"},
                    "direct and external exact controls diverged")
            evidence["scenarios"][scenario] = {
                **cases["direct"], "external_tool_calls": cases["tool"]["external_tool_calls"],
                "direct_tool_parity": True,
            }
        evidence["same_host_second_contract"] = second_contract(root, models["quarantine"])
        if options.browser:
            browser_run(options.browser, models["allocation"], root)
            evidence["chromium_stream"] = "passed"
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
