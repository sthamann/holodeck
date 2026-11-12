#!/usr/bin/env bash
# Build both native teachers, capture separate fit and holdout data, and export four models.
set -euo pipefail
source_dir=$(cd "${1:?usage: build-lab.sh PINNED_MYNOTES_SOURCE NEW_LAB}" && pwd)
mkdir "${2:?new lab directory required}"
lab=$(cd "$2" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
node=${HOLODECK_NODE:-node}
go=${HOLODECK_GO:-go}
train_python=${HOLODECK_TRAIN_PYTHON:-python3}
python3 "$here/prepare.py" "$source_dir" "$lab/teachers"
"$go" build -o "$lab/source-policy" "$here/source-policy.go"
pid=
stop() {
  if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; pid=; fi
}
trap stop EXIT
for version in original review; do
  source="$lab/teachers/$version"
  "$lab/source-policy" "$source/internal/service/service.go" >"$lab/$version.policy.json"
  (
    cd "$source"
    "$go" build -o "$lab/$version.teacher" .
    HOLODECK_OBSERVATIONS="$lab/$version.transitions.json" \
      "$go" test ./internal/service -run '^TestHolodeckObserve$' -count=1
  )
  for phase in fit holdout; do
    data="$lab/native-$version-$phase"
    "$lab/$version.teacher" -addr 127.0.0.1 -port 55766 -data "$data" >"$lab/$version-$phase.log" 2>&1 &
    pid=$!
    for ((i=0; i<100; i++)); do
      kill -0 "$pid" 2>/dev/null
      if curl -fsS http://127.0.0.1:55766/api/v1/notes >/dev/null 2>&1; then break; fi
      sleep .1
    done
    "$node" "$here/observe.mjs" http://127.0.0.1:55766 "$phase" "$lab/$version.$phase.json"
    stop
  done
  python3 "$here/persistence.py" record "$lab/native-$version-holdout/mynotes.sqlite" "$lab/$version.persistence.json"
  "$train_python" "$here/train.py" "$lab/$version.transitions.json" "$lab/$version.fit.json" "$lab/$version.weights.json"
  "$node" "$here/build.mjs" compiled "$lab/$version.policy.json" "$lab/$version.compiled.json"
  "$node" "$here/build.mjs" recurrent "$lab/$version.weights.json" "$lab/$version.recurrent.json"
done
