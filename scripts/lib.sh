#!/usr/bin/env bash
# 公共环境与路径。所有脚本 source 它。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/src"
OUT="$ROOT/out"
ARCH="$ROOT/arch"
KERNELS="$ROOT/kernels"
CONDA_ENV="${CONDA_ENV:-cgra-tc}"

# 激活编译/运行用 conda 环境（llvm 10.0.1 + clang 10 + GCC 7.5 都在里面）。
# 注意：系统里另有 /usr/lib/llvm-10（10.0.0）与 clang-10，所以不能用 `command -v llvm-config`
# 判断是否已激活——必须看 CONDA_DEFAULT_ENV。混用系统 LLVM 编出的 pass 与 conda opt 版本不一致。
activate_tc() {
  if [ "${CONDA_DEFAULT_ENV:-}" != "$CONDA_ENV" ]; then
    # 非交互 ssh 下 conda 不在 PATH，先手工加载 conda.sh
    if ! command -v conda >/dev/null 2>&1; then
      for c in "$HOME/miniconda3/etc/profile.d/conda.sh" \
               "$HOME/anaconda3/etc/profile.d/conda.sh" \
               /opt/conda/etc/profile.d/conda.sh; do
        # shellcheck disable=SC1090
        [ -f "$c" ] && { . "$c"; break; }
      done
    fi
    command -v conda >/dev/null 2>&1 || { echo "找不到 conda，请先 conda activate $CONDA_ENV" >&2; exit 2; }
    # conda 的 activate.d 脚本里有大量未绑定变量，会撞上本脚本的 set -u，临时关掉
    set +eu
    conda activate "$CONDA_ENV"
    _rc=$?
    set -eu
    [ $_rc -eq 0 ] || { echo "conda activate $CONDA_ENV 失败" >&2; exit 2; }
  fi
  # conda activate 会注入 CC/CXX/CFLAGS，其中 -DNDEBUG 会删掉仿真器里带副作用的 assert
  # （后果是仿真死循环），所以统一清掉，编译时显式指定编译器与选项。
  unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CMAKE_ARGS CC_FOR_BUILD CXX_FOR_BUILD

  : "${CONDA_PREFIX:?CONDA_PREFIX 未设置}"
  GXX="${GXX:-$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-g++}"
  GCC="${GCC:-$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-gcc}"
  export GXX GCC
}

kernels() { awk '!/^#/ && NF>=2 {print $1}' "$ROOT/scripts/kernels.tsv"; }
kfunc()   { awk -v k="$1" '!/^#/ && $1==k {print $2}' "$ROOT/scripts/kernels.tsv"; }

# 每个 kernel 一个固定工作目录：out/<kernel>/
kdir() { echo "$OUT/$1"; }

banner() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
