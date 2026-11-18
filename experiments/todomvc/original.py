"""Record what the original TodoMVC does, in a real browser, for generated action sequences.

    python original.py /path/to/todomvc/examples/javascript-es5/dist

Open http://127.0.0.1:8770/__harness in a browser. The harness loads the untouched application in a
frame, performs every sequence through DOM events a person would cause, and posts what is visible
after each step. The result is written to traces.json next to this file.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import random
import subprocess

ROOT = Path(__file__).resolve().parent
TEXTS = ["buy milk", "call mum", "  pay rent  ", "write report", "book flights", "water plants", "", "   ",
         "\tfix bike\n", "a", "read <b>book</b>", "tea & cake", "buy milk", "renew passport", "ünïcödé ✓",
         "plan  trip", " gym ", "email \"Sam\"", "it's done"]
FILTERS = ["all", "active", "completed"]


def sequences(seed=1, count=400, length=14):
    """Action sequences over positions and texts. Positions may be out of range on purpose."""
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        steps = []
        for _ in range(length):
            kind = rng.choices(["add", "toggle", "destroy", "edit", "edit_cancel", "toggle_all",
                                "clear_completed", "show"], [5, 4, 2, 3, 1, 1, 1, 2])[0]
            if kind == "add":
                steps.append(["add", {"text": rng.choice(TEXTS)}])
            elif kind in ("toggle", "destroy"):
                steps.append([kind, {"row": rng.randrange(5)}])
            elif kind in ("edit", "edit_cancel"):
                steps.append([kind, {"row": rng.randrange(5), "text": rng.choice(TEXTS)}])
            elif kind == "show":
                steps.append(["show", {"filter": rng.choice(FILTERS)}])
            else:
                steps.append([kind, {}])
        out.append(steps)
    return out


def handler(dist, plan):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, kind):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0].split("#")[0]
            if path == "/__harness":
                self.send((ROOT / "harness.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/__plan":
                self.send(json.dumps(plan).encode(), "application/json")
            else:
                file = (dist / (path.lstrip("/") or "index.html")).resolve()
                if dist not in file.parents or not file.is_file():
                    self.send_error(404)
                    return
                kind = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}
                self.send(file.read_bytes(), kind.get(file.suffix, "application/octet-stream"))

        def do_POST(self):
            traces = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            commit = subprocess.run(["git", "-C", str(dist), "rev-parse", "HEAD"], capture_output=True, text=True)
            record = {"source": "https://github.com/tastejs/todomvc", "commit": commit.stdout.strip(),
                      "example": "examples/javascript-es5/dist", "sequences": len(plan), "traces": traces}
            (ROOT / "traces.json").write_text(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.send(b"{}", "application/json")
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dist")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(Path(args.dist).resolve(), sequences()))
    print(f"http://127.0.0.1:{args.port}/__harness", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
