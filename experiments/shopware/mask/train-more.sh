#!/usr/bin/env bash
# Train candidates for additional native views, checkout and shipping.
set -euo pipefail
out=${1:?usage: train-more.sh NEW_OUTPUT_DIRECTORY TEACHER_URL}
teacher=${2:?A loopback Shopware teacher URL is required}
python=${HOLODECK_PYTHON:-python3}
node=${HOLODECK_NODE:-node}
mask=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$mask/../../.." && pwd)
bench="$root/experiments/shopware/benchmark"
evidence="$root/experiments/shopware/evidence"
mkdir "$out"
"$node" "$mask/capture.cjs" batch "$teacher" - "$out/areas.json"
"$python" "$mask/learn.py" batch "$out/areas.json" "$out/area-candidates.json"
"$python" "$bench/trace_induction.py" fit "$evidence/trace-induction/contrast-train.jsonl" \
  "$out/checkout" --baseline "$evidence/joint-deck/checkpoint" --teacher-port "${HOLODECK_REPLAY_CLOSED_PORT:-55745}"
"$python" "$bench/shipping_rules.py" fit "$evidence/shipping-rules/train.jsonl" "$out/shipping-prior.json"
