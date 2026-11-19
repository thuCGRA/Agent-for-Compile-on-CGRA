#!/usr/bin/env bash
# 生成数据存储器 trace（仿真器的激励）
# 用法: scripts/gen_trace.sh <kernel>
# 产物: out/<kernel>/trace_0.txt（另有 memtraces/ 原始文件）
#
# 原理：DFG pass 会把每个内存访问插桩，再链接 instrumentation.cpp 提供的桩函数，
# 在 host 上真正跑一遍程序，把每次访存的字节值按地址记进 memtraces/*.txt。
# 仿真器读这个文件当"预期数据"（DMEM 初值 + 参考输出）。
#
# 为什么要单独再跑一遍 pass：gen_dfg.sh 生成的 IR 是 -target i386 的（为了 DFG 里
# 指针 32 位），但 i386 object 在 x86_64 host 上没法链接（缺 32 位 libstdc++）。
# trace 只记 4 字节数据值、与字宽无关，所以这里用 **native** 重新编译 + 同一个 pass
# 插桩，产物放在 trace_build/ 子目录，绝不覆盖 out/<k>/dfg.xml。
source "$(dirname "$0")/lib.sh"
activate_tc

k="${1:?用法: gen_trace.sh <kernel>}"
f="$(kfunc "$k")"
NOBANKS="${2:-2}"
BANKSIZE="${3:-2048}"
W="$(kdir "$k")"
[ -f "$W/kernel.c" ] || { echo "先跑 gen_dfg.sh $k" >&2; exit 2; }

T="$W/trace_build"; rm -rf "$T"; mkdir -p "$T"; cd "$T"
cp "$W/kernel.c" .

banner "[$k] 1/4 native C -> IR（trace 与字宽无关，故不用 i386 目标）"
clang -D CGRA_COMPILER -Wno-implicit-function-declaration -Wno-format \
      -Wno-main-return-type -c -emit-llvm -O2 -fno-tree-vectorize \
      -fno-unroll-loops kernel.c -S -o "$f.ll"

banner "[$k] 2/4 IR 规范化 + DFG pass 插桩（-fn $f）"
opt -gvn -mem2reg -memdep -memcpyopt -lcssa -loop-simplify -licm \
    -loop-deletion -indvars -simplifycfg -mergereturn \
    "$f.ll" -S -o "${f}_opt.ll"
opt -load "$OUT/libdfggenPass.so" -fn "$f" \
    -nobanks "$NOBANKS" -banksize "$BANKSIZE" -type PartPred \
    -skeleton "${f}_opt.ll" -S -o "${f}_inst.ll"

banner "[$k] 3/4 编译插桩 runtime 并链接（object 层，native GCC 7.5）"
"$GXX" -std=c++14 -c "$SRC/dfggen/src/instrumentation/instrumentation.cpp" \
       -I"$SRC/dfggen/include" -o instrumentation.o
llc -filetype=obj "${f}_inst.ll" -o inst.o
"$GXX" -std=c++14 -no-pie inst.o instrumentation.o -o final

banner "[$k] 4/4 运行插桩程序，写出 trace"
rm -rf memtraces; mkdir -p memtraces
# 注意 set -e：直接写 `timeout ... ./final` 会在程序非 0 退出时把整个脚本带走，
# 所以用 if/else 接住退出码。
if timeout 300 ./final > final_log.txt 2> final_err_log.txt; then rc=0; else rc=$?; fi
# benchmark 的 main 常常 return 一个校验和，非 0 退出是正常的；只看有没有 trace。
echo "  插桩程序退出码 $rc（非 0 正常）"
# 注意：不要用 `ls memtraces/*.txt | head -1`——pipefail 会把 head 提前关闭导致的
# SIGPIPE(141) 当成失败，脚本在 cp 之前就退出（这个坑踩过一次）。
shopt -s nullglob
files=(memtraces/*.txt)
shopt -u nullglob
if [ ${#files[@]} -eq 0 ]; then
  echo "没生成 trace" >&2; tail -5 final_err_log.txt; exit 3
fi
echo "  trace 文件数: ${#files[@]}（取第一个）"
cp "${files[0]}" "$W/trace_0.txt"
echo "trace: $W/trace_0.txt  ($(wc -l < "$W/trace_0.txt") 行)"
