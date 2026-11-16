"""Loopback fixture server for the same Deck, scene stream and action contract."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

from compiler import canonical
from runtime import Host


PAGE = Path(__file__).with_name("holo.html").read_bytes()


def application(host, path, payload):
    if path == "/scene":
        return host.patches(payload["actor"], payload.get("previous"), payload.get("base"))
    if path == "/invoke":
        return host.invoke(payload["actor"], payload["handle"], payload["key"])
    if path == "/act":
        return host.dispatch(payload["action"], payload["actor"], payload["args"], payload["key"], payload["epoch"])
    raise KeyError(path)


def create_server(host: Host, verifier=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?", 1)[0] != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            page = PAGE
            if verifier:
                page = page.replace(b"</html>", b'<script type="module">' + verifier + b'</script></html>')
            self.wfile.write(page)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length < 65536:
                    raise ValueError("invalid body length")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("expected an object")
                result = application(host, self.path, payload)
                if self.path == "/scene":
                    # Resolve the first patch before sending headers, so malformed requests fail cleanly.
                    first = next(result)
                else:
                    first = None
            except (TypeError, ValueError, KeyError, AttributeError):
                self.send_error(400)
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson" if first else "application/json")
            self.end_headers()
            if first:
                self.write_line(first)
                for patch in result:
                    self.write_line(patch)
            else:
                self.write_line(result)

        def write_line(self, event):
            self.wfile.write((canonical(event) + "\n").encode())
            self.wfile.flush()

        def log_message(self, *_):
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
