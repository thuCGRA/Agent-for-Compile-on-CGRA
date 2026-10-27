#!/usr/bin/env bash
# Rerun the five kernels whose bundles were overwritten by another agent's probe.
set -uo pipefail
cd /home/steve/cac-armA
mkdir -p work/rerun
export PYTHONPATH=src
for k in array_add conv2d conv3 mac mults1; do
  echo "== reset $k"
  rm -rf "work/armA/$k"
  /home/steve/miniconda3/envs/cgra/bin/python -m cgra_agent.cli run \
    --kernel "$k" --placer greedy --pins-mode 3 --out-root work/armA > "work/rerun/$k.log" 2>&1
  echo "$k rc=$?"
  grep -E "status=|verified|failed|mismatch" "work/rerun/$k.log" | tail -2
done
