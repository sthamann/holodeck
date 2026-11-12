#!/usr/bin/env bash
set -euo pipefail
native=${1:?usage: verify.sh NATIVE_MYNOTES_BINARY NEW_OUTPUT_DIRECTORY}
mkdir "${2:?new output directory}"
out=$(cd "$2" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
node=${HOLODECK_NODE:-node}
mkdir "$out/runtime"
cp "$here/host.mjs" "$here/kernel.mjs" "$here/grounding.mjs" "$out/runtime/"
cp -R "$here/web" "$out/runtime/web"
"$node" "$here/compile.mjs" "$out/candidates.json"
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
  for ((i=0; i<100; i++)); do
    kill -0 "$pid" 2>/dev/null || return 1
    if curl -fsS "$1/api/v1/notes" >/dev/null 2>&1; then return; fi
    sleep .1
  done
  return 1
}
reference=http://127.0.0.1:55766
base=http://127.0.0.1:55770
"$native" -addr 127.0.0.1 -port 55766 -data "$out/calibration" >"$out/calibration.log" 2>&1 &
pid=$!
wait_for_api "$reference"
"$node" "$here/capture.mjs" "$reference" "$out/training.json"
stop
"$node" "$here/fit.mjs" "$out/candidates.json" "$out/training.json" "$out/runtime/weights.json" "$out/fit.json" "$out/alternate.json"
"$native" -addr 127.0.0.1 -port 55766 -data "$out/reference" >"$out/native.log" 2>&1 &
pid=$!
wait_for_api "$reference"
python3 "$here/../workspace/journey.py" "$reference" "$out/native.json" --database "$out/reference/mynotes.sqlite" --logical-state
stop

python3 - <<'PY'
import socket
for port in (55766, 55767, 55768):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, f"Reference or earlier WebAssembly build is still running on {port}"
print("The reference and both earlier WebAssembly test ports are closed.")
PY

cd "$out/runtime"
start_deck() {
  "$node" host.mjs "$1" "$2" >"$out/deck.log" 2>&1 &
  pid=$!
  wait_for_api "$base"
}
start_deck weights.json "$out/records.sqlite"
python3 "$here/../workspace/journey.py" "$base" "$out/deck.json" --database "$out/records.sqlite" --record-store --reference "$out/native.json"
stop
if [[ ${HOLODECK_API_ONLY:-0} == 1 ]]; then exit; fi

start_deck weights.json "$out/browser.sqlite"
"$node" "$here/browser.cjs" "$base" "$out"
stop
start_deck weights.json "$out/browser.sqlite"
"$node" "$here/browser.cjs" "$base" "$out" restart
stop

"$node" --input-type=module - "$out/runtime/weights.json" "$out/alternate.json" <<'JS'
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const [selected, alternate] = process.argv.slice(2).map((file) => JSON.parse(readFileSync(file)));
const differing = selected.parameters.filter((row, i) => JSON.stringify(row) !== JSON.stringify(alternate.parameters[i]));
assert.equal(differing.length, 1);
assert.deepEqual(selected.routines, alternate.routines);
assert.deepEqual(selected.routes, alternate.routes);
JS
start_deck weights.json "$out/shallow.sqlite"
"$node" "$here/intervention.cjs" "$base" shallow
stop
cp "$out/alternate.json" "$out/runtime/alternate.json"
start_deck alternate.json "$out/deep.sqlite"
"$node" "$here/intervention.cjs" "$base" deep
stop
