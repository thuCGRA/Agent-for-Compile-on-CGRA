#!/usr/bin/env bash
# 把 Morpher 上游的全部 testcase 装进本工程的 kernels/ 并重写 kernels.tsv。
#
# 用法:
#   scripts/install_benchmarks.sh              # 装进当前工程
#   scripts/install_benchmarks.sh <工程根>      # 装进指定工程
#
# 环境变量:
#   MORPHER_HOME   上游 morpher 仓库（默认 ~/morpher）
#
# 做的事:
#   1. 按 scripts/benchmarks.tsv 把每个 .c 拷成 kernels/<kernel>/<kernel>.c
#      （文件内容与上游逐字节相同，不在这里改）
#   2. microspeech_* 额外带上它运行时要读的 param_conv.txt / param_fc.txt，
#      并把 microspeech_test_data/ 放到 kernels/ 下（源码里写死了
#      "../microspeech_test_data/"）
#   3. 重写 scripts/kernels.tsv（只留 kernel<TAB>function 两列 + 表头注释）
#
# 幂等：重复跑只是重新覆盖同样的文件。
set -euo pipefail

ROOT="$(cd "${1:-$(dirname "${BASH_SOURCE[0]}")/..}" && pwd)"
BENCH_MANIFEST="$ROOT/scripts/benchmarks.tsv"
MORPHER_HOME="${MORPHER_HOME:-$HOME/morpher}"
UPSTREAM="$MORPHER_HOME/Morpher_DFG_Generator/benchmarks"

[ -f "$BENCH_MANIFEST" ] || { echo "缺 $BENCH_MANIFEST" >&2; exit 2; }
[ -d "$UPSTREAM" ] || { echo "找不到上游 benchmarks: $UPSTREAM" >&2
  echo "设 MORPHER_HOME 指向上游 morpher 仓库" >&2; exit 2; }

mkdir -p "$ROOT/kernels"

n=0
missing=0
while IFS=$'\t' read -r k fn src; do
  case "$k" in ''|'#'*) continue ;; esac
  [ -f "$UPSTREAM/$src" ] || { echo "  跳过 $k：上游没有 $src" >&2; missing=$((missing+1)); continue; }
  d="$ROOT/kernels/$k"
  mkdir -p "$d"
  cp "$UPSTREAM/$src" "$d/$k.c"
  # microspeech 的 main() 要读这两个参数文件（同目录），
  # 以及 ../microspeech_test_data/ 下的测试数据
  if [ "${k#microspeech_}" != "$k" ]; then
    srcdir="$(dirname "$UPSTREAM/$src")"
    for f in param_conv.txt param_fc.txt; do
      [ -f "$srcdir/$f" ] && cp "$srcdir/$f" "$d/$f"
    done
  fi
  n=$((n+1))
done < "$BENCH_MANIFEST"

# microspeech_test_data 只放一份在 kernels/ 下（源码里是 "../microspeech_test_data/"）
DATA="$UPSTREAM/woset_benchmarks/microspeech_test_data"
if [ -d "$DATA" ]; then
  if [ ! -d "$ROOT/kernels/microspeech_test_data" ]; then
    cp -r "$DATA" "$ROOT/kernels/microspeech_test_data"
  fi
fi

# 重写 kernels.tsv：保留表头，条目按 benchmarks.tsv 顺序
{
  echo "# kernel<TAB>进入 DFG 的函数名"
  echo "# 由 scripts/install_benchmarks.sh 从 scripts/benchmarks.tsv 生成，别手改"
  echo "# 注意：cgrame/polybench 那批的循环在 main() 里（没有独立函数），所以 -fn main"
  awk -F'\t' '/^[^#]/ && NF>=2 {printf "%s\t%s\n", $1, $2}' "$BENCH_MANIFEST"
} > "$ROOT/scripts/kernels.tsv"

echo "装好 $n 个 testcase 到 $ROOT/kernels/（缺 $missing 个）"
echo "kernels.tsv 现有 $(grep -vc '^#' "$ROOT/scripts/kernels.tsv") 条"
