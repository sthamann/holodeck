"""Local HTTP adapter for the deck and generic renderer."""

import argparse
from functools import partial
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from .neural_shop import NeuralDeck
from .runtime import Ledger, Refusal, digest, encode


HERE = Path(__file__).resolve().parent
ASSETS = HERE.parents[1] / "experiments/shopware/native/public/assets"
FILES = {"/": (HERE / "surface.html", "text/html; charset=utf-8")}
FILES.update({"/media/" + name: (ASSETS / name, "image/jpeg")
              for name in ("moka.jpg", "moka-pair.jpg", "press.jpg")})


def buyer_identity(token):
    return digest(["buyer", token])


def test_payment(work):
    if work["kind"] == "neural-test-payment":
        return "order_transaction.paid", {"order_id":work["order_id"]}
    if work["kind"] != "payment":
        raise ValueError("Unsupported adapter effect")
    outcome = {"test": "approved", "declined": "declined"}[work["method"]]
    return "payment.result", {"order_id": work["order_id"], "outcome": outcome}


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, ledger, admin_token, admin_password, **kwargs):
        self.ledger, self.admin_token, self.admin_password = ledger, admin_token, admin_password
        self.response_session = None
        super().__init__(*args, **kwargs)

    def actor(self, task):
        roles = self.ledger.deck.roles(task)
        if not roles:
            raise Refusal("Unbekannte Ansicht.")
        if roles == ("admin",):
            if not compare_digest(self.headers.get("X-Deck-Admin", ""), self.admin_token):
                raise PermissionError("Bitte im Admin anmelden.")
            return {"id":"merchant","role":"admin"}
        buyer = self.headers.get("X-Deck-Session", "")
        if not re.fullmatch(r"[a-f0-9]{32}", buyer):
            buyer = secrets.token_hex(16)
            self.response_session = buyer
        return {"id":buyer_identity(buyer),"role":"buyer"}

    def respond(self, status, data, mime="application/json; charset=utf-8"):
        if self.response_session and isinstance(data, dict):
            data = {**data, "session": self.response_session}
        body = encode(data).encode() if not isinstance(data, bytes) else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def execute(self):
        port = self.server.server_port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise PermissionError("Der Testshop ist nur lokal erreichbar.")
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if self.command == "GET" and url.path in FILES:
            file, mime = FILES[url.path]
            return self.respond(200,file.read_bytes(),mime)
        if self.command == "GET" and url.path == "/projection":
            task = query.get("task", ["storefront"])[0]
            actor = self.actor(task)
            page = int(query.get("page", ["0"])[0])
            args = {"q":query.get("q", [""])[0][:120], "page":max(0,min(page,500))}
            projection = self.ledger.deck.project(task,self.ledger.read(),actor,args)
            return self.respond(200,projection)
        if self.command == "GET" and url.path == "/health":
            return self.respond(200, {"checkpoint":self.ledger.deck.identity})
        if self.command != "POST" or url.path not in ("/admin-login","/command"):
            return self.respond(404,{"error":"Seite nicht gefunden."})
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            raise PermissionError("Diese Anfrage ist nicht zugelassen.")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 24_000 or self.headers.get_content_type() != "application/json":
            raise Refusal("Ungültige Anfrage.")
        body = json.loads(self.rfile.read(size))
        if not isinstance(body, dict):
            raise Refusal("Ungültige Anfrage.")
        if url.path == "/admin-login":
            password = body.get("password")
            if not isinstance(password, str) or not compare_digest(password.encode(),self.admin_password.encode()):
                raise PermissionError("Das Passwort stimmt nicht.")
            return self.respond(200,{"admin_session":self.admin_token})
        actor = self.actor(body["task"])
        args, event_id, event = body["args"], body["id"], body["event"]
        if not isinstance(args,dict) or not isinstance(event,str) or not re.fullmatch(r"[a-f0-9-]{32,36}",event_id):
            raise Refusal("Ungültige Anfrage.")
        result = self.ledger.apply(event,actor,args,event_id)
        self.ledger.settle(test_payment)
        return self.respond(200,result)

    def dispatch(self):
        try:
            self.execute()
        except PermissionError as error:
            self.respond(403,{"error":str(error)})
        except Refusal as error:
            self.respond(409,{"error":str(error)})
        except (ValueError, TypeError, KeyError):
            self.respond(400,{"error":"Die Eingaben konnten nicht verarbeitet werden."})

    do_GET = do_POST = dispatch


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port",type=int,default=18779)
    cli.add_argument("--database",type=Path,default=None)
    cli.add_argument("--checkpoint",type=Path,default=HERE/"weights.npz")
    cli.add_argument("--merchant",type=Path,default=HERE/"merchant.json")
    cli.add_argument("--admin-password",default="deck-demo")
    args = cli.parse_args()
    deck = NeuralDeck(args.checkpoint,args.merchant)
    database = args.database or Path(".holodeck/shop-neural.sqlite")
    database.parent.mkdir(parents=True,exist_ok=True)
    ledger = Ledger(database,deck,deck.initial)
    ledger.settle(test_payment)
    handler = partial(Handler, ledger=ledger, admin_token=secrets.token_hex(32),admin_password=args.admin_password)
    with ThreadingHTTPServer(("127.0.0.1",args.port),handler) as server:
        print(f"Holoshop http://127.0.0.1:{server.server_port} · {deck.identity[:12]}",flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
