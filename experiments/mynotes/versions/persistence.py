"""Compare logical SQLite state across the two physical storage layouts."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "workspace"))
from journey import persisted_workspace

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("mode", choices=("record", "compare"))
parser.add_argument("database", type=Path)
parser.add_argument("snapshot", type=Path)
args = parser.parse_args()
with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
    observed = persisted_workspace(connection, record_store=args.mode == "compare")
if args.mode == "record":
    args.snapshot.write_text(json.dumps(observed, indent=2, sort_keys=True) + "\n")
else:
    expected = json.loads(args.snapshot.read_text())
    if observed != expected:
        raise AssertionError("Offline notes, tag memberships or catalog differ from native SQLite")
    print(f"SQLite state matches the native teacher ({len(observed['notes'])} notes, {len(observed['tags'])} tags).")
