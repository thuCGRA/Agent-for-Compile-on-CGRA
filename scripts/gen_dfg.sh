#!/usr/bin/env bash
# C → DFG XML + mem_alloc.txt
# 用法: scripts/gen_dfg.sh <kernel> [banks] [banksize]
# 产物: out/<kernel>/{dfg.xml, mem_alloc.txt, *.dot}
#
# 对应上游 run_morpher.py 的 "Running Morpher_DFG_Generator" 段，但把
# 那几个 os.chdir / 固定 benchmarks 路径全换成显式工作目录。
source "$(dirname "$0")/lib.sh"
activate_tc

k="${1:?用法: gen_dfg.sh <kernel> [banks] [banksize]}"
NOBANKS="${2:-2}"
BANKSIZE="${3:-2048}"
f="$(kfunc "$k")"
[ -n "$f" ] || { echo "kernels.tsv 里没有 $k" >&2; exit 2; }

W="$(kdir "$k")"; mkdir -p "$W"
cp "$KERNELS/$k/$k.c" "$W/kernel.c"
cd "$W"

banner "[$k] 1/3 C -> LLVM IR   (function=$f)"
# -target i386-unknown-linux-gnu：让指针 32 位，DFG 里 base pointer 的宽度才和硬件一致。
# -isystem toolchain/include32：只有 -target i386 才会去找 gnu/stubs-32.h，而 conda 的
#   sysroot 里没有 32 位 glibc 头；这里放一个空的 gnu/stubs-32.h 顶掉它（我们只需要
#   编译过、不需要真的链接 32 位 libc）。
# -isystem /usr/include/x86_64-linux-gnu：补 bits/ 下多架构头（host 提供的）。
# 注意不要加 -fno-inline（上游 run_pass.sh 加了，但 run_morpher.py 没加，二者结果不同）；
# 这里跟 run_morpher.py 保持一致。
clang -D CGRA_COMPILER -target i386-unknown-linux-gnu \
      -isystem "$ROOT/toolchain/include32" \
      -isystem /usr/include/x86_64-linux-gnu \
      -Wno-implicit-function-declaration -Wno-format -Wno-main-return-type \
      -c -emit-llvm -O2 -fno-tree-vectorize -fno-unroll-loops kernel.c -S -o "$f.ll"

banner "[$k] 2/3 IR 规范化"
opt -gvn -mem2reg -memdep -memcpyopt -lcssa -loop-simplify -licm \
    -loop-deletion -indvars -simplifycfg -mergereturn -indvars \
    "$f.ll" -S -o "${f}_opt.ll"

banner "[$k] 3/3 DFG pass (-nobanks $NOBANKS -banksize $BANKSIZE -type PartPred)"
opt -load "$OUT/libdfggenPass.so" -fn "$f" \
    -nobanks "$NOBANKS" -banksize "$BANKSIZE" -type PartPred \
    -skeleton "${f}_opt.ll" -S -o "${f}_opt_instrument.ll"

cp "${f}_PartPredDFG.xml" dfg.xml
cp "${f}_mem_alloc.txt"   mem_alloc.txt
# f == "kernel" 时（opencgra 的 fir）目标就是它自己，ln 会报
# "为同一文件"。这种情况不用建链接。
[ "$f" = "kernel" ] || ln -sfn "${f}_opt_instrument.ll" kernel_opt_instrument.ll
echo
echo "节点数: $(grep -c '<Node ' dfg.xml)   边(Inputs 内 Input 项): $(grep -o '<Input idx' dfg.xml | wc -l)"
echo "产物: $W/dfg.xml  $W/mem_alloc.txt  $(ls *.dot 2>/dev/null || true)"
