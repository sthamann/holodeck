"""Fit discrete commerce specialists against Shopware traces and a lab teacher.

Only the exported checkpoint is needed to serve the shop.
"""

from copy import deepcopy
from itertools import combinations, product
import json
from pathlib import Path
import random
import sys

from .runtime import Deck, Refusal, digest, rounded_product


ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "experiments/shopware/core"
BUYER = {"id": "shopper", "role": "buyer"}
ADMIN = {"id": "merchant", "role": "admin"}
PROVIDER = {"id": "local-payment", "role": "provider"}
CONFIG = {
    "shipping_gross": 490, "shipping_rate": 19, "free_shipping_at": 5000,
    "promo_code": "KAFFEE10", "promo_percent": 10,
}


def ref(*keys):
    return ["ref", *keys]


def lit(value):
    return ["literal", value]


def get(obj, key, default=None):
    return ["get", obj, key, default]


def merge(a, b):
    return ["merge", a, b]


def assoc(obj, key, value):
    return ["assoc", obj, key, value]


def eq(a, b):
    return ["eq", a, b]


def choose(*values):
    return {"$learn": list(values)}


EMPTY_CART = {"items": {}, "promo": "", "last": ""}
A = lambda name: ref("arg", name)
C = lambda name: ref("config", name)
O = lambda name: ref("order", name)
P = lambda name: ref("item", name)
K = ref("catalog")
B = ref("cart")
I = ref("items")
Q = ref("quote")
ORDERS = ref("state", "orders")
CARTS = ref("state", "carts")
UID = ref("actor", "id")
ID = ref("new_id")


def change_cart(cart):
    return {"carts": assoc(CARTS, UID, cart)}


def change_order(fields):
    return assoc(ORDERS, O("id"), merge(ref("order"), fields))


def inventory_delta(quantities, sign):
    return ["map_dict", K, merge(ref("row"), {
        "stock": ["add", ref("row", "stock"), ["mul", sign, get(quantities, ref("key"), 0)]]
    })]


def plan(writes, result=None, outbox=None):
    return {"writes": writes, "result": result or {}, "outbox": outbox or lit([])}


def call(name, *args):
    return ["call", name, *args]


def calc_functions():
    row = get(ref("catalog"), ref("row"), {})
    return {
        "quote": {
            "args": ["catalog", "cart", "config"],
            "bindings": [
                ["promo", choose(eq(ref("cart", "promo"), C("promo_code")), False, True)],
                ["lines", ["map", ["keys", ref("cart", "items")], {
                    "sku": ref("row"),
                    "quantity": get(ref("cart", "items"), ref("row")),
                    "name": get(row, "name"),
                    "rate": get(row, "rate"),
                    "unit": ["if", ref("promo"),
                             ["muldiv", get(row, "price"), ["sub", 100, C("promo_percent")], 100],
                             get(row, "price")],
                }]],
                ["subtotal", ["sum", ["map", ref("lines"), ["mul", ref("row", "unit"), ref("row", "quantity")]]]],
                ["shipping", choose(
                    ["if", ["or", eq(["len", ref("lines")], 0), ["le", C("free_shipping_at"), ref("subtotal")]], 0, C("shipping_gross")],
                    ["if", ["le", C("free_shipping_at"), ref("subtotal")], 0, C("shipping_gross")],
                    0, C("shipping_gross"),
                )],
                ["price", ["dataflow", {
                    "shipping_gross": ref("shipping"),
                    "shipping_tax": ["muldiv", ref("shipping"), C("shipping_rate"), ["add", 100, C("shipping_rate")]],
                }, ref("lines")]],
                ["quote", merge(ref("price"), {"shipping": ref("shipping"), "promo": ref("promo")})],
            ],
            "result": "quote",
        },
    }


BINDINGS = [
    ["catalog", ref("state", "catalog")],
    ["cart", get(CARTS, UID, EMPTY_CART)],
    ["items", ref("cart", "items")],
    ["item", get(K, A("sku"), {})],
    ["order", get(ORDERS, get(ref("arg"), "order_id", ref("cart", "last")), {})],
    ["quote", call("quote", K, B, ref("config"))],
    ["quote_key", ["hash", Q]],
]


