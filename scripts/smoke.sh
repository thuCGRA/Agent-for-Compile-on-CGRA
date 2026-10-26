#!/usr/bin/env bash
# 端到端冒烟：clang -> DFG(pass) -> mem 架构 -> mapper -> 模拟器，要求 Mismatches::0。
# 用法：scripts/smoke.sh [array_add]
# 说明：trace 由插桩后的 32-bit 程序产生，P1 的 runner 会接管这一步；这里若 work/smoke 下
#       已有 <kernel>_trace_0.txt 就直接用于仿真，没有则只跑到映射并标记 SKIP。
set -o pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/toolchain/out"
W="$ROOT/work/smoke"
APP="${1:-array_add}"
KERNEL="${2:-$APP}"
BANKS=2
BSIZE=2048

source /home/steve/miniconda3/etc/profile.d/conda.sh
conda activate cgra-tc
export PATH="$ROOT/toolchain/bin:$PATH"
set -u

mkdir -p "$W"; cd "$W"
fail=0
ok()  { echo "    PASS  $1"; }
bad() { echo "    FAIL  $1"; fail=1; }

echo "[A] ABI：opt 能加载 DFG pass"
if opt -load "$OUT/libdfggenPass.so" -help 2>/dev/null | grep -q -- "--fn="; then
  ok "pass 与 opt 同源"
else
  bad "pass 加载失败"
fi

echo "[B] DFG 生成（$APP）"
SRC="$ROOT/toolchain/vendor/dfggen/benchmarks/morpher_benchmarks/$APP/$KERNEL.c"
[ -f "$SRC" ] || SRC="$W/$KERNEL.c"
cp "$SRC" "$KERNEL.c"
clang -D CGRA_COMPILER -target i386-unknown-linux-gnu -g0 \
  -isystem "$ROOT/toolchain/include32" -isystem /usr/include/x86_64-linux-gnu \
  -O2 -fno-tree-vectorize -fno-unroll-loops -S -emit-llvm "$KERNEL.c" -o "$KERNEL.ll" 2>&1 | tail -3
opt -gvn -mem2reg -memdep -memcpyopt -lcssa -loop-simplify -licm -loop-deletion \
    -indvars -simplifycfg -mergereturn "$KERNEL.ll" -S -o "${KERNEL}_opt.ll"
opt -load "$OUT/libdfggenPass.so" -fn "$KERNEL" -nobanks "$BANKS" -banksize "$BSIZE" \
    -type PartPred -skeleton "${KERNEL}_opt.ll" -S -o "${KERNEL}_opt_instrument.ll" 2>&1 | tail -3
DFG="${KERNEL}_PartPredDFG.xml"
if [ -f "$DFG" ]; then ok "nodes=$(grep -c '<Node ' "$DFG")"; else bad "未生成 DFG"; fi

echo "[C] 生成带 bank 布局的 arch（$BANKS banks x $BSIZE）"
PY="$(command -v python3 || echo python)"
$PY "$ROOT/toolchain/vendor/mapper/update_mem_alloc.py" \
  "$ROOT/toolchain/vendor/mapper/json_arch/hycube_original.json" \
  "${KERNEL}_mem_alloc.txt" "$BSIZE" "$BANKS" "${KERNEL}_mem.json" > /dev/null 2>&1 \
  && ok "arch=${KERNEL}_mem.json" || bad "update_mem_alloc.py 失败"

echo "[D] 映射（method 0 PathFinder，4x4）"
"$OUT/cgra_xml_mapper" -d "$DFG" -x 4 -y 4 -j "${KERNEL}_mem.json" \
  -i 2 -t HyCUBE_4REG -m 0 > mapper.log 2>&1
grep -E "Map Success|Elapsed" mapper.log | tail -2
grep -q "Map Success" mapper.log && ok "映射成功" || bad "映射失败"

echo "[E] 仿真（比对测试向量）"
BIN="$(ls "${DFG}"*binary.bin 2>/dev/null | head -1)"
if [ -z "$BIN" ]; then
  bad "没有 binary.bin"
elif [ ! -f "${KERNEL}_trace_0.txt" ]; then
  echo "    SKIP  缺 ${KERNEL}_trace_0.txt（trace 生成归 P1 runner）"
else
  "$OUT/hycube_simulator" -x 4 -y 4 -c "$BIN" -d "${KERNEL}_trace_0.txt" \
    -a "${KERNEL}_mem_alloc.txt" -m 4096 > sim.log 2>&1
  grep -E "Simulation Result|CGRA cycles" sim.log | tail -2
  grep -q "Mismatches::0" sim.log && ok "零失配" || bad "仿真失配"
fi

echo
if [ $fail -eq 0 ]; then echo "SMOKE OK"; else echo "SMOKE FAILED"; fi
exit $fail