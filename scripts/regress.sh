#!/usr/bin/env bash
# 基线回归：确认 ALLPHASE 改动不影响 pinsMode==0 的老路径
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
for k in array_add mac trmm; do
  bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
  m=$(bash scripts/map.sh "$k" 0 0 2>&1 | tail -3)
  s=$(bash scripts/sim.sh "$k" 0 2>&1 | tail -2)
  echo "$k | $(echo "$m" | grep -o 'Map Success with II = [0-9]*' | head -1) | $(echo "$s" | grep -o 'Matches::[0-9]*, Mismatches::[0-9]*' | head -1)"
done