def action_specs():
    item_quantity = assoc(I, A("sku"), A("quantity"))
    order = {
        "id": ID, "buyer": UID, "name": A("name"), "email": A("email"),
        "address": A("address"), "items": I, "quote": Q, "tracking": "",
        "consent": {"quote_key":A("quote_key"),"confirmed":A("consent")},
        "sequence": choose(["add",["len",ORDERS],1], 0),
        "stage": choose("pending", "ready", "completed"),
        "payment": choose("pending", "paid", "failed"),
    }
    paid = eq(A("outcome"), "approved")
    return {
        "cart.set": ("buyer", {"sku":"text", "quantity":"number"}, plan(change_cart(merge(B, {
            "items": choose(["if", eq(A("quantity"), 0), ["drop", I, A("sku")], item_quantity],
                            item_quantity, ["drop", I, A("sku")]),
        })))),
        "cart.promo": ("buyer", {"code":"text"}, plan(change_cart(merge(B, {
            "promo": choose(A("code"), "", C("promo_code")),
        })))),
        "checkout.place": ("buyer", {
            "name":"text", "email":"text", "address":"text", "quote_key":"text", "method":"text", "consent":"flag",
        }, plan({
            "catalog": choose(inventory_delta(I, -1), inventory_delta(I, 1), K),
            "orders": assoc(ORDERS, ID, order),
            "carts": change_cart(merge(EMPTY_CART, {"last": ID}))["carts"],
        }, {"order_id": ID}, ["list", {"kind":"payment", "order_id":ID, "method":A("method")} ])),
        "payment.result": ("provider", {"order_id":"text", "outcome":"text"}, plan({
            "orders": change_order({
                "stage": choose(["if", paid, "ready", "payment_failed"], "ready", "payment_failed"),
                "payment": choose(["if", paid, "paid", "failed"], "paid", "failed"),
            }),
            "catalog": choose(["if", paid, K, inventory_delta(O("items"), 1)], K, inventory_delta(O("items"), 1)),
        })),
        "catalog.edit": ("admin", {"sku":"text", "price":"number", "stock":"number",
                                    "expected_price":"number", "expected_stock":"number"}, plan({
            "catalog": assoc(K, A("sku"), merge(ref("item"), {
                "price": choose(A("price"), P("price"), A("stock")),
                "stock": choose(A("stock"), P("stock"), A("price")),
            })),
        })),
        "order.ship": ("admin", {"order_id":"text", "tracking":"text"}, plan({
            "orders": change_order({
                "stage": choose("shipped", "ready", "completed"),
                "tracking": choose(A("tracking"), "", O("tracking")),
            }),
        })),
        "order.complete": ("admin", {"order_id":"text"}, plan({
            "orders": change_order({"stage": choose("completed", "shipped", "ready")}),
        })),
        "order.cancel": ("admin", {"order_id":"text"}, plan({
            "orders": change_order({"stage": choose("cancelled", "returned", "ready"),
                                    "payment": choose("refunded", "paid", "failed")}),
            "catalog": choose(inventory_delta(O("items"), 1), inventory_delta(O("items"), -1), K),
        })),
        "order.return": ("admin", {"order_id":"text", "inspection":"text"}, plan({
            "orders": change_order({"stage": choose("returned", "cancelled", "completed"),
                                    "payment": choose("refunded", "paid", "failed")}),
            "catalog": choose(["if", eq(A("inspection"), "resellable"),
                               inventory_delta(O("items"), 1), K], inventory_delta(O("items"), 1), K),
        })),
    }


def atom(expression, message, preview=True):
    return {"expression": expression, "message": message, "preview": preview}


