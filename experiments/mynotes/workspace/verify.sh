#!/usr/bin/env bash
# A native capture followed by two isolated offline executions.
set -euo pipefail
reference=${1:?usage: verify.sh MYNOTES_BINARY SOURCE_BUILD WEIGHT_BUILD NEW_EVIDENCE_DIR}
source_build=${2:?source build required}
weight_build=${3:?weight build required}
evidence=${4:?new evidence directory required}
root=$(cd "$(dirname "$0")" && pwd)
mkdir "$evidence"
pid=
stop() {
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    pid=
  fi
}
trap stop EXIT
wait_for_api() {
  for ((attempt=0; attempt<100; attempt++)); do
    kill -0 "$pid" 2>/dev/null || return 1
    if curl -fsS "$1/api/v1/notes" >/dev/null 2>&1; then
      return
    fi
    sleep 0.1
  done
  return 1
}
capture() {
  python3 "$root/journey.py" "$@"
}
reference_base=http://127.0.0.1:55766
base=http://127.0.0.1:55768
"$reference" -addr 127.0.0.1 -port 55766 -data "$evidence/reference" >"$evidence/native.log" 2>&1 &
pid=$!
wait_for_api "$reference_base"
capture "$reference_base" "$evidence/native.json" --database "$evidence/reference/mynotes.sqlite"
stop

for profile in source weights; do
  build=$source_build
  if [[ "$profile" == weights ]]; then build=$weight_build; fi
  database="$evidence/$profile.sqlite"
  start_guest() {
    "$build/host" -module "$build/workspace.wasm" -assets "$build/static" -db "$database" -port 55768 >>"$evidence/$profile.log" 2>&1 &
    pid=$!
    wait_for_api "$base"
  }
  start_guest
  printf '%s: ' "$profile"
  capture "$base" "$evidence/$profile.json" --database "$database" --reference "$evidence/native.json"
  "${HOLODECK_NODE:-node}" "$root/browser.cjs" "$base" "$evidence/$profile.png"
  stop
  start_guest
  python3 - "$base" <<'PY'
import json, sys
from urllib.request import urlopen

def get(slug):
    with urlopen(sys.argv[1] + "/api/v1/notes/" + slug, timeout=10) as response:
        return json.load(response)

note = get("field-log")
assert note["title"] == "Field log browser revision"
assert [tag["slug"] for tag in note["tags"]] == ["field", "review"]
assert note["updated_at"] == "2099-01-01T00:00:00.002Z"
assert note["outgoing_links"] == [{"slug": "brief", "title": "Project brief"}]
handoff = get("browser-handoff")
assert handoff["title"] == "Browser handoff"
assert [tag["slug"] for tag in handoff["tags"]] == ["review"]
assert handoff["outgoing_links"] == note["outgoing_links"]
print("Browser creation, edit, revision, tags and links survived complete host restart.")
PY
  stop
done

if [[ ${HOLODECK_TEST_WEIGHT_MUTATION:-0} == 1 ]]; then
  mutant="$evidence/mutation"
  mkdir "$mutant"
  cp -R "$weight_build/source" "$mutant/source"
  python3 - "$mutant/source/internal/repository/deck_revision.json" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
checkpoint = json.loads(path.read_text())
gate = checkpoint["gates"][3]
assert gate["operation"] == [0, 0, 0, 1, 0, 0]
gate["operation"] = [1, 0, 0, 0, 0, 0]
path.write_text(json.dumps(checkpoint) + "\n")
PY
  mutant=$(cd "$mutant" && pwd)
  (
    cd "$mutant/source"
    GOOS=wasip1 GOARCH=wasm CGO_ENABLED=0 go build -trimpath -buildmode=c-shared -o "$mutant/mutant.wasm" ./cmd/deck-workspace
  )
  "$weight_build/host" -module "$mutant/mutant.wasm" -assets "$weight_build/static" -db "$mutant/guest.sqlite" -port 55768 >"$mutant/host.log" 2>&1 &
  pid=$!
  wait_for_api "$base"
  if capture "$base" "$mutant/guest.json" --database "$mutant/guest.sqlite" --reference "$evidence/native.json" >"$mutant/journey.log" 2>&1; then
    echo "Mutated checkpoint unexpectedly passed" >&2
    exit 1
  fi
  python3 - "$mutant" <<'PY'
from pathlib import Path
import sqlite3, sys

root = Path(sys.argv[1])
log = (root / "journey.log").read_text()
assert "AssertionError: ('backwards wall clock'," in log, log
with sqlite3.connect(root / "guest.sqlite") as connection:
    title, revision = connection.execute(
        "SELECT title, updated_at FROM notes WHERE slug = ?", ("field-log",)
    ).fetchone()
assert title == "Field log revised"
assert revision < "2099-01-01T00:00:00.000Z", revision
print(f"One parameter change caused the edited note to persist revision {revision}.")
PY
  stop
fi
