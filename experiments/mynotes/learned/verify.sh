#!/usr/bin/env bash
set -euo pipefail
lab=$(cd "${1:?usage: verify.sh ACQUISITION_DIR [training|holdout]}" && pwd)
cohort=${2:-holdout}
case "$cohort" in training|holdout) ;; *) exit 2;; esac
here=$(cd "$(dirname "$0")" && pwd)
node=${HOLODECK_NODE:-node}
run=$(mktemp -d "$lab/run-${cohort}.XXXXXX")
echo "$run" > "$lab/last-$cohort"
mkdir "$run/runtime"
cp "$here/../deck/host.mjs" "$here/../deck/kernel.mjs" "$here/../deck/grounding.mjs" "$run/runtime/"
cp -R "$here/../deck/web" "$run/runtime/web"
projection=
if [[ -n ${HOLODECK_CHECKPOINT:-} ]]; then
  cp "$HOLODECK_CHECKPOINT" "$run/runtime/checkpoint.json"
else
  if [[ -s "$lab/holo.source.js" ]] && [[ ${HOLODECK_API_ONLY:-0} != 1 ]]; then projection="$lab/holo.source.js"; fi
  "$node" "$here/lower.cjs" "$lab/api.source.js" "$run/runtime/checkpoint.json" "$projection" | tee "$run/compile.log"
fi
cp "${HOLODECK_HEAD:-$lab/grounding.json}" "$run/runtime/grounding.json"
cd "$run/runtime"
export HOLODECK_GROUNDING="$run/runtime/grounding.json"
export HOLODECK_PORT=${HOLODECK_PORT:-55771}
base="http://127.0.0.1:$HOLODECK_PORT"
pid=
stop() {
  if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; pid=; fi
}
trap stop EXIT
start() {
  "$node" host.mjs checkpoint.json "$1" "$HOLODECK_PORT" >>"$run/host.log" 2>&1 &
  pid=$!
  for ((i=0;i<100;i++)); do
    kill -0 "$pid" 2>/dev/null || return 1
    if curl -sS "$base/" >/dev/null 2>&1; then return; fi
    sleep .1
  done
  return 1
}
start "$run/api.sqlite"
status=0
"$node" "$here/replay.mjs" "$base" "$lab/$cohort.json" "$run/comparison.json" | tee "$run/replay.log" || status=$?
stop
if [[ "$cohort" == holdout ]] && [[ ${HOLODECK_BROWSER:-0} == 1 ]]; then
  start "$run/browser.sqlite"
  "$node" "$here/offline-browser.cjs" "$base" "$run"
  stop
  start "$run/browser.sqlite"
  "$node" "$here/offline-browser.cjs" "$base" "$run" restart
fi
echo "Evidence: $run"
exit "$status"