# Shared grammar of candidate tests. The learner finds a separating subset for each event.
def atoms():
    collection = [
        atom(["present", P("sku")], "Artikel nicht gefunden."),
        atom(["or", eq(A("quantity"), 0), eq(P("active"), True)], "Dieser Artikel ist nicht verfügbar."),
        atom(["and", ["int", A("quantity")], ["int", P("stock")],
              ["le", A("quantity"), P("stock")]], "Nicht genügend Bestand.", False),
        atom(["and", ["int", A("price")], ["lt", 0, A("price")]], "Der Preis muss größer als null sein.", False),
        atom(["int", A("stock")], "Bitte den Bestand angeben.", False),
        atom(eq(A("expected_price"), P("price")), "Der Preis wurde geändert. Bitte lade die Ansicht neu."),
        atom(eq(A("expected_stock"), P("stock")), "Der Bestand wurde geändert. Bitte lade die Ansicht neu."),
        atom(["in", A("code"), ["list", "", C("promo_code")]], "Dieser Rabattcode gilt hier nicht.", False),
        atom(["lt", 0, ["len", I]], "Der Warenkorb ist leer."),
        atom(["all", ["keys", I], ["and", eq(get(get(K, ref("row"), {}), "active"), True),
              ["le", get(I, ref("row")), get(get(K, ref("row"), {}), "stock", 0)]]],
             "Der Bestand hat sich geändert."),
        atom(eq(A("quote_key"), ref("quote_key")), "Der Warenkorb hat sich geändert. Bitte bestätige den aktuellen Preis.", False),
        atom(["present", A("name")], "Bitte den Namen angeben.", False),
        atom(["email", A("email")], "Bitte eine gültige E-Mail-Adresse angeben.", False),
        atom(["present", A("address")], "Bitte eine Lieferadresse angeben.", False),
        atom(["in", A("method"), lit(["test", "declined"])], "Bitte eine Testzahlung wählen.", False),
        atom(eq(A("consent"), True), "Bitte bestätige die Testbestellung zum angezeigten Preis.", False),
        atom(["in", A("outcome"), lit(["approved", "declined"])], "Unbekanntes Zahlungsergebnis.", False),
        atom(["present", A("tracking")], "Bitte eine Sendungsnummer eingeben.", False),
        atom(["in", A("inspection"), lit(["resellable", "damaged"])], "Bitte den Zustand auswählen.", False),
        atom(eq(O("payment"), "paid"), "Die Bestellung ist noch nicht bezahlt."),
    ]
    for stage in ("pending", "ready", "shipped", "completed", "cancelled", "returned"):
        collection.append(atom(eq(O("stage"), stage), "Diese Aktion passt nicht zum aktuellen Bestellstatus."))
    return collection


# Independent lab teacher for lifecycle and configured shipping/promotion policy.
# Its implementation never runs in the serving process.
def expected_quote(state, actor):
    cart = state["carts"].get(actor["id"], EMPTY_CART)
    promo = cart["promo"] == CONFIG["promo_code"]
    lines = []
    for sku, quantity in sorted(cart["items"].items()):
        p = state["catalog"][sku]
        unit = rounded_product(p["price"], 90, 100) if promo else p["price"]
        gross = unit * quantity
        tax = rounded_product(gross, p["rate"], 100 + p["rate"])
        lines.append(dict(sku=sku, quantity=quantity, name=p["name"], rate=p["rate"], unit=unit,
                          gross=gross, tax=tax, net=gross-tax))
    goods = sum(r["gross"] for r in lines)
    shipping = 490 if lines and goods < 5000 else 0
    tax = sum(r["tax"] for r in lines) + rounded_product(shipping, 19, 119)
    return dict(goods=goods, gross=goods+shipping, tax=tax, net=goods+shipping-tax,
                shipping=shipping, lines=lines, promo=promo)


