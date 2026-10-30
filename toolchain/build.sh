#!/usr/bin/env bash
# 在 cgra-tc 环境里编译三件套，产物进 toolchain/out/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${CONDA_PREFIX:?请先 conda activate cgra-tc}"
command -v llvm-config >/dev/null || { echo "llvm-config 不在 PATH，确认已激活 cgra-tc" >&2; exit 2; }

OUT="$ROOT/toolchain/out"; BUILD="$ROOT/build"
mkdir -p "$OUT" "$BUILD"

# 装 gcc_linux-64 之后，conda activate 会污染 CC/CXX/CFLAGS 甚至 CPPFLAGS（含 -DNDEBUG）。
# -DNDEBUG 会删掉模拟器里带副作用的 assert，导致死循环（见下面 HOSTFLAGS 注释），所以这里全部清掉，
# 后面每个目标都显式指定编译器。
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CMAKE_ARGS CC_FOR_BUILD CXX_FOR_BUILD

RTTI_FLAG="-frtti"
[ "$(llvm-config --has-rtti)" = "NO" ] && RTTI_FLAG="-fno-rtti"
CC="$CONDA_PREFIX/bin/clang"
CXX="$ROOT/toolchain/bin/clang++"
[ -x "$CXX" ] || CXX="$CONDA_PREFIX/bin/clang++"
[ -x "$CXX" ] || { echo "找不到 C++ 编译器（clang++ 或 toolchain/bin/clang++）" >&2; exit 2; }

apply_patch() {
  local dir="$1" p="$2"
  local marker="$dir/.$(basename "$p").applied"
  [ -f "$p" ] || { echo "  (无 patch) $p"; return 0; }
  if [ -f "$marker" ]; then echo "  已应用 $(basename "$p")"; return 0; fi
  echo "  应用 $(basename "$p")"
  ( cd "$dir" && patch -p1 -N --no-backup-if-mismatch < "$p" ) || return 1
  touch "$marker"
}
echo "== LLVM $(llvm-config --version)  rtti=$(llvm-config --has-rtti)  cxx=$CXX"

echo "== [1/3] DFG pass"
apply_patch "$ROOT/toolchain/vendor/dfggen" "$ROOT/toolchain/patches/0001-dfggen.patch"
cmake -S "$ROOT/toolchain/vendor/dfggen" -B "$BUILD/dfggen" \
  -DLLVM_DIR="$CONDA_PREFIX/lib/cmake/llvm" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
  -DCMAKE_C_FLAGS="-B/usr/bin" -DCMAKE_CXX_FLAGS="$RTTI_FLAG -B/usr/bin -D_GLIBCXX_USE_CXX11_ABI=1" > "$BUILD/dfggen-config.log" 2>&1 || { tail -30 "$BUILD/dfggen-config.log"; exit 3; }
cmake --build "$BUILD/dfggen" -j"$(nproc)" > "$BUILD/dfggen-build.log" 2>&1 || { tail -40 "$BUILD/dfggen-build.log"; exit 4; }
cp "$BUILD"/dfggen/src/libdfggenPass.so "$OUT/"

# mapper / simulator 用 GCC 7.5（upstream 的 Dockerfile 是 ubuntu:18.04 + gcc 7.5）。
# 用 GCC 13 编译会在 arch JSON 解析里踩到 latent UB（PreprocessPattern 里 SIGSEGV/SIGILL），
# 详见 docs/ENVIRONMENT.md 的「为什么锁 GCC 7」。
GXX="${GXX:-$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-g++}"
GCC="${GCC:-$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-gcc}"
if [ -x "$GXX" ] && [ -x "$GCC" ]; then
  echo "== host 编译器 $("$GXX" --version | head -1)"
else
  GXX=g++; GCC=gcc
  echo "== 警告：未找到 conda 的 GCC 7.5，回退到系统 $($GXX --version | head -1)"
fi
# 两个坑，都跟 upstream 的代码质量有关，记在 docs/ENVIRONMENT.md：
# 1) conda 的 GCC 7 自带 ld 太老，认不了 Ubuntu 24.04 的 .relr.dyn，用 -B/usr/bin 换系统 binutils。
# 2) 必须保留 assert：Release 的 -DNDEBUG 会删掉带副作用的断言，模拟器在某些 binary.bin 上直接死循环
#    （-O3/-O2 + NDEBUG 复现，-O2 不带 NDEBUG 正常）。所以用 Debug 构建类型 + 手动 -O2。
HOSTFLAGS="-B/usr/bin -O2"
echo "== [2/3] mapper"
apply_patch "$ROOT/toolchain/vendor/mapper" "$ROOT/toolchain/patches/0002-mapper.patch"
apply_patch "$ROOT/toolchain/vendor/mapper" "$ROOT/toolchain/patches/0004-mapper-dump.patch"
apply_patch "$ROOT/toolchain/vendor/mapper" "$ROOT/toolchain/patches/0005-mapper-phase-free-pins.patch"
cmake -S "$ROOT/toolchain/vendor/mapper" -B "$BUILD/mapper" -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_COMPILER="$GCC" -DCMAKE_CXX_COMPILER="$GXX" \
  -DCMAKE_C_FLAGS="$HOSTFLAGS" -DCMAKE_CXX_FLAGS="$HOSTFLAGS" > "$BUILD/mapper-config.log" 2>&1 || { tail -30 "$BUILD/mapper-config.log"; exit 5; }
cmake --build "$BUILD/mapper" -j"$(nproc)" > "$BUILD/mapper-build.log" 2>&1 || { tail -40 "$BUILD/mapper-build.log"; exit 6; }
cp "$BUILD"/mapper/src/cgra_xml_mapper "$OUT/"

echo "== [3/3] simulator"
apply_patch "$ROOT/toolchain/vendor/simulator" "$ROOT/toolchain/patches/0003-simulator.patch"
cmake -S "$ROOT/toolchain/vendor/simulator/src" -B "$BUILD/simulator" -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_COMPILER="$GCC" -DCMAKE_CXX_COMPILER="$GXX" \
  -DCMAKE_C_FLAGS="$HOSTFLAGS" -DCMAKE_CXX_FLAGS="$HOSTFLAGS" > "$BUILD/simulator-config.log" 2>&1 || { tail -30 "$BUILD/simulator-config.log"; exit 7; }
cmake --build "$BUILD/simulator" -j"$(nproc)" > "$BUILD/simulator-build.log" 2>&1 || { tail -40 "$BUILD/simulator-build.log"; exit 8; }
SIM="$(find "$BUILD/simulator" -maxdepth 2 -type f -name hycube_simulator | head -1)"
[ -n "$SIM" ] || { echo "找不到 hycube_simulator" >&2; exit 9; }
cp "$SIM" "$OUT/"

echo
echo "== 产物"
sha256sum "$OUT"/* | tee "$OUT/SHA256SUMS"