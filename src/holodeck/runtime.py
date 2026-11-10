"""Typed expression evaluation and durable execution for a learned deck."""

import hashlib
from contextlib import contextmanager
import json
import operator
import sqlite3
from pathlib import Path
from uuid import uuid4


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def lookup(obj, key, default=None):
    if isinstance(obj, dict) and isinstance(key, (str, int)):
        return obj.get(str(key), default)
    return default


def rounded_product(a, b, divisor):
    n = a * b
    return (-1 if n < 0 else 1) * ((abs(n) * 2 + divisor) // (2 * divisor))


OPS = {
    "add": operator.add, "sub": operator.sub, "mul": operator.mul,
    "eq": operator.eq, "le": operator.le, "lt": operator.lt,
    "muldiv": rounded_product, "get": lookup,
    "len": len, "not": operator.not_, "sum": sum, "hash": digest,
    "present": lambda x: isinstance(x, str) and bool(x.strip()),
    "email": lambda x: isinstance(x, str) and 0 < x.find("@") < len(x) - 1,
    "int": lambda x: type(x) is int,
    "contains": lambda x, y: str(y).casefold() in str(x).casefold(),
    "in": lambda x, y: x in y,
    "keys": lambda x: list(x),
    "values": lambda x: list(x.values()),
    "latest": lambda rows, key: sorted(rows, key=lambda row: lookup(row, key, 0), reverse=True),
    "slice": lambda rows, start, count: rows[start:start+count],
    "assoc": lambda obj, key, value: {**obj, str(key): value},
    "drop": lambda obj, key: {k: v for k, v in obj.items() if k != key},
    "merge": lambda a, b: {**a, **b},
}


class Refusal(ValueError):
    pass


class Deck:
    def __init__(self, checkpoint):
        self.model = json.loads(Path(checkpoint).read_text())
        if self.model["format"] != 1:
            raise ValueError("Unsupported deck format")
        self.identity = digest(self.model)
        self.config = self.model["config"]

    def expr(self, node, env):
        if isinstance(node, dict):
            return {k: self.expr(v, env) for k, v in node.items()}
        if not isinstance(node, list):
            return node
        op, *args = node
        if op == "literal":
            return args[0]
        if op == "list":
            return [self.expr(v, env) for v in args]
        if op == "ref":
            current = env
            for key in args:
                current = lookup(current, key)
            return current
        if op == "if":
            return self.expr(args[1] if self.expr(args[0], env) else args[2], env)
        if op == "and":
            return all(self.expr(v, env) for v in args)
        if op == "or":
            return any(self.expr(v, env) for v in args)
        if op in {"map", "filter", "all"}:
            collection = self.expr(args[0], env)
            if len(collection) > (10_000 if op == "filter" else 512):
                raise ValueError("Collection exceeds deck limit")
            if op == "map":
                return [self.expr(args[1], {**env, "row": row}) for row in collection]
            if op == "filter":
                return [row for row in collection if self.expr(args[1], {**env, "row": row})]
            return all(self.expr(args[1], {**env, "row": row}) for row in collection)
        if op == "map_dict":
            collection = self.expr(args[0], env)
            if len(collection) > 512:
                raise ValueError("Collection exceeds deck limit")
            return {
                key: self.expr(args[1], {**env, "key": key, "row": row})
                for key, row in collection.items()
            }
        if op == "call":
            fn = self.model["functions"][args[0]]
            values = [self.expr(v, env) for v in args[1:]]
            local = dict(zip(fn["args"], values, strict=True))
            return self.sequence(fn["bindings"], local)[fn["result"]]
        if op == "dataflow":
            # Executes the exported Shopware-trained numeric head selections.
            config, rows = (self.expr(v, env) for v in args)
            objects = {"root": [{}], "item": [dict(row) for row in rows]}
            for step in self.model["numeric_program"]:
                for current in objects[step["kind"]]:
                    current[step["field"]] = self.numeric(
                        step["expr"], current, objects, config
                    )
            return {**objects["root"][0], "lines": objects["item"]}
        return OPS[op](*(self.expr(v, env) for v in args))

    def numeric(self, node, current, objects, config):
        op, *args = node
        if op == "const":
            return args[0]
        if op == "read":
            return {"self": current, "config": config, "root": objects["root"][0]}[args[0]][args[1]]
        if op == "sum":
            return sum(row[args[1]] for row in objects[args[0]])
        return OPS[op](*(self.numeric(v, current, objects, config) for v in args))

    def sequence(self, bindings, env):
        for name, expression in bindings:
            env[name] = self.expr(expression, env)
        return env

    def context(self, state, actor, args, new_id):
        return self.sequence(self.model["bindings"], {
            "state": state, "actor": actor, "arg": args,
            "new_id": new_id, "config": self.config,
        })

    def action(self, name, state, actor, args, new_id):
        spec = self.model["actions"].get(name)
        if spec is None or actor["role"] not in spec["roles"]:
            raise Refusal("Diese Aktion ist hier nicht verfügbar.")
        if args.keys() - set(spec["inputs"]):
            raise Refusal("Unbekannte Eingabe.")
        for field, value in args.items():
            typ = spec["inputs"][field]
            if not ((typ == "number" and type(value) is int and 0 <= value <= 10**8)
                    or (typ == "flag" and type(value) is bool)
                    or (typ == "text" and isinstance(value, str) and len(value) <= 512)):
                raise Refusal("Ungültige Eingabe.")
        env = self.context(state, actor, args, new_id)
        for check in spec["guards"]:
            if not self.expr(check["expression"], env):
                raise Refusal(check["message"])
        effects = self.expr(spec["effects"], env)
        return {**effects, "trace": {"checkpoint": self.identity, "action": name, "graph": digest(spec)}}

    def project(self, task, state, actor, args):
        surface = self.model["surfaces"].get(task)
        if surface is None or actor["role"] not in surface["roles"]:
            raise Refusal("Diese Ansicht ist hier nicht verfügbar.")
        env = self.context(state, actor, args, "")
        nodes = []
        for readout in surface["readouts"]:
            if self.expr(readout["when"], env):
                node = self.expr(readout["component"], env)
                self.filter_actions(node, state, actor)
                nodes.append(node)
        return {
            "title": surface["title"], "nodes": nodes,
            "checkpoint": self.identity, "projection": uuid4().hex,
        }

    def filter_actions(self, node, state, actor):
        # A control's eligibility comes from the very same learned guard graph.
        if isinstance(node, list):
            for item in node:
                self.filter_actions(item, state, actor)
        if not isinstance(node, dict):
            return
        if "actions" in node:
            allowed = []
            for control in node["actions"]:
                spec = self.model["actions"].get(control["event"])
                if spec is None or actor["role"] not in spec["roles"]:
                    continue
                env = self.context(state, actor, control.get("args", {}), "")
                if all(self.expr(c["expression"], env) for c in spec["guards"]
                       if c["preview"] or not control.get("fields")):
                    allowed.append(control)
            node["actions"] = allowed
        for key, value in node.items():
            if key != "actions":
                self.filter_actions(value, state, actor)


class Ledger:
    def __init__(self, path, deck, initial):
        self.path, self.deck = path, deck
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS business (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_version (id INTEGER PRIMARY KEY CHECK(id=1), checkpoint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, work TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
            """)
            db.execute("INSERT OR IGNORE INTO model_version VALUES (1, ?)", (deck.identity,))
            saved = db.execute("SELECT checkpoint FROM model_version WHERE id=1").fetchone()[0]
            if saved != deck.identity:
                raise ValueError("This database is bound to a different deck checkpoint")
            db.execute("INSERT OR IGNORE INTO business VALUES (1, ?)", (encode(initial),))

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def read(self, db=None):
        if db is None:
            with self.connect() as connection:
                return self.read(connection)
        return json.loads(db.execute("SELECT state FROM business WHERE id=1").fetchone()[0])

    def apply(self, name, actor, args, event_id):
        receipt_id = digest([actor, event_id])
        request = digest([name, args])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT request, result FROM receipts WHERE id=?", (receipt_id,)).fetchone()
            if previous:
                if previous[0] != request:
                    raise Refusal("Diese Anfrage wurde mit anderen Angaben bereits verarbeitet.")
                return json.loads(previous[1])
            state = self.read(db)
            result = self.deck.action(name, state, actor, args, uuid4().hex)
            next_state = {**state, **result["writes"]}
            db.execute("UPDATE business SET state=? WHERE id=1", (encode(next_state),))
            for index, work in enumerate(result.get("outbox", [])):
                db.execute("INSERT INTO outbox(id, work) VALUES (?, ?)", (
                    f"{receipt_id}.{index}", encode(work)
                ))
            receipt = {k: v for k, v in result.items() if k not in {"writes", "outbox"}}
            db.execute("INSERT INTO receipts VALUES (?, ?, ?)", (receipt_id, request, encode(receipt)))
            return receipt

    def settle(self, adapter):
        with self.connect() as db:
            pending = db.execute("SELECT id, work FROM outbox WHERE done=0 ORDER BY rowid").fetchall()
        for work_id, encoded in pending:
            event, args = adapter(json.loads(encoded))
            try:
                self.apply(event, {"id": "local-payment", "role": "provider"}, args, work_id)
            except Refusal:
                # Leave a refused event visible for explicit reconciliation.
                raise
            with self.connect() as db:
                db.execute("UPDATE outbox SET done=1 WHERE id=?", (work_id,))