def teacher(name, state, actor, args, new_id):
    s = deepcopy(state)
    cart = s["carts"].get(actor["id"], deepcopy(EMPTY_CART))
    p = s["catalog"].get(args.get("sku"))
    o = s["orders"].get(args.get("order_id"), {})
    qty = args.get("quantity")
    result, outgoing = {}, []
    if name == "cart.set":
        if not p or type(qty) is not int or not (0 <= qty <= p["stock"]) or (qty > 0 and not p["active"]):
            return None
        if qty:
            cart["items"][p["sku"]] = qty
        else:
            cart["items"].pop(p["sku"], None)
        s["carts"][actor["id"]] = cart
        changed = ["carts"]
    elif name == "cart.promo":
        code = args.get("code")
        if code not in ("", "KAFFEE10"):
            return None
        cart["promo"] = code
        s["carts"][actor["id"]] = cart
        changed = ["carts"]
    elif name == "catalog.edit":
        price, stock = args.get("price"), args.get("stock")
        if (not p or type(price) is not int or price <= 0 or type(stock) is not int
                or args.get("expected_price") != p["price"] or args.get("expected_stock") != p["stock"]):
            return None
        p.update(price=price, stock=stock)
        changed = ["catalog"]
    elif name == "checkout.place":
        if not cart["items"] or any(not s["catalog"][sku]["active"] or n > s["catalog"][sku]["stock"]
                                     for sku, n in cart["items"].items()):
            return None
        q = expected_quote(state, actor)
        email = args.get("email", "")
        if (not args.get("name", "").strip() or not 0 < email.find("@") < len(email) - 1
                or not args.get("address", "").strip() or args.get("quote_key") != digest(q)
                or args.get("method") not in ("test", "declined") or args.get("consent") is not True):
            return None
        s["orders"][new_id] = {**{k:args[k] for k in ("name", "email", "address")},
                              "id":new_id, "buyer":actor["id"], "items":cart["items"], "quote":q,
                              "consent":{"quote_key":args["quote_key"],"confirmed":True},
                              "sequence":len(s["orders"])+1,
                              "stage":"pending", "payment":"pending", "tracking":""}
        for sku, quantity in cart["items"].items():
            s["catalog"][sku]["stock"] -= quantity
        s["carts"][actor["id"]] = {**EMPTY_CART, "last":new_id}
        result = {"order_id":new_id}
        outgoing = [{"kind":"payment", "order_id":new_id, "method":args["method"]}]
        changed = ["catalog", "orders", "carts"]
    elif name == "payment.result":
        outcome = args.get("outcome")
        if o.get("stage") != "pending" or outcome not in ("approved", "declined"):
            return None
        if outcome == "approved":
            o.update(stage="ready", payment="paid")
        else:
            o.update(stage="payment_failed", payment="failed")
            for sku, n in o["items"].items():
                s["catalog"][sku]["stock"] += n
        changed = ["orders", "catalog"]
    elif name == "order.ship":
        if o.get("stage") != "ready" or o.get("payment") != "paid" or not args.get("tracking", "").strip():
            return None
        o.update(stage="shipped", tracking=args["tracking"])
        changed = ["orders"]
    elif name == "order.complete":
        if o.get("stage") != "shipped":
            return None
        o["stage"] = "completed"
        changed = ["orders"]
    elif name in ("order.cancel", "order.return"):
        cancellation = name == "order.cancel"
        if o.get("stage") != ("ready" if cancellation else "completed") or o.get("payment") != "paid":
            return None
        if not cancellation and args.get("inspection") not in ("resellable", "damaged"):
            return None
        o.update(stage="cancelled" if cancellation else "returned", payment="refunded")
        if cancellation or args["inspection"] == "resellable":
            for sku, n in o["items"].items():
                s["catalog"][sku]["stock"] += n
        changed = ["orders", "catalog"]
    else:
        raise ValueError(name)
    return {"writes": {key:s[key] for key in changed}, "result":result, "outbox":outgoing}


