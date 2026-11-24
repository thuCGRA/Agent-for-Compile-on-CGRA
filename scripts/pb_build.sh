#!/usr/bin/env bash
# 为 polybench 系列 kernel 生成 DFG。原 stub 用 2-D 指针（DFG pass 不接受），
# 先用 tools/ilp/pb_convert.py 规整成"全局命名数组 + 单层仿射下标"。
# 用法: scripts/pb_build.sh [kernel...]   不给参数则处理 kernels.tsv 里所有 pb_*
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +e    # activate_tc 会打开 set -e

OUTD=/tmp/pb; mkdir -p $OUTD
list="$*"
if [ -z "$list" ]; then list=$(kernels | grep '^pb_'); fi
for k in $list; do
  f="$(kfunc "$k")"
  W="$(kdir "$k")"; mkdir -p "$W"
  cp "$KERNELS/$k/$k.c" "$W/kernel.c" || { echo "NO SRC $k"; continue; }
  python3 tools/ilp/pb_convert.py "$W/kernel.c" >/dev/null 2>&1 || { echo "CONV FAIL $k"; continue; }
  ( cd "$W" || exit 1
    clang -D CGRA_COMPILER -target i386-unknown-linux-gnu \
      -isystem "$ROOT/toolchain/include32" -isystem /usr/include/x86_64-linux-gnu \
      -Wno-implicit-function-declaration -Wno-format -Wno-main-return-type \
      -c -emit-llvm -O2 -fno-tree-vectorize -fno-unroll-loops kernel.c -S -o "$f.ll" 2>$OUTD/$k.err \
    && opt -gvn -mem2reg -memdep -memcpyopt -lcssa -loop-simplify -licm \
         -loop-deletion -indvars -simplifycfg -mergereturn -indvars "$f.ll" -S -o "${f}_opt.ll" 2>>$OUTD/$k.err \
    && opt -load "$OUT/libdfggenPass.so" -fn "$f" -nobanks 2 -banksize 2048 -type PartPred \
         -skeleton "${f}_opt.ll" -S -o "${f}_opt_instrument.ll" 2>>$OUTD/$k.err \
    && cp "${f}_PartPredDFG.xml" dfg.xml && cp "${f}_mem_alloc.txt" mem_alloc.txt )
  if [ -s "$W/dfg.xml" ]; then
    echo "OK   $k nodes=$(grep -c '<Node ' $W/dfg.xml) edges=$(grep -o '<Input idx' $W/dfg.xml | wc -l)"
  else
    echo "FAIL $k : $(head -1 $OUTD/$k.err 2>/dev/null)"
  fi
done
