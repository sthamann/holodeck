"""Live demo: say what you want, see the exact list it leads to, confirm, and the Host commits it atomically."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import random
import sys
from tempfile import mkdtemp
from threading import Lock

import torch

from adapter import Adapter, load_backbone
from deck import Deck
from intent import compile_program, outcome, parse
from tasks import relabel

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "model-applications" / "vertical"))

from runtime import Host  # noqa: E402
from storage import Journal  # noqa: E402

START = ["buy milk", "call the dentist", "pay the rent", "book flights to Lisbon", "water the plants"]


def preview(before, after):
    """Rows of the list after the plan, each marked as new, changed or the same, then the rows that go away."""
    old = {record["id"]: record for record in before["todos"]}
    rows = [{**record, "change": "new" if record["id"] not in old else
             "changed" if old[record["id"]] != record else "same"} for record in after["todos"]]
    kept = {record["id"] for record in after["todos"]}
    return rows + [{**record, "change": "removed"} for record in before["todos"] if record["id"] not in kept]


class Demo:
    def __init__(self, backbone, checkpoint):
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        tokenizer, lm = load_backbone(backbone, device)
        self.deck = Deck()
        self.model = Adapter(tokenizer, lm, self.deck)
        self.model.load_state_dict(torch.load(checkpoint), strict=False)
        self.model.eval()
        self.lock, self.tickets, self.rng = Lock(), {}, random.Random(0)
        self.host = Host(Journal(Path(mkdtemp(prefix="holodeck-todomvc-")) / "journal"), self.deck)
        for number, title in enumerate(START):
            self.host.dispatch("add", "user", {"text": title}, f"seed-{number}", number)

    def state(self):
        snapshot = self.host.snapshot()
        fields = snapshot["fields"]
        return {"epoch": snapshot["epoch"], **self.deck.observe(fields),
                "rows": [{"id": record["id"], "title": record["title"], "completed": record["completed"]}
                         for record in self.deck.view(fields)]}

    def ask(self, request):
        snapshot = self.host.snapshot()
        task = {"state": snapshot["fields"], "request": request}
        readings = [task, relabel(self.rng, task), relabel(self.rng, task)]
        with self.lock:
            texts, tokens = self.model.write(readings)
        results = []
        for reading, text in zip(readings, texts):
            statements = parse(text)
            compiled = compile_program(self.deck, reading["state"], statements) if statements is not None else None
            results.append(None if compiled is None else outcome(compiled[1]))
        statements = parse(texts[0])
        compiled = compile_program(self.deck, snapshot["fields"], statements) if statements is not None else None
        answer = {"program": texts[0], "context_tokens": round(tokens), "epoch": snapshot["epoch"],
                  "readings_agree": all(result is not None and result == results[0] for result in results)}
        if compiled is None:
            return {**answer, "status": "refused"}
        actions, after = compiled
        if not actions:
            return {**answer, "status": "unclear" if texts[0] == "unclear" else "nothing to do"}
        ticket = f"plan-{len(self.tickets)}"
        self.tickets[ticket] = (actions, snapshot["epoch"])
        return {**answer, "status": "ready", "ticket": ticket, "steps": len(actions),
                "filter": after["filter"], "preview": preview(snapshot["fields"], after)}

    def confirm(self, ticket, key):
        actions, epoch = self.tickets[ticket]
        return self.host.dispatch_plan("user", actions, key, epoch)

    def act(self, body):
        return self.host.dispatch(body["action"], "user", body["args"], body["key"], body["epoch"])


def handler(demo):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, kind="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

        def do_GET(self):
            if self.path == "/state":
                self.send(demo.state())
            else:
                self.send((ROOT / "demo.html").read_bytes(), "text/html; charset=utf-8")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/ask":
                self.send(demo.ask(body["request"][:300]))
            elif self.path == "/confirm":
                self.send(demo.confirm(body["ticket"], body["key"]))
            elif self.path == "/act":
                self.send(demo.act(body))
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backbone")
    parser.add_argument("--checkpoint", default=str(ROOT.parents[1] / ".holodeck" / "todomvc-adapter.pt"))
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(Demo(args.backbone, args.checkpoint)))
    print(f"http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