def samples(name, seed):
    rng = random.Random(seed)
    actors = {"buyer":BUYER, "admin":ADMIN, "provider":PROVIDER}
    role, _, _ = action_specs()[name]
    actor = actors[role]
    for i in range(90):
        first, second = rng.randrange(750, 8500), rng.randrange(950, 8500)
        catalog = {
            "a":{"sku":"a","name":"A","price":first,"rate":19,"stock":7,"active":True},
            "b":{"sku":"b","name":"B","price":second,"rate":7,"stock":4,"active":True},
        }
        stage = {"payment.result":"pending", "order.ship":"ready", "order.complete":"shipped",
                 "order.cancel":"ready", "order.return":"completed"}.get(name, "ready")
        order = {"id":"o","buyer":BUYER["id"],"stage":stage,
                 "payment":"pending" if stage=="pending" else "paid","items":{"a":2,"b":1},"tracking":"OLD"}
        cart = {"items":{"a":rng.randrange(1,5),"b":rng.randrange(1,3)}, "promo":"KAFFEE10" if i%3==0 else "", "last":""}
        state = {"catalog":catalog,"carts":{BUYER["id"]:cart},"orders":{"o":order}}
        args = {
            "cart.set":{"sku":"a","quantity":i%5},
            "cart.promo":{"code":"" if i%2 else "KAFFEE10"},
            "checkout.place":{"name":"Mira Test","email":"mira@example.test","address":"Testweg 3",
                              "method":"declined" if i%2 else "test","consent":True},
            "payment.result":{"order_id":"o","outcome":"declined" if i%2 else "approved"},
            "catalog.edit":{"sku":"a","price":rng.randrange(200,10000),"stock":i%8,
                            "expected_price":first,"expected_stock":7},
            "order.ship":{"order_id":"o","tracking":f"TRACK-{i}"},
            "order.complete":{"order_id":"o"},
            "order.cancel":{"order_id":"o"},
            "order.return":{"order_id":"o","inspection":"resellable" if i%2 else "damaged"},
        }[name]
        if name == "checkout.place":
            args["quote_key"] = digest(expected_quote(state, actor))
        yield state, actor, args, "created", teacher(name, state, actor, args, "created")

        negative = {
            "cart.set":[("sku","missing"),("quantity",99),("quantity",None)],
            "cart.promo":[("code","FAKE"),("code",None)],
            "checkout.place":[("name",""),("email","no"),("address",""),("method","unknown"),
                              ("quote_key","stale"),("consent",False)],
            "payment.result":[("order_id","missing"),("outcome","unknown")],
            "catalog.edit":[("sku","missing"),("price",0),("stock",None),("expected_price",1),("expected_stock",1)],
            "order.ship":[("order_id","missing"),("tracking","")],
            "order.complete":[("order_id","missing")],
            "order.cancel":[("order_id","missing")],
            "order.return":[("order_id","missing"),("inspection","unknown")],
        }[name]
        for field,value in negative:
            bad = {**args, field:value}
            yield state, actor, bad, "created", teacher(name,state,actor,bad,"created")
        for change in ("stage","payment","stock","active","empty"):
            altered = deepcopy(state)
            if change == "stage":
                altered["orders"]["o"]["stage"] = ("completed" if stage != "completed" else "ready")
            elif change == "payment":
                altered["orders"]["o"]["payment"] = "failed"
            elif change == "stock":
                altered["catalog"]["a"]["stock"] = 0
            elif change == "active":
                altered["catalog"]["a"]["active"] = False
            elif change == "empty":
                altered["carts"][BUYER["id"]]["items"] = {}
            change_args = args
            if change == "empty" and name == "checkout.place":
                change_args = {**args, "quote_key":digest(expected_quote(altered, actor))}
            yield altered, actor, change_args, "created", teacher(name,altered,actor,change_args,"created")


def candidates(node):
    if isinstance(node, dict):
        if "$learn" in node:
            yield from node["$learn"]
        else:
            for values in product(*(candidates(v) for v in node.values())):
                yield dict(zip(node,values,strict=True))
    elif isinstance(node,list):
        for values in product(*(candidates(v) for v in node)):
            yield list(values)
    else:
        yield node


def truth(deck, expression, env):
    try:
        return bool(deck.expr(expression, env))
    except (TypeError, KeyError):
        return False


