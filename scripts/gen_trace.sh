#!/usr/bin/env bash
# Generate instrumentation traces (pre/post memory) for one kernel.
#
# Recipe follows morpher's Morpher_DFG_Generator/benchmarks/*/run_pass.sh.
# Differences on this machine:
#  * host binary is native x86_64: gcc-multilib provides 32-bit libc but not the
#    32-bit libstdc++ headers (/usr/include/c++/13/32 is absent), so the C++
#    instrumentation runtime cannot be built with -m32;
#  * the runtime is compiled to a native object with g++ and linked at the
#    object level (llvm-link would need a matching IR triple).
# The trace records 4-byte data values only, so host word size is irrelevant.
set -uo pipefail

KERNEL=${1:?kernel}
SRC=${2:?absolute source .c}
FUNC=${3:-main}
BANKS=${4:-2}
BS=${5:-2048}
OUTROOT=${6:-/home/steve/CGRA_Agent_Compiler/work/traces}

REPO=/home/steve/CGRA_Agent_Compiler
PASS=$REPO/toolchain/out/libdfggenPass.so
TC=/home/steve/miniconda3/envs/cgra-tc/bin
CXX=${CXX:-g++}
CXXFLAGS=${CXXFLAGS:--std=c++14}
D=$OUTROOT/$KERNEL
rm -rf "$D"; mkdir -p "$D"

cd "$D" || exit 1
cp "$SRC" src.c
echo "== [$KERNEL] clang"
"$TC/clang" -D CGRA_COMPILER -c -emit-llvm \
    -O2 -fno-vectorize -fno-slp-vectorize -fno-tree-vectorize -fno-inline -fno-unroll-loops \
    src.c -S -o k.ll 2> clang.err || { echo "CLANG_FAIL"; tail -20 clang.err; exit 10; }

echo "== [$KERNEL] opt canonicalize"
"$TC/opt" -gvn -mem2reg -memdep -memcpyopt -lcssa -loop-simplify -licm \
    -disable-slp-vectorization -loop-deletion -indvars -simplifycfg -mergereturn \
    k.ll -S -o k_opt.ll 2> opt.err || { echo "OPT_FAIL"; tail -20 opt.err; exit 11; }

echo "== [$KERNEL] dfggen -skeleton -fn $FUNC"
"$TC/opt" -load "$PASS" -fn "$FUNC" -nobanks "$BANKS" -banksize "$BS" -type PartPred \
    -skeleton k_opt.ll -S -o k_inst.ll 2> pass.err || { echo "PASS_FAIL"; tail -20 pass.err; exit 12; }

echo "== [$KERNEL] instrumentation + link"
"$CXX" $CXXFLAGS -c "$REPO/toolchain/vendor/dfggen/src/instrumentation/instrumentation.cpp" \
    -I"$REPO/toolchain/vendor/dfggen/include" -o instrumentation.o 2> ins.err || { echo "INS_FAIL"; tail -20 ins.err; exit 13; }
"$TC/llc" -filetype=obj k_inst.ll -o k_inst.o 2> llc.err || { echo "LLC_FAIL"; tail -20 llc.err; exit 15; }
"$CXX" $CXXFLAGS -no-pie k_inst.o instrumentation.o -o final 2> ld.err || { echo "LD_FAIL"; tail -30 ld.err; exit 16; }

echo "== [$KERNEL] run"
rm -rf memtraces; mkdir -p memtraces
timeout 300 ./final > run.out 2> run.err; RC=$?
# Benchmarks often `return` a checksum from main, so a non-zero exit is normal;
# the trace files are what matter.
echo "run_exit=$RC"

N=$(ls memtraces/*.txt 2>/dev/null | wc -l)
echo "TRACE_COUNT=$N"
[ "$N" -gt 0 ] || { echo "NO_TRACES"; tail -5 run.out; exit 18; }

i=0
for f in $(ls memtraces/*.txt | head -4); do
    i=$((i+1))
    cp "$f" "$OUTROOT/$KERNEL/${KERNEL}_trace_${i}.txt"
done
echo "OK $KERNEL traces=$i -> $OUTROOT/$KERNEL"
