#!/usr/bin/env bash
# 构建 Morpher 三件套 → out/{libdfggenPass.so, cgra_xml_mapper, hycube_simulator}
#
# 三个组件对编译器的要求不一样，原因都是上游代码质量，别随手改：
#   dfggen    : 必须用 LLVM 10 的 clang（pass 链接 LLVM，版本必须与 opt 一致）
#   mapper    : 必须用 GCC 7.5。GCC 13 编出来在解析 ADL JSON 时踩 latent UB（PreprocessPattern
#               里 SIGSEGV/SIGILL）。upstream Dockerfile 就是 ubuntu18.04 + gcc7.5。
#   simulator : 同上（且必须保留 assert，见下面 HOSTFLAGS）
source "$(dirname "$0")/lib.sh"
activate_tc

# 注意：不能用本文件的绝对路径判断（$0 可能是脚本副本），直接用 CONDA_PREFIX 里的
# llvm-config，并确认版本是 10.0.1（系统另有 /usr/lib/llvm-10 的 10.0.0，
# 混用会让 pass 与 opt 版本不一致）。
[ -x "$CONDA_PREFIX/bin/llvm-config" ] || { echo "找不到 $CONDA_PREFIX/bin/llvm-config，检查 conda activate $CONDA_ENV" >&2; exit 2; }
export PATH="$CONDA_PREFIX/bin:$PATH"
[ -x "$GXX" ] || { echo "找不到 $GXX（cgra-tc 环境里应有 x86_64-conda-linux-gnu-g++ 7.5）" >&2; exit 2; }
mkdir -p "$OUT" "$ROOT/build"

RTTI_FLAG="-frtti"
[ "$(llvm-config --has-rtti)" = "NO" ] && RTTI_FLAG="-fno-rtti"

# -B/usr/bin : conda 的 GCC7 自带 ld 太老，认不了 Ubuntu 20.04+ 的 .relr.dyn
# -O2 + Debug: 不能用 Release，-DNDEBUG 会删掉模拟器里带副作用的 assert → 死循环
HOSTFLAGS="-B/usr/bin -O2"

echo "== LLVM $(llvm-config --version)   rtti=$(llvm-config --has-rtti)"
echo "== dfggen clang: $("$CONDA_PREFIX/bin/clang" --version | head -1)"
echo "== host gcc   : $("$GXX" --version | head -1)"

# conda 的 clang 包只装了 clang 驱动，没有 clang++ 这个可执行文件（10.0.1 就是这样）。
# 用 --driver-mode=g++ 得到 clang++ 语义（链接时自动加 -lstdc++、按扩展名判定语言）。
# 不要用 -x c++：它会对后续所有输入生效，链接阶段会把 .o 当源码编译。
CX="$ROOT/build/clang++-wrapper"
printf '#!/usr/bin/env bash\nexec "%s/bin/clang" --driver-mode=g++ "$@"\n' "$CONDA_PREFIX" > "$CX"
chmod +x "$CX"

# 只在必要处改上游源码，且全部以 patch 形式留下痕迹（可复现、可审）
apply_patch() {
  local dir="$1" p="$2"
  local marker="$dir/.$(basename "$p").applied"
  local name
  name="$(basename "$p")"
  [ -f "$p" ] || { echo "  (no patch) $p"; return 0; }
  [ -f "$marker" ] && { echo "  skip $name (marker)"; return 0; }

  # Idempotent: the committed tree may already carry the change (git archive of
  # a fork), so probe before touching anything. Forward dry-run applies; reverse
  # dry-run means "already applied, nothing to do"; both failing is a real
  # conflict and must stop the build.
  if ( cd "$dir" && patch -p1 -N --dry-run --no-backup-if-mismatch < "$p" ) >/dev/null 2>&1; then
    echo "  apply $name"
    ( cd "$dir" && patch -p1 -N --no-backup-if-mismatch < "$p" ) || return 1
  elif ( cd "$dir" && patch -p1 -R --dry-run --no-backup-if-mismatch < "$p" ) >/dev/null 2>&1; then
    echo "  skip $name (already applied)"
  else
    echo "  ERROR $name does not apply cleanly to $dir" >&2
    ( cd "$dir" && patch -p1 -N --dry-run --no-backup-if-mismatch < "$p" ) >&2 || true
    return 1
  fi
  touch "$marker"
}

