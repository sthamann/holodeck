#!/usr/bin/env bash
# Recreate the pinned native captures and fit the deployable checkpoint.
set -euo pipefail
binary=${1:?usage: build-lab.sh PINNED_MYNOTES_BINARY NEW_LAB}
binary=$(cd "$(dirname "$binary")" && pwd)/$(basename "$binary")
mkdir "${2:?new lab directory required}"
lab=$(cd "$2" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
node=${HOLODECK_NODE:-node}
go=${HOLODECK_GO:-go}
train_python=${HOLODECK_TRAIN_PYTHON:-python3}
"$go" version -m "$binary" >"$lab/teacher-version.txt"
grep -F 'vcs.revision=73ef4b7b37109a5209d9fbcfbd5d3799918071be' "$lab/teacher-version.txt" >/dev/null
grep -F 'vcs.modified=false' "$lab/teacher-version.txt" >/dev/null
pid=
stop() {
  if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; pid=; fi
}
trap stop EXIT
for mode in train holdout browser; do
  data="$lab/native-$mode"
  "$binary" -addr 127.0.0.1 -port 55766 -data "$data" >"$lab/native-$mode.log" 2>&1 &
  pid=$!
  for ((i=0; i<100; i++)); do
    kill -0 "$pid" 2>/dev/null
    if curl -fsS http://127.0.0.1:55766/api/v1/notes >/dev/null 2>&1; then break; fi
    sleep .1
  done
  if [[ "$mode" == browser ]]; then
    "$node" "$here/capture-browser.cjs" http://127.0.0.1:55766 "$lab/browser.json"
  else
    "$node" "$here/capture.mjs" http://127.0.0.1:55766 "$data/mynotes.sqlite" "$mode" "$lab/$mode.json"
  fi
  stop
done
"$train_python" "$here/learn.py" "$lab/train.json" "$lab/browser.json" "$lab/learned.json" "$lab/fit.json"
"$node" "$here/build.mjs" "$lab/learned.json" "$lab/checkpoint.json"
