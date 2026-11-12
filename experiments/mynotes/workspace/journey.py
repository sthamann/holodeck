"""Run the same connected API journey against either implementation."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import socket
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def comparable(value):
    if isinstance(value, list):
        return [comparable(item) for item in value]
    if isinstance(value, dict):
        return {
            key: comparable(item)
            for key, item in value.items()
            if key not in {"created_at", "updated_at", "published_at"}
        }
    return value


def revision(body, etag):
    assert etag and etag == '"' + body["updated_at"] + '"', (etag, body)
    return datetime.fromisoformat(body["updated_at"])


def journey(base, database, record_store=False, logical_state=False):
    transcript = {}

    def call(label, method, route, expected, body=None, etag=None, media_type="application/json"):
        headers = {"Origin": base}
        if body is not None:
            headers["Content-Type"] = media_type
        if etag is not None:
            headers["If-Match"] = etag
        data = None if body is None else (
            json.dumps(body) if media_type == "application/json" else body
        ).encode()
        req = Request(base + "/api/v1" + route, data, headers, method=method)
        try:
            response = urlopen(req, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            content_type = response.headers.get_content_type()
            value = json.loads(raw) if raw and content_type == "application/json" else raw.decode()
            assert response.status == expected, (label, response.status, value)
            tag = response.headers.get("ETag")
            attachment = response.headers.get("Content-Disposition")
            normalized = comparable(value)
            if content_type == "text/markdown" and attachment:
                front_matter, _, markdown = value[4:].partition("\n---\n")
                normalized = "---\n" + re.sub(r'^date: "[^"]+"$', 'date: "<clock>"', front_matter, flags=re.M) + "\n---\n" + markdown
            transcript[label] = {"status": response.status, "body": normalized}
            if attachment:
                transcript[label]["attachment"] = attachment
            return value, tag

    first, _ = call("empty workspace", "GET", "/notes", 200)
    assert first == {"total": 0, "notes": []}
    call("create project tag", "POST", "/tags", 201, {"slug": "field"})
    call("create review tag", "POST", "/tags", 201, {"slug": "review"})
    call("create link destination", "POST", "/notes", 201, {"slug": "brief", "title": "Project brief"})
    call("reserve split slug", "POST", "/notes", 201, {"slug": "decisions", "title": "Earlier decisions"})
    draft = "## Decisions\n\nA fulcrum result. See [[brief]].\n\n## Follow-up\n\nPrepare the next visit."
    call("write project notes", "POST", "/notes", 201, {
        "slug": "field-log", "title": "Field log", "content": draft, "tags": ["field"],
    })
    # Simulate a stored revision ahead of the wall clock on both sides.
    # The next successful edit must advance it by exactly one millisecond.
    with sqlite3.connect(database) as connection:
        if record_store:
            changed = connection.execute(
                "UPDATE records SET document=json_set(document, '$.updated_at', ?) WHERE collection='notes' AND identity=?",
                ("2099-01-01T00:00:00.000Z", "field-log"),
            )
        else:
            changed = connection.execute(
                "UPDATE notes SET updated_at = ? WHERE slug = ?",
                ("2099-01-01T00:00:00.000Z", "field-log"),
            )
        assert changed.rowcount == 1
    original, old = call("open draft", "GET", "/notes/field-log", 200)
    version = revision(original, old)
    assert original["outgoing_links"] == [{"slug": "brief", "title": "Project brief"}]
    target, _ = call("inspect backlink", "GET", "/notes/brief", 200)
    assert target["incoming_links"] == [{"slug": "field-log", "title": "Field log"}]
    updated, current = call("edit title, content and tags", "PATCH", "/notes/field-log", 200, {
        "title": "Field log revised", "content": draft + "\n\nSend the notes.", "tags": ["field", "review"],
    }, old)
    assert updated["updated_at"] == "2099-01-01T00:00:00.001Z", (
        "backwards wall clock", updated["updated_at"],
    )
    assert revision(updated, current) > version
    call("refuse stale overwrite", "PATCH", "/notes/field-log", 412, {"title": "Lost update"}, old)
    noop, same = call("unchanged edit", "PATCH", "/notes/field-log", 200, {
        "title": updated["title"], "tags": ["review", "field"],
    }, current)
    assert same == current and noop == updated
    call("reject unknown tag", "PATCH", "/notes/field-log", 400, {"tags": ["missing"]}, current)
    found, _ = call("search text with two tags", "GET", "/notes?q=fulcrum&tag=field&tag=review", 200)
    assert [note["slug"] for note in found["notes"]] == ["field-log"]
    call("find title prefix", "GET", "/notes?q=Field&titlePrefix=true", 200)
    call("list tags", "GET", "/tags", 200)
    call("reject split with unknown tag", "POST", "/notes/field-log/split", 400, {"tag": "missing"})
    split, _ = call("split into linked and tagged notes", "POST", "/notes/field-log/split", 201, {"tag": "review"})
    children = split["notes"]
    assert [note["title"] for note in children] == ["Decisions", "Follow-up"]
    assert children[0]["slug"] != "decisions"
    for note in children:
        child, _ = call("open split " + note["slug"], "GET", "/notes/" + note["slug"], 200)
        assert [tag["slug"] for tag in child["tags"]] == ["review"]
    retained, retained_tag = call("check split preserves source", "GET", "/notes/field-log", 200)
    assert retained == updated and retained_tag == current
    markdown, _ = call("download markdown", "GET", "/notes/field-log/download-markdown", 200)
    date = re.search(r'^date: "([^"]+)"$', markdown, flags=re.M)
    assert date and datetime.fromisoformat(date[1]) == datetime.fromisoformat(retained["created_at"])
    assert draft in markdown
    imported, _ = call("import supporting markdown", "POST", "/import", 201,
                       "# Visit agenda\n\nDiscuss [[brief]].", media_type="text/markdown")
    assert imported["title"] == "Visit agenda"
    assert [note["slug"] for note in imported["outgoing_links"]] == ["brief"]
    call("delete split note", "DELETE", "/notes/" + children[1]["slug"], 204)
    call("deleted note is gone", "GET", "/notes/" + children[1]["slug"], 404)
    call("browse final workspace", "GET", "/notes?sort=title&order=asc", 200)
    call("inspect final backlink graph", "GET", "/notes/brief", 200)
    with sqlite3.connect(database) as connection:
        if logical_state:
            transcript["persisted workspace"] = persisted_workspace(connection, record_store)
        else:
            for table in ("notes", "tags", "note_tags", "note_links"):
                rows = connection.execute(f"SELECT * FROM {table}")
                keys = [column[0] for column in rows.description]
                transcript["database " + table] = sorted(
                    (comparable(dict(zip(keys, row))) for row in rows),
                    key=lambda row: json.dumps(row, sort_keys=True),
                )
    return transcript


def persisted_workspace(connection, record_store):
    if record_store:
        notes = {}
        tags = []
        for collection, identity, raw in connection.execute(
            "SELECT collection, identity, document FROM records ORDER BY collection, identity"
        ):
            document = json.loads(raw)
            assert document["slug"] == identity
            if collection == "notes":
                notes[identity] = comparable(document)
            elif collection == "tags":
                tags.append(identity)
            else:
                raise AssertionError(f"Unexpected record type {collection}")
        return {"notes": notes, "tags": tags}

    notes = {
        slug: {"slug": slug, "title": title, "content": content, "tags": []}
        for slug, title, content in connection.execute("SELECT slug, title, content FROM notes ORDER BY slug")
    }
    for slug, tag in connection.execute(
        "SELECT n.slug, t.slug FROM note_tags nt JOIN notes n ON nt.note_id=n.id "
        "JOIN tags t ON nt.tag_id=t.id ORDER BY n.slug, t.slug"
    ):
        notes[slug]["tags"].append(tag)
    return {"notes": notes, "tags": [slug for (slug,) in connection.execute("SELECT slug FROM tags ORDER BY slug")]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--logical-state", action="store_true")
    parser.add_argument("--record-store", action="store_true")
    args = parser.parse_args()
    if args.reference:
        try:
            with socket.create_connection(("127.0.0.1", 55766), timeout=1):
                raise RuntimeError("The native MyNotes reference is still listening")
        except ConnectionRefusedError:
            pass
    observed = journey(args.base, args.database, args.record_store, args.logical_state or args.record_store)
    args.transcript.write_text(json.dumps(observed, ensure_ascii=False, indent=2) + "\n")
    if args.reference:
        expected = json.loads(args.reference.read_text())
        assert list(expected) == list(observed), "Different request sequences"
        mismatches = [name for name in expected if expected[name] != observed[name]]
        assert not mismatches, "\n".join(
            f"{name}:\n  reference: {expected[name]}\n  guest: {observed[name]}" for name in mismatches
        )
        state = "the persisted workspace" if args.logical_state or args.record_store else "4 persisted tables"
        print(f"26 HTTP responses and {state} match the native reference after clock normalization")
    else:
        print("Recorded the native HTTP journey and persisted note/tag/link tables")


if __name__ == "__main__":
    main()