def fit_action(deck, rows, effect_template):
    envs = [deck.context(s,a,x,i) for s,a,x,i,_ in rows]
    expected = [row[-1] for row in rows]
    positive = [i for i,e in enumerate(expected) if e is not None]
    negative = {i for i,e in enumerate(expected) if e is None}
    eligible = []
    for check in atoms():
        if all(truth(deck,check["expression"],envs[i]) for i in positive):
            rejects = {i for i in negative if not truth(deck,check["expression"],envs[i])}
            if rejects:
                eligible.append((check,rejects))
    for count in range(len(eligible)+1):
        valid = next((group for group in combinations(eligible,count)
                      if set().union(*(rejects for _,rejects in group)) == negative), None)
        if valid is not None:
            break
    if valid is None:
        raise ValueError("No consistent guard set")
    matches = [candidate for candidate in candidates(effect_template)
               if all(deck.expr(candidate,envs[i]) == expected[i] for i in positive)]
    if not matches:
        raise ValueError("No consistent effect graph")
    return [check for check,_ in valid], matches[0], len(matches)


def field(name, label, kind="text", value="", options=None):
    return {"name":name,"label":label,"type":kind,"value":value,"options":lit(options or [])}


def control(event, title, args=None, fields=None, navigate=None):
    value = {"event":event,"title":title,"args":args or {},"fields":["list", *(fields or [])]}
    if navigate:
        value["navigate"] = navigate
    return value


def visible_order(scope):
    fields = ("id", "name", "address", "stage", "payment", "quote", "tracking")
    return {field:ref(scope,field) for field in fields}


def readout_library():
    lines = ref("quote","lines")
    cart_lines = ["map", lines, merge(ref("row"), {
        "actions":["list", control("cart.set", "Menge ändern", {"sku":ref("row","sku")},
                                   [field("quantity","Menge","number",ref("row","quantity"))]),
                   control("cart.set","Entfernen",{"sku":ref("row","sku"),"quantity":0})],
    })]
    totals = {"component":"totals","title":"Zusammenfassung","data":Q}
    buyer_order = {"component":"order","title":"Deine Bestellung",
                   "data":{**visible_order("order"),"consent":O("consent")},"actions":lit([])}
    admin_catalog = ["map", ["values",K], merge(ref("row"), {
        "actions":["list", control("catalog.edit","Speichern",{"sku":ref("row","sku"),
            "expected_price":ref("row","price"),"expected_stock":ref("row","stock")},[
            field("price","Preis in Euro","money",ref("row","price")),
            field("stock","Bestand","number",ref("row","stock")),
        ])],
    })]
    matching_orders = ["filter", ["values",ORDERS], ["or",
        ["contains",ref("row","id"),get(ref("arg"),"q","")],
        ["contains",ref("row","name"),get(ref("arg"),"q","")],
    ]]
    page = get(ref("arg"),"page",0)
    admin_orders = ["map", ["slice",["latest",matching_orders,"sequence"],["mul",page,20],20], merge(visible_order("row"), {
        "actions":["list",
            control("order.ship","Versand erfassen",{"order_id":ref("row","id")},[
                field("tracking","Sendungsnummer"),
            ]),
            control("order.complete","Als zugestellt markieren",{"order_id":ref("row","id")}),
            control("order.cancel","Stornieren und erstatten",{"order_id":ref("row","id")}),
            control("order.return","Retoure abschließen",{"order_id":ref("row","id")},[
                field("inspection","Zustand","select","resellable",[
                    ["resellable","Wiederverkaufbar"],["damaged","Nicht wiederverkaufbar"],
                ]),
            ]),
        ],
    })]
    return {
        "catalog": {"component":"products","title":"Kannen und Zubehör",
            "data":["map",["filter",["values",K],["and",eq(ref("row","active"),True),
                       ["contains",ref("row","name"),get(ref("arg"),"q","")]]],
                    merge(ref("row"),{
                        "actions":["list",control("cart.set","In den Warenkorb",{
                            "sku":ref("row","sku"),
                            "quantity":["add",get(I,ref("row","sku"),0),1],
                        })],
                    })]},
        "cart": {"component":"lines","title":"Warenkorb","data":cart_lines,
                 "link":{"task":"checkout","label":"Zur Kasse"}},
        "checkout_items": {"component":"lines","title":"Deine Auswahl","data":cart_lines},
        "totals": totals,
        "empty": {"component":"empty","title":"Dein Warenkorb ist leer",
                  "text":"Wähle im Sortiment einen Artikel aus.","link":{"task":"storefront","label":"Zum Sortiment"}},
        "promo": {"component":"controls","title":"Rabattcode",
                  "actions":["list",control("cart.promo","Anwenden",{},[
                      field("code","Code","text",ref("cart","promo")),
                  ])]},
        "purchase": {"component":"controls","title":"Lieferung und Testzahlung",
                     "actions":["list",control("checkout.place","Testbestellung aufgeben",
                         {"quote_key":ref("quote_key")},[
                             field("name","Vollständiger Name"),field("email","E-Mail","email"),
                             field("address","Lieferadresse"),
                             field("method","Zahlungsart","select","test",[
                                 ["test","Testzahlung"],["declined","Testzahlung ablehnen"],
                             ]),
                             field("consent","Ich bestätige die Testbestellung zum angezeigten Preis.","checkbox",False),
                         ],"order")]},
        "receipt": buyer_order,
        "catalog_admin": {"component":"inventory","title":"Sortiment pflegen","data":admin_catalog},
        "orders_admin": {"component":"orders","title":"Bestellungen","data":admin_orders,
                         "page":page,"size":20,"total":["len",matching_orders]},
    }


