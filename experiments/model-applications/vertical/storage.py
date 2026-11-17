"""Two durable implementations of the same epoch and snapshot contract."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3

from compiler import canonical, digest


class Conflict(Exception):
    pass


class Journal:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def locked(self, writable):
        with self.path.open("a+b") as file:
            fcntl.flock(file, fcntl.LOCK_EX if writable else fcntl.LOCK_SH)
            file.seek(0)
            yield file

    def latest(self, file):
        state = None
        while True:
            row = file.readline()
            if not row or not row.endswith(b"\n"):
                return state
            envelope = json.loads(row)
            state = envelope["state"]
            if digest(state) != envelope["sha256"]:
                raise ValueError("corrupt journal frame")

    def save(self, expected, state):
        with self.locked(True) as file:
            previous = self.latest(file)
            if (previous["epoch"] if previous else None) != expected:
                raise Conflict("storage epoch changed")
            # Remove an interrupted trailing frame while holding the writer lock.
            if file.tell() and not self.terminated(file):
                file.seek(0)
                complete_end = 0
                for row in file:
                    if not row.endswith(b"\n"):
                        break
                    complete_end += len(row)
                file.truncate(complete_end)
            file.write((canonical({"sha256": digest(state), "state": state}) + "\n").encode())
            file.flush()
            os.fsync(file.fileno())

    def terminated(self, file):
        file.seek(-1, os.SEEK_END)
        return file.read(1) == b"\n"

    def load(self):
        with self.locked(False) as file:
            return self.latest(file)


class Relational:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (epoch INTEGER, revision INTEGER, checkpoint TEXT);
                CREATE TABLE IF NOT EXISTS fields (name TEXT PRIMARY KEY, value INTEGER);
                CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS outbox (position INTEGER PRIMARY KEY, body TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, isolation_level=None)
        try:
            with db:
                yield db
        finally:
            db.close()

    def load(self):
        with self.connect() as db:
            db.execute("BEGIN")
            meta = db.execute("SELECT epoch, revision, checkpoint FROM meta").fetchone()
            if meta is None:
                return None
            epoch, revision, checkpoint = meta
            return {
                "epoch": epoch, "revision": revision, "checkpoint": checkpoint,
                "fields": dict(db.execute("SELECT name, value FROM fields")),
                "receipts": {key: json.loads(body) for key, body in db.execute("SELECT id, body FROM receipts")},
                "outbox": [json.loads(body) for (body,) in db.execute("SELECT body FROM outbox ORDER BY position")],
            }

    def save(self, expected, state):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT epoch FROM meta").fetchone()
            if (row[0] if row else None) != expected:
                raise Conflict("storage epoch changed")
            for table in ("meta", "fields", "receipts", "outbox"):
                db.execute(f"DELETE FROM {table}")
            db.execute("INSERT INTO meta VALUES (?, ?, ?)",
                       (state["epoch"], state["revision"], state["checkpoint"]))
            db.executemany("INSERT INTO fields VALUES (?, ?)", state["fields"].items())
            db.executemany("INSERT INTO receipts VALUES (?, ?)",
                           ((key, canonical(body)) for key, body in state["receipts"].items()))
            db.executemany("INSERT INTO outbox VALUES (?, ?)",
                           ((position, canonical(effect)) for position, effect in enumerate(state["outbox"])))
            db.commit()


def migrate(source, destination):
    """Copy one snapshot while callers have quiesced application writers."""
    snapshot = source.load()
    if snapshot is None:
        raise ValueError("migration source is empty")
    destination.save(None, snapshot)
    if destination.load() != snapshot:
        raise ValueError("migration failed readback")


class Receiver:
    """Independent durable receiver for an effect contract keyed by stable delivery ID."""

    def __init__(self, path):
        self.path = path
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, body TEXT)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def deliver(self, effects):
        with self.connection() as db:
            for effect in effects:
                body = canonical(effect)
                db.execute("INSERT OR IGNORE INTO deliveries VALUES (?, ?)", (effect["id"], body))
                stored = db.execute("SELECT body FROM deliveries WHERE id = ?", (effect["id"],)).fetchone()
                if stored != (body,):
                    raise ValueError("delivery ID reused for a different effect")

    def records(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM deliveries ORDER BY rowid")]
