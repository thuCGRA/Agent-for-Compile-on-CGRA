#!/bin/bash
# Generate PartPred DFG xml for every morpher_benchmarks kernel: clang -> opt -> dfggen pass.
# The pass alone emits <kernel>_PartPredDFG.xml + mem_alloc; no link/run needed.
set -o pipefail
source /home/steve/miniconda3/etc/profile.d/conda.sh
conda activate cgra-tc
REPO=/home/steve/cac-armB
GEN=$REPO/toolchain/vendor/dfggen
PASS=$REPO/toolchain/out/libdfggenPass.so
OUT=$REPO/work/armB/dfggen

mkdir -p "$OUT"
for dir in "$GEN"/benchmarks/morpher_benchmarks/*/; do
  K=$(basename "$dir")
  SRC=$(ls "$dir"/*.c 2>/dev/null | head -1)
  [ -z "$SRC" ] && { echo "SKIP $K (no .c)"; continue; }
  FN=$K
  WD=$OUT/$K
  rm -rf "$WD"; mkdir -p "$WD"
  cp "$SRC" "$WD/$K.c"
  ( cd "$WD" || exit 1
    clang -D CGRA_COMPILER -c -emit-llvm -O2 -fno-vectorize -fno-slp-vectorize -fno-tree-vectorize \
          -fno-inline -fno-unroll-loops "$K.c" -S -o "$K.ll" 2>/dev/null
    opt -gvn -mem2reg -memdep -memcpyopt -lcssa -loop-simplify -licm -disable-slp-vectorization \
        -loop-deletion -indvars -simplifycfg -mergereturn "$K.ll" -S -o "${K}_opt.ll" 2>/dev/null
    opt -load "$PASS" -fn "$FN" -nobanks 2 -banksize 2048 -skeleton "${K}_opt.ll" -S -o "${K}_inst.ll" \
        > pass.log 2>&1
    rc=$?
    n=$(grep -c "<Node " "${K}_PartPredDFG.xml" 2>/dev/null || echo 0)
    e=$(grep -c "<Input " "${K}_PartPredDFG.xml" 2>/dev/null || echo 0)
    printf "%-12s fn=%-12s rc=%d nodes=%-4s inputs=%-4s %s\n" "$K" "$FN" "$rc" "$n" "$e" \
      "$(grep -o 'DFG node count: [0-9]*' pass.log | head -1)"
  )
done
