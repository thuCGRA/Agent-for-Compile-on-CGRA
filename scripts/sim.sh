#!/usr/bin/env bash
# bitstream + trace → 周期级仿真 → matches/mismatches
# 用法: scripts/sim.sh <kernel> [method] [mem_size] [dims]
# 产物: out/<kernel>/m<method>[_<X>x<Y>]/sim_result.txt
#
# 判定：Mismatches::0 且 Matches>0 即功能正确。其它情况都不算通过。
#
# 三个参数必须与 arch.json 一致，否则不是"仿真失败"而是"喂错了"：
#   -x/-y 阵列尺寸、-m 总字数（= banks × bank_size）、-t bank 在哪一侧（1 左 / 2 两侧）
source "$(dirname "$0")/lib.sh"

k="${1:?用法: sim.sh <kernel> [method] [mem_size] [dims]}"
M="${2:-0}"
W="$(kdir "$k")"
XD=4; YD=4; NOBANKS=2; BANKSIZE=2048
if [ -f "$W/dims" ]; then read -r XD YD NOBANKS BANKSIZE < "$W/dims"; fi
DIMS="${4:-${XD}x${YD}}"
MEM="${3:-$((NOBANKS * BANKSIZE))}"
# bank 在左侧（4x4）还是两侧（8x8 的 x=0 与 x=7 都有 PE_MEM）
if [ "$DIMS" = "8x8" ]; then SIDE="${SIM_T:-2}"; else SIDE="${SIM_T:-1}"; fi

R="$(mdir "$k" "$M")"
[ -d "$R" ] || { echo "先跑 map.sh $k $M" >&2; exit 2; }
[ -f "$W/trace_0.txt" ] || { echo "先跑 gen_trace.sh $k" >&2; exit 2; }
cd "$R"
BIN="$(ls *.bin 2>/dev/null | head -1)"
[ -n "$BIN" ] || { echo "没有 bitstream（映射失败？）" >&2; exit 3; }

banner "[$k] hycube_simulator -c $BIN  ($DIMS, -m $MEM, -t $SIDE)"
"$OUT/hycube_simulator" -c "$BIN" -d "$W/trace_0.txt" -a "$W/mem_alloc.txt" \
  -m "$MEM" -x "$XD" -y "$YD" -t "$SIDE" | tee sim_result.txt

echo
if grep -q "Mismatches::0" sim_result.txt && ! grep -q "Matches::0," sim_result.txt; then
  echo "PASS: $(grep -o 'Matches::[0-9]*, Mismatches::[0-9]*' sim_result.txt)"
else
  echo "FAIL: $(grep -o 'Matches::[0-9]*, Mismatches::[0-9]*' sim_result.txt || echo '没有仿真结果（可能断言中止）')"
  echo "排错：用 SIMULATOR=1 重跑，会在 stdout 打印逐周期指令流（见 docs/04-sim.md）"
  exit 1
fi
