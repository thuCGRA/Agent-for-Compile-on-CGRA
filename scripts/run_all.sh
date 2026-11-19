#!/usr/bin/env bash
# 端到端：C → DFG → 架构注入 → trace → 映射(默认 method 0) → 仿真
# 用法: scripts/run_all.sh [kernel ...]      不带参数 = kernels.tsv 里全部
#        METHOD=1 scripts/run_all.sh mac      （换 SA；注意上游 SA 在 PathFinder 成功时直接返回）
source "$(dirname "$0")/lib.sh"
METHOD="${METHOD:-0}"

cd "$ROOT"
[ -f "$OUT/libdfggenPass.so" ] || { echo "先跑 scripts/build.sh" >&2; exit 2; }

LIST=("$@")
[ ${#LIST[@]} -eq 0 ] && mapfile -t LIST < <(kernels)

printf '%-10s %-6s %-4s %-10s %s\n' kernel nodes II map sim
FAILED=0
for k in "${LIST[@]}"; do
  W="$(kdir "$k")"
  if ! bash "$ROOT/scripts/gen_dfg.sh"    "$k" >/dev/null 2>&1; then echo "$k  DFG 生成失败";
    printf '%-10s %-6s %-4s %-10s %s\n' "$k" - - - "DFG_FAIL"; FAILED=1; continue; fi
  bash "$ROOT/scripts/inject_mem.sh" "$k" >/dev/null 2>&1 || true
  bash "$ROOT/scripts/gen_trace.sh"  "$k" >/dev/null 2>&1 || true
  if ! bash "$ROOT/scripts/map.sh"   "$k" "$METHOD" >/dev/null 2>&1; then
    printf '%-10s %-6s %-4s %-10s %s\n' "$k" "$(grep -c '<Node ' "$W/dfg.xml")" - - "MAP_FAIL"; FAILED=1; continue; fi
  II="$(grep -o 'Map Success with II = [0-9]*' "$W/m$METHOD/map.log" | head -1 | grep -o '[0-9]*$')"
  MAP="ok"
  { bash "$ROOT/scripts/sim.sh" "$k" "$METHOD" >/dev/null 2>&1 && SIM=PASS; } || SIM=FAIL
  [ "$SIM" = PASS ] || FAILED=1
  printf '%-10s %-6s %-4s %-10s %s\n' "$k" "$(grep -c '<Node ' "$W/dfg.xml")" "${II:--}" "$MAP" "$SIM"
done
echo
[ $FAILED -eq 0 ] && echo "全部通过" || echo "有失败项（见上表）"
exit $FAILED
