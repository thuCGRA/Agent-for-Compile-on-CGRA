#!/usr/bin/env bash
# Simulate map-level results with the freshly generated traces.
set -uo pipefail
cd /home/steve/cac-armA
OUT=work/simv
mkdir -p $OUT
PY=/home/steve/miniconda3/envs/cgra/bin/python
export PYTHONPATH=src

run() {
  local k=$1; shift
  local ratios="$*"
  local trace="work/traces/$k/${k}_trace_1.txt"
  [ -f "$trace" ] || { echo "no trace for $k"; return; }
  for r in $ratios; do
    if [ "$r" = "0" ]; then
      tag="base"
      extra="--pin-ratio 0 --pins-mode 3"
    else
      tag="crit$r"
      extra="--pin-rule critical --pin-ratio $r --pins-mode 3"
    fi
    "$PY" -m cgra_agent.cli run --kernel "$k" --placer greedy $extra \
        --trace "$trace" --out-root "$OUT" --max-llm-calls 0 --quiet \
        > "$OUT/$k-$tag.log" 2>&1
    rc=$?
    latest=$(ls -dt "$OUT/$k"/*/ 2>/dev/null | head -1)
    res=$("$PY" - "$latest" <<'PYEOF'
import json, sys, os
run = os.path.join(sys.argv[1], "run.json")
try:
    d = json.load(open(run))
except Exception as e:
    print("no-run-json", e); raise SystemExit
sim = d.get("sim") or {}
print(d.get("status"), d.get("ii"), d.get("lat"), sim.get("status"),
      sim.get("matches"), sim.get("mismatches"), sim.get("cycles"))
PYEOF
)
    echo "$k $tag rc=$rc $res"
  done
}

for k in array_add array_cond trmm hpcg conv3 conv2d mac mults1 gemm_pace gemm; do
  run "$k" 0 0.25 0.5
done
echo SWEEP_DONE
