#!/usr/bin/env bash
# bitstream + trace → 周期级仿真 → matches/mismatches
# 用法: scripts/sim.sh <kernel> [method] [mem_size] [subdir]
#   subdir: 产物目录名，默认 m<method>；钉住布线的产物在 pins<mode>
# 产物: out/<kernel>/m<method>/sim_result.txt
#
# 判定：Mismatches::0 且 Matches>0 即功能正确。其它情况都不算通过。
source "$(dirname "$0")/lib.sh"

k="${1:?用法: sim.sh <kernel> [method] [mem_size]}"
M="${2:-0}"
MEM="${3:-4096}"
SUB="${4:-}"
W="$(kdir "$k")"
[ -n "$SUB" ] && R="$W/$SUB" || R="$W/m$M"
[ -d "$R" ] || { echo "先跑 map.sh $k $M" >&2; exit 2; }
[ -f "$W/trace_0.txt" ] || { echo "先跑 gen_trace.sh $k" >&2; exit 2; }
cd "$R"
BIN="$(ls *.bin 2>/dev/null | head -1)"
[ -n "$BIN" ] || { echo "没有 bitstream（映射失败？）" >&2; exit 3; }

banner "[$k] hycube_simulator -c $BIN"
# -c 配置包(bitstream)  -d 数据 trace  -a 变量基址表  -m 总存储字数  -x/-y 阵列  -t 1=bank 在左侧
"$OUT/hycube_simulator" -c "$BIN" -d "$W/trace_0.txt" -a "$W/mem_alloc.txt" \
  -m "$MEM" -x 4 -y 4 -t 1 | tee sim_result.txt

echo
if grep -q "Mismatches::0" sim_result.txt && ! grep -q "Matches::0," sim_result.txt; then
  echo "PASS: $(grep -o 'Matches::[0-9]*, Mismatches::[0-9]*' sim_result.txt)"
else
  echo "FAIL: $(grep -o 'Matches::[0-9]*, Mismatches::[0-9]*' sim_result.txt || echo '没有仿真结果（可能断言中止）')"
  echo "排错：用 SIMULATOR=1 重跑，会在 stdout 打印逐周期指令流（见 docs/04-sim.md）"
  exit 1
fi