def learn_projections():
    """Induce component eligibility from compact UI teaching cases."""
    lib = readout_library()
    cases = [
        ("storefront",False,False,["catalog"]),
        ("storefront",True,False,["catalog","cart"]),
        ("storefront",False,True,["catalog"]),
        ("storefront",True,True,["catalog","cart"]),
        ("checkout",False,False,["empty"]),
        ("checkout",True,False,["checkout_items","promo","purchase","totals"]),
        ("checkout",False,True,["empty"]),
        ("checkout",True,True,["checkout_items","promo","purchase","totals"]),
        ("order",False,True,["receipt"]),
        ("order",True,True,["receipt"]),
        ("order",False,False,["empty"]),
        ("order",True,False,["empty"]),
        ("admin",False,False,["orders_admin","catalog_admin"]),
        ("admin",True,True,["orders_admin","catalog_admin"]),
    ]
    expressions = [
        (lambda c:True, True),
        (lambda c:c[1], ["lt",0,["len",I]]),
        (lambda c:not c[1], eq(["len",I],0)),
        (lambda c:c[2], ["and",["present",O("id")],eq(O("buyer"),UID)]),
        (lambda c:not c[2], ["not",["and",["present",O("id")],eq(O("buyer"),UID)]]),
    ]
    titles = {"storefront":"Sortiment","checkout":"Kasse","order":"Bestellstatus","admin":"Shop verwalten"}
    surfaces = {}
    for task,title in titles.items():
        rows = [row for row in cases if row[0]==task]
        components = list(dict.fromkeys(component for row in rows for component in row[3]))
        surfaces[task] = {"title":title,"roles":["admin" if task=="admin" else "buyer"],"readouts":[
            {"when":next(expr for predicate,expr in expressions
                         if all(predicate(row)==(component in row[3]) for row in rows)),
             "component":lib[component]} for component in components
        ]}
    return surfaces,len(cases)


def catalog_fixture():
    return {
        "moka-3":{"sku":"moka-3","name":"Moka Express","variant":"3 Tassen",
                  "description":"Klassischer Espressokocher aus Aluminium.","price":3490,
                  "rate":19,"stock":8,"active":True,"image":"moka.jpg"},
        "moka-6":{"sku":"moka-6","name":"Moka Express","variant":"6 Tassen",
                  "description":"Die größere Kanne für den gemeinsamen Kaffee.","price":5490,
                  "rate":19,"stock":5,"active":True,"image":"moka-pair.jpg"},
        "press":{"sku":"press","name":"French Press","variant":"1 Liter",
                 "description":"Glaskanne mit Edelstahlfilter für kräftigen Filterkaffee.","price":2990,
                 "rate":19,"stock":6,"active":True,"image":"press.jpg"},
    }


