#!/usr/bin/env bash
# All compared model files run at the same address through one staged runtime.
set -euo pipefail
lab=$(cd "${1:?usage: verify.sh LAB NEW_OUTPUT}" && pwd)
mkdir "${2:?new output directory required}"
out=$(cd "$2" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
node=${HOLODECK_NODE:-node}
unset HOLODECK_GROUNDING
mkdir "$out/runtime"
cp "$here/../deck/host.mjs" "$here/../deck/kernel.mjs" "$here/../deck/grounding.mjs" "$out/runtime/"
cp -R "$here/../deck/web" "$out/runtime/web"
python3 - <<'PY'
import socket
for port in (55766, 55767, 55768):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, f"Reference is still listening on {port}"
print("Native and earlier WebAssembly reference ports are closed.")
PY
"$node" "$here/compare-models.mjs" "$lab" "$out/sequence-report.json"
cd "$out/runtime"
port=${HOLODECK_PORT:-55774}
base="http://127.0.0.1:$port"
pid=
stop() {
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    pid=
  fi
}
trap stop EXIT
start() {
  "$node" host.mjs model.json "$1" "$port" >>"$out/host.log" 2>&1 &
  pid=$!
  for ((i=0; i<100; i++)); do
    kill -0 "$pid" 2>/dev/null || return 1
    if curl -fsS "$base/api/v1/notes" >/dev/null 2>&1; then return; fi
    sleep .1
  done
  return 1
}
cp "$lab/original.compiled.json" model.json
start "$out/seed.sqlite"
"$node" "$here/seed.mjs" "$base" "$lab/original.holdout.json"
stop
python3 - "$out" <<'PY'
import sqlite3, sys
from pathlib import Path
root = Path(sys.argv[1])
with sqlite3.connect(root / "seed.sqlite") as source, sqlite3.connect(root / "common.sqlite") as target:
    source.backup(target)
PY
for engine in compiled recurrent; do
  for version in original review; do
    name="$version.$engine"
    results="$out/$name"
    mkdir "$results"
    cp "$lab/$name.json" model.json
    start "$results/api.sqlite"
    printf '%s: ' "$name"
    "$node" "$here/../learned/replay.mjs" "$base" "$lab/$version.holdout.json" "$results/api.json"
    stop
    python3 "$here/persistence.py" compare "$results/api.sqlite" "$lab/$version.persistence.json"
    rm -f workspace.sqlite workspace.sqlite-shm workspace.sqlite-wal
    cp "$out/common.sqlite" workspace.sqlite
    python3 - "$lab/$name.json" "$out/common.sqlite" "$results/manifest.json" <<'PY'
import hashlib, json, sys
from pathlib import Path
digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert digest("workspace.sqlite") == digest(sys.argv[2])
assert digest("model.json") == digest(sys.argv[1])
files = ["host.mjs", "kernel.mjs", "grounding.mjs", "web/index.html", "web/render.mjs", "web/surface.css"]
Path(sys.argv[3]).write_text(json.dumps({
    "runtime": {p: digest(p) for p in files},
    "database_before": digest("workspace.sqlite"),
    "model": digest("model.json"),
}, indent=2) + "\n")
PY
    start workspace.sqlite
    "$node" "$here/browser.cjs" "$base" "$lab/$version.holdout.json" "$results"
    stop
    start workspace.sqlite
    "$node" "$here/browser.cjs" "$base" "$lab/$version.holdout.json" "$results" restart
    stop
  done
done
python3 - "$out" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
arms = ("original.compiled", "review.compiled", "original.recurrent", "review.recurrent")
read = lambda arm, name: json.loads((root / arm / (name + ".json")).read_text())
manifests = [read(arm, "manifest") for arm in arms]
assert all(m["runtime"] == manifests[0]["runtime"] for m in manifests)
assert len({m["database_before"] for m in manifests}) == 1
assert len({m["model"] for m in manifests}) == len(arms)
assert read("original.compiled", "browser") == read("original.recurrent", "browser")
assert read("review.compiled", "browser") == read("review.recurrent", "browser")
assert read("original.recurrent", "browser") != read("review.recurrent", "browser")
summary = {
    "model_only_comparison": True,
    "arms": {
        arm: {
            "api": {k: read(arm, "api")[k] for k in ("matched", "total")},
            "browser": read(arm, "browser"),
            "restart_passed": True,
            "native_sqlite_state_matched": True,
            "manifest": read(arm, "manifest"),
        } for arm in arms
    },
    "sequence_tests": json.loads((root / "sequence-report.json").read_text()),
}
(root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print("All four runs used identical runtime files and identical initial SQLite bytes.")
PY
echo "Evidence: $out"
