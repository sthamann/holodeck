#!/usr/bin/env bash
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
cp "$lab/checkpoint.json" "$out/runtime/model.json"
python3 - <<'PY'
import socket
for port in (55766, 55767, 55768):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, f"Reference listening on {port}"
print("Native and earlier WebAssembly reference ports are closed.")
PY
cd "$out/runtime"
port=${HOLODECK_PORT:-55775}
base="http://127.0.0.1:$port"
pid=
stop() {
  if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; pid=; fi
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
start "$out/api.sqlite"
"$node" "$here/replay.mjs" "$base" "$lab/holdout.json" "$out/api.sqlite" "$out/api.json"
stop
for phase in draft republish withdrawn; do
  start "$out/browser.sqlite"
  "$node" "$here/browser.cjs" "$base" "$out" "$phase"
  stop
done
start "$out/intact.sqlite"
"$node" "$here/ablation.mjs" probe "$base" "$out/intact.json"
stop
"$node" "$here/ablation.mjs" zero "$lab/checkpoint.json" model.json
start "$out/ablated.sqlite"
"$node" "$here/ablation.mjs" probe "$base" "$out/ablated.json"
stop
python3 - "$out" <<'PY'
from pathlib import Path
import json, sys
root = Path(sys.argv[1])
def read(name):
    return json.loads((root / (name + ".json")).read_text())

intact, ablated = read("intact"), read("ablated")
assert intact == {"publicStatus": 200, "title": "Published note"}, intact
assert ablated == {"publicStatus": 404, "title": "Publish note"}, ablated
result = {**read("api"), "browser": [read(p) for p in ("draft", "republish", "withdrawn")],
          "ablation": {"intact": intact, "zeroedRecurrentOutput": ablated}}
(root / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
print("Zeroing only the recurrent output changed both public availability and the Holo.")
PY
echo "Evidence: $out"