def fit(output):
    sys.path.insert(0, str(CORE))
    from vm import Model
    numeric = Model(CORE / "models/base")
    data = {
        "format":1, "config":CONFIG, "bindings":BINDINGS,
        "functions":{}, "actions":{}, "numeric_program":numeric.program({"kind":"price"}),
        "surfaces":{}, "initial":{"catalog":catalog_fixture(),"carts":{},"orders":{}},
    }
    deck = Deck.__new__(Deck)
    deck.model, deck.config, deck.identity = data, CONFIG, "fitting"
    pricing_cases = []
    for state,actor,_,_,_ in list(samples("checkout.place",245))[:170]:
        pricing_cases.append((state,actor,expected_quote(state,actor)))
    fixture = {"catalog":catalog_fixture(),"carts":{BUYER["id"]:{"items":{},"promo":"","last":""}},"orders":{}}
    for sku, price, promo in product(("moka-3", "press"), (4999,5000,5001,8400), ("","KAFFEE10")):
        row = deepcopy(fixture)
        row["catalog"][sku]["price"] = price
        row["carts"][BUYER["id"]].update(items={sku:1},promo=promo)
        pricing_cases.append((row,BUYER,expected_quote(row,BUYER)))
    solutions = []
    for functions in candidates(calc_functions()):
        data["functions"] = functions
        if all(deck.context(s,a,{},"")["quote"]==expected for s,a,expected in pricing_cases):
            solutions.append(functions)
    if len(solutions) != 1:
        raise RuntimeError(f"Pricing candidates unresolved: {len(solutions)}")
    data["functions"] = solutions[0]

    oracle = json.loads((CORE / "data/shopware.json").read_text())
    checked = {"train":0,"test":0}
    for case in oracle:
        for transition in case["events"]:
            if transition["event"]["kind"] != "price":
                continue
            before, after = transition["before"], transition["after"]
            rows = [o["fields"] for o in before["objects"] if o["kind"]=="item"]
            actual = deck.expr(["dataflow",lit(before["config"]),lit(rows)],{})
            root = next(o["fields"] for o in after["objects"] if o["kind"]=="root")
            assert all(actual[k]==root[k] for k in ("goods","gross","net","tax"))
            assert actual["lines"] == [o["fields"] for o in after["objects"] if o["kind"]=="item"]
            checked[case["split"]] += 1

    proof = {}
    for index,(name,(role,inputs,template)) in enumerate(action_specs().items()):
        train = list(samples(name,137+index))
        guards,effects,consistent = fit_action(deck,train,template)
        data["actions"][name] = {"roles":[role],"inputs":inputs,"guards":guards,"effects":effects}
        held_out = list(samples(name,3127+index))
        for state,actor,args,new_id,expected in held_out:
            try:
                got = deck.action(name,state,actor,args,new_id)
            except Refusal:
                got = None
            if got is not None:
                got.pop("trace")
            assert got == expected, (name,args,got,expected)
        proof[name] = {"training":len(train),"held_out":len(held_out),
                       "consistent_effect_candidates":consistent,"guards":len(guards)}
    data["surfaces"], ui_cases = learn_projections()
    data["training"] = {"numeric_checkpoint":numeric.identity, "shopware_price_traces":checked,
                        "lab_policy":"Independent synthetic teacher in teach.py",
                        "quote_cases":len(pricing_cases),"actions":proof,"projection_cases":ui_cases}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps({"checkpoint":digest(data),"training":data["training"]},indent=2))


if __name__ == "__main__":
    import argparse
    cli = argparse.ArgumentParser()
    cli.add_argument("--output",type=Path,default=ROOT/"src/holodeck/checkpoint.json")
    fit(cli.parse_args().output)
