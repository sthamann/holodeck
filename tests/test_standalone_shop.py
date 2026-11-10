"""Acceptance tests against the same HTTP adapter used by the generated holos."""

from copy import deepcopy
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from holodeck.runtime import Deck, Ledger, Refusal, digest, encode
from holodeck.server import Handler, HERE, buyer_identity, test_payment


class Client:
    def __init__(self, base):
        self.base, self.headers = base, {}

    def call(self, path, body=None, expected=200):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data,
                          headers={"Content-Type":"application/json", **self.headers})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.load(response)
            if response.status != expected:
                raise AssertionError((path,response.status,result))
        if "session" in result:
            self.headers["X-Deck-Session"] = result["session"]
        if "admin_session" in result:
            self.headers["X-Deck-Admin"] = result["admin_session"]
        return result

    def view(self, task):
        return self.call("/projection?task=" + task)

    def command(self, event, args, *, task="storefront", event_id=None, expected=200):
        return self.call("/command", {"event":event, "args":args, "task":task,
                                      "id":event_id or uuid4().hex}, expected)

    def login(self):
        self.call("/admin-login", {"password":"fixture-password"})


class StandaloneShop(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "store.sqlite"
        self.deck = Deck(HERE / "checkpoint.json")
        self.start()
        self.addCleanup(self.stop)
        self.buyer = Client(self.base)
        self.admin = Client(self.base)
        self.admin.login()

    def start(self):
        self.ledger = Ledger(self.database,self.deck,self.deck.model["initial"])
        # Same handler and process-start reconciliation as the command-line app.
        self.ledger.settle(test_payment)
        handler = partial(Handler,ledger=self.ledger,admin_token=uuid4().hex,
                          admin_password="fixture-password")
        self.httpd = ThreadingHTTPServer(("127.0.0.1",0),handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever,daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def fresh_quote(self, client=None):
        client = client or self.buyer
        view = client.view("checkout")
        panel = next(node for node in view["nodes"] if node["title"] == "Lieferung und Testzahlung")
        return {**panel["actions"][0]["args"],"name":"Mira Test","email":"mira@example.test",
                "address":"Testweg 3","method":"test","consent":True}

    def test_customer_checkout_merchant_completion_and_restore(self):
        first = self.buyer.view("storefront")
        self.assertEqual([node["component"] for node in first["nodes"]],["products"])
        self.assertEqual(len(first["nodes"][0]["data"]),3)
        self.buyer.command("cart.set",{"sku":"moka-3","quantity":1})
        opened = self.buyer.view("storefront")
        self.assertNotEqual(first["projection"],opened["projection"])
        self.assertEqual([node["component"] for node in opened["nodes"]],["products","lines"])
        self.buyer.command("cart.promo",{"code":"KAFFEE10"},task="checkout")
        args = self.fresh_quote()
        totals = next(node["data"] for node in self.buyer.view("checkout")["nodes"] if node["component"]=="totals")
        self.assertEqual((totals["goods"],totals["shipping"],totals["gross"],totals["tax"]),(3141,490,3631,580))
        self.buyer.command("checkout.place",{**args,"consent":False},task="checkout",expected=409)
        event_id = uuid4().hex
        receipt = self.buyer.command("checkout.place",args,task="checkout",event_id=event_id)
        self.assertEqual(self.buyer.command("checkout.place",args,task="checkout",event_id=event_id),receipt)
        self.buyer.command("checkout.place",{**args,"name":"Andere Angabe"},
                           task="checkout",event_id=event_id,expected=409)
        order_id = receipt["result"]["order_id"]
        self.assertEqual(receipt["trace"]["checkpoint"],self.deck.identity)
        self.assertEqual(receipt["trace"]["graph"],digest(self.deck.model["actions"]["checkout.place"]))
        self.assertEqual(len(self.ledger.read()["orders"]),1)
        self.assertEqual(self.ledger.read()["catalog"]["moka-3"]["stock"],7)
        paid = self.buyer.view("order")["nodes"][0]["data"]
        self.assertEqual((paid["id"],paid["payment"],paid["stage"]),(order_id,"paid","ready"))
        self.assertTrue(paid["consent"]["confirmed"])
        self.assertEqual(Client(self.base).view("order")["nodes"][0]["component"],"empty")
        self.buyer.call("/projection?task=admin",expected=403)
        self.buyer.command("order.ship",{"order_id":order_id,"tracking":"D-01"},expected=409)
        admin_order = self.admin.view("admin")["nodes"][0]["data"][0]
        self.assertEqual(admin_order["id"],order_id)
        session = self.buyer.headers["X-Deck-Session"]
        self.assertNotIn(session,encode(admin_order))
        self.assertNotIn("buyer",admin_order)
        self.assertNotEqual(self.ledger.read()["orders"][order_id]["buyer"],session)
        self.assertEqual({a["event"] for a in admin_order["actions"]},{"order.ship","order.cancel"})
        self.admin.command("order.ship",{"order_id":order_id,"tracking":"D-01"},task="admin")
        self.stop()
        self.start()
        self.buyer.base = self.admin.base = self.base
        self.admin.login()
        self.assertEqual(self.buyer.view("order")["nodes"][0]["data"]["tracking"],"D-01")
        self.admin.command("order.complete",{"order_id":order_id},task="admin")
        self.assertEqual(self.buyer.view("order")["nodes"][0]["data"]["stage"],"completed")
        self.admin.command("order.return",{"order_id":order_id,"inspection":"resellable"},task="admin")
        returned = self.buyer.view("order")["nodes"][0]["data"]
        self.assertEqual((returned["stage"],returned["payment"]),("returned","refunded"))
        self.assertEqual(self.ledger.read()["catalog"]["moka-3"]["stock"],8)

    def test_empty_stale_and_declined_orders(self):
        self.buyer.view("checkout")
        actor = {"id":buyer_identity(self.buyer.headers["X-Deck-Session"]),"role":"buyer"}
        empty_key = digest(self.deck.context(self.ledger.read(),actor,{},"")["quote"])
        self.buyer.command("checkout.place",{"name":"Mira Test","email":"mira@example.test",
                           "address":"Testweg 3","method":"test","consent":True,
                           "quote_key":empty_key},task="checkout",expected=409)
        self.buyer.command("cart.set",{"sku":"moka-3","quantity":1})
        stale = self.fresh_quote()
        edit = {"sku":"moka-3","price":3690,"stock":7,"expected_price":3490,"expected_stock":8}
        self.admin.command("catalog.edit",edit,task="admin")
        self.admin.command("catalog.edit",edit,task="admin",expected=409)
        self.buyer.command("checkout.place",stale,task="checkout",expected=409)
        self.buyer.command("checkout.place",{**self.fresh_quote(),"method":"declined"},task="checkout")
        declined = self.buyer.view("order")["nodes"][0]["data"]
        self.assertEqual((declined["stage"],declined["payment"]),("payment_failed","failed"))
        self.assertEqual(self.ledger.read()["catalog"]["moka-3"]["stock"],7)

    def test_pending_effect_survives_restart_and_model_is_pinned(self):
        self.buyer.view("storefront")
        self.buyer.command("cart.set",{"sku":"moka-3","quantity":1})
        actor = {"id":buyer_identity(self.buyer.headers["X-Deck-Session"]),"role":"buyer"}
        receipt = self.ledger.apply("checkout.place",actor,self.fresh_quote(),uuid4().hex)
        order_id = receipt["result"]["order_id"]
        self.assertEqual(self.ledger.read()["orders"][order_id]["stage"],"pending")
        self.stop()
        self.start()
        self.buyer.base = self.base
        self.assertEqual(self.buyer.view("order")["nodes"][0]["data"]["payment"],"paid")

        modified = deepcopy(self.deck.model)
        modified["actions"].pop("order.ship")
        file = Path(self.temp.name) / "ablated.json"
        file.write_text(json.dumps(modified))
        ablated = Deck(file)
        with self.assertRaisesRegex(ValueError,"different deck checkpoint"):
            Ledger(self.database,ablated,modified["initial"])
        snapshot = self.ledger.read()
        view = ablated.project("admin",snapshot,{"id":"merchant","role":"admin"},{})
        self.assertNotIn("order.ship",[a["event"] for a in view["nodes"][0]["data"][0]["actions"]])
        with self.assertRaises(Refusal):
            ablated.action("order.ship",snapshot,{"id":"merchant","role":"admin"},
                           {"order_id":order_id,"tracking":"D-01"},uuid4().hex)

    def test_large_order_list_uses_a_bounded_live_projection(self):
        state = self.ledger.read()
        for i in range(1,514):
            key = f"test-{i}"
            state["orders"][key] = {"id":key,"sequence":i,"name":"Mira Test",
                                    "stage":"completed","payment":"paid"}
        with self.ledger.connect() as db:
            db.execute("UPDATE business SET state=? WHERE id=1",(encode(state),))
        page = self.admin.view("admin")["nodes"][0]
        self.assertEqual((page["total"],len(page["data"]),page["data"][0]["id"]),(513,20,"test-513"))
        search = self.admin.call("/projection?task=admin&q=test-513")["nodes"][0]
        self.assertEqual([record["id"] for record in search["data"]],["test-513"])


if __name__ == "__main__":
    unittest.main()
