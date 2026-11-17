"""Live demo: type a request, the reader states the goal and streams a Holo, a click commits on the durable Host."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import mkdtemp
from threading import Lock

import torch

import holo
from fusion import load_backbone
from reader import ReaderArm
from worlds import authored, build

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "model-applications" / "vertical"))

from runtime import Host  # noqa: E402
from storage import Journal  # noqa: E402

SIGNS = {"==": "=", ">=": "≥", "<=": "≤"}


class Demo:
    def __init__(self, backbone, checkpoint):
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        tokenizer, lm = load_backbone(backbone, device)
        self.model = ReaderArm(tokenizer, lm)
        self.model.load_state_dict(torch.load(checkpoint), strict=False)
        self.model.eval()
        self.lock, self.root = Lock(), Path(mkdtemp(prefix="holodeck-demo-"))
        hand = authored()
        unseen = build(7, 3, "test")
        self.worlds = {"allocation": hand[0][0], "quarantine": hand[1][0],
                       **{f"generated-{index + 1}": world for index, (world, _) in enumerate(unseen)}}
        self.hosts, self.generation = {}, 0
        for name in self.worlds:
            self.reset(name)

    def reset(self, name):
        self.generation += 1
        self.hosts[name] = Host(Journal(self.root / f"{name}-{self.generation}"), self.worlds[name].model)

    def apps(self):
        return [{"id": name, "actors": list(world.spec["actors"]),
                 "actions": [rule["label"] for rule in world.spec["actions"].values()]}
                for name, world in self.worlds.items()]

    def task(self, name, actor, request):
        world, snapshot = self.worlds[name], self.hosts[name].snapshot()
        state = [snapshot["fields"][field] for field in world.names]
        return world, {"actor": actor, "state": state, "request": request,
                       "legal": world.legal(state, actor)}, snapshot

    def holo(self, name, actor, request):
        world, task, snapshot = self.task(name, actor, request)
        with self.lock:
            scenes, output = self.model.decode([(world, task)])
        goal = output["goal_texts"][0]
        for sign, shown in SIGNS.items():
            goal = goal.replace(sign, shown)
        yield {"understood": goal, "checked": output["agree"][0], "context_tokens": round(output["tokens"]),
               "state": snapshot["fields"], "epoch": snapshot["epoch"]}
        scene = holo.tree(scenes[0], world, task, snapshot["epoch"])
        stated = output["goals"][0]
        if stated and world.satisfied(task["state"], stated):
            for node in scene:
                if node["kind"] == "notice":
                    node["text"] = "This request is already fulfilled."
        yield from holo.patches(scene, snapshot["epoch"])

    def invoke(self, name, handle, key):
        return self.hosts[name].invoke(handle["actor"], handle, key)


def handler(demo):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, kind="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/apps":
                self.send(json.dumps(demo.apps()).encode())
            else:
                self.send((ROOT / "demo.html").read_bytes(), "text/html; charset=utf-8")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/holo":
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for patch in demo.holo(body["app"], body["actor"], body["request"][:300]):
                    self.wfile.write((json.dumps(patch) + "\n").encode())
                    self.wfile.flush()
            elif self.path == "/invoke":
                self.send(json.dumps(demo.invoke(body["app"], body["handle"], body["key"])).encode())
            elif self.path == "/reset":
                demo.reset(body["app"])
                self.send(b"{}")
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backbone")
    parser.add_argument("--checkpoint", default=str(ROOT.parents[1] / ".holodeck" / "reader.pt"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(Demo(args.backbone, args.checkpoint)))
    print(f"http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
