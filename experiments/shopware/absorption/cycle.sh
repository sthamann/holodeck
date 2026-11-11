#!/usr/bin/env bash
# One isolated capture, fit, blind replay and promotion cycle.
set -euo pipefail

out=${1:?usage: cycle.sh NEW_OUTPUT_DIRECTORY}
teacher=${HOLODECK_TEACHER_CONTAINER:?Set the disposable holodeck teacher container}
teacher_port=${HOLODECK_TEACHER_PORT:-55745}
deck_port=${HOLODECK_DECK_PORT:-55746}
python=${HOLODECK_PYTHON:-python3}
node=${HOLODECK_NODE:-node}
source_dir=$(cd "$(dirname "$0")" && pwd)
capture=${HOLODECK_CAPTURE_DRIVER:-$source_dir/capture.py}
browser=${HOLODECK_BROWSER_DRIVER:-$source_dir/browser.cjs}
export HOLODECK_TEACHER_URL="http://127.0.0.1:$teacher_port"
deck_url="http://127.0.0.1:$deck_port"
[[ "$teacher" == holodeck-* && "$teacher_port" != "$deck_port" ]]
docker port "$teacher" 80/tcp | grep -Fx "127.0.0.1:$teacher_port" >/dev/null
mkdir "$out"

"$python" "$capture" train "$out/train"
"$node" "$browser" native "$HOLODECK_TEACHER_URL" "$out/train/episodes.json" "$out/train/ui.json"
"$python" "$source_dir/learn.py" "$out/train/inventory.json" "$out/train/episodes.json" "$out/candidate.json" --ui "$out/train/ui.json"
"$python" "$capture" holdout "$out/holdout" --count 12
"$node" "$browser" native "$HOLODECK_TEACHER_URL" "$out/holdout/episodes.json" "$out/holdout/ui.json" "$out/native-screens"
docker stop "$teacher"
"$python" -c 'import json,sys; print(json.dumps([r["record"] for r in json.load(open(sys.argv[1]))]))' "$out/holdout/episodes.json" > "$out/catalog.json"

"$python" "$source_dir/serve.py" "$out/candidate.json" "$out/catalog.json" "$out/verify.sqlite" --port "$deck_port" --teacher-port "$teacher_port" > "$out/server.log" 2>&1 &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT
for attempt in $(seq 1 45); do
  if curl -fsS "$deck_url/" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$server" 2>/dev/null; then cat "$out/server.log"; exit 1; fi
  sleep 1
done
"$python" "$source_dir/verify.py" "$out/train/inventory.json" "$out/candidate.json" "$out/holdout/episodes.json" "$out/api.json" --port "$deck_port" --teacher-port "$teacher_port"
"$node" "$browser" replay "$deck_url" "$out/holdout/episodes.json" "$out/deck-ui.json" "$out/deck-screens"
"$python" "$source_dir/promote.py" "$out/train/inventory.json" "$out/candidate.json" "$out/api.json" "$out/holdout/ui.json" "$out/deck-ui.json" "$out/approved"