banner "[1/3] DFG pass (libdfggenPass.so)"
cmake -S "$SRC/dfggen" -B "$ROOT/build/dfggen" \
  -DLLVM_DIR="$CONDA_PREFIX/lib/cmake/llvm" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="$CONDA_PREFIX/bin/clang" \
  -DCMAKE_CXX_COMPILER="$CX" \
  -DCMAKE_C_FLAGS="-B/usr/bin" \
  -DCMAKE_CXX_FLAGS="$RTTI_FLAG -B/usr/bin -D_GLIBCXX_USE_CXX11_ABI=1" \
  > "$ROOT/build/dfggen-config.log" 2>&1 || { tail -30 "$ROOT/build/dfggen-config.log"; exit 3; }
cmake --build "$ROOT/build/dfggen" -j"$(nproc)" > "$ROOT/build/dfggen-build.log" 2>&1 \
  || { tail -40 "$ROOT/build/dfggen-build.log"; exit 4; }
cp "$ROOT/build/dfggen/src/libdfggenPass.so" "$OUT/"

banner "[2/3] mapper (cgra_xml_mapper)"
apply_patch "$SRC/mapper" "$ROOT/toolchain/patches/0001-mapper-no-hardcoded-compiler.patch"
apply_patch "$SRC/mapper" "$ROOT/toolchain/patches/0002-pinned-mapper.patch"
cmake -S "$SRC/mapper" -B "$ROOT/build/mapper" -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_COMPILER="$GCC" -DCMAKE_CXX_COMPILER="$GXX" \
  -DCMAKE_C_FLAGS="$HOSTFLAGS" -DCMAKE_CXX_FLAGS="$HOSTFLAGS" \
  > "$ROOT/build/mapper-config.log" 2>&1 || { tail -30 "$ROOT/build/mapper-config.log"; exit 5; }
cmake --build "$ROOT/build/mapper" -j"$(nproc)" > "$ROOT/build/mapper-build.log" 2>&1 \
  || { tail -40 "$ROOT/build/mapper-build.log"; exit 6; }
cp "$ROOT/build/mapper/src/cgra_xml_mapper" "$OUT/"

banner "[3/3] simulator (hycube_simulator)"
cmake -S "$SRC/simulator" -B "$ROOT/build/simulator" -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_COMPILER="$GCC" -DCMAKE_CXX_COMPILER="$GXX" \
  -DCMAKE_C_FLAGS="$HOSTFLAGS" -DCMAKE_CXX_FLAGS="$HOSTFLAGS" \
  > "$ROOT/build/simulator-config.log" 2>&1 || { tail -30 "$ROOT/build/simulator-config.log"; exit 7; }
cmake --build "$ROOT/build/simulator" -j"$(nproc)" > "$ROOT/build/simulator-build.log" 2>&1 \
  || { tail -40 "$ROOT/build/simulator-build.log"; exit 8; }
SIM="$(find "$ROOT/build/simulator" -maxdepth 2 -type f -name hycube_simulator | head -1)"
[ -n "$SIM" ] || { echo "找不到 hycube_simulator" >&2; exit 9; }
cp "$SIM" "$OUT/"

banner "产物"
# 只算三个二进制：out/ 下面还有每个 kernel 的工作目录
sha256sum "$OUT/libdfggenPass.so" "$OUT/cgra_xml_mapper" "$OUT/hycube_simulator" | tee "$OUT/SHA256SUMS"
