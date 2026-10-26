#!/usr/bin/env bash
# 从本地 Morpher 工作区离线生成 vendor 快照与 patch（不需要网络）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${MORPHER_SRC:-$HOME/morpher-src}"
VENDOR="$ROOT/toolchain/vendor"
PATCHES="$ROOT/toolchain/patches"
mkdir -p "$VENDOR" "$PATCHES"

COMPONENTS=(dfggen mapper simulator)
declare -A DIRNAME=(
  [dfggen]=Morpher_DFG_Generator
  [mapper]=Morpher_CGRA_Mapper
  [simulator]=hycube_simulator
)
declare -A PREFIX=(
  [dfggen]=0001
  [mapper]=0002
  [simulator]=0003
)
declare -A SRCPATHS=(
  [dfggen]="include src CMakeLists.txt"
  [mapper]="include src CMakeLists.txt"
  [simulator]="src CMakeLists.txt"
)

{
  echo "# vendored sources"
  echo
  echo "由 \`scripts/vendor_morpher.sh\` 从 \`$SRC\` 离线导出：快照 = \`git archive HEAD\`，patch = 工作区相对 HEAD 的增量（仅源码目录）。"
  echo
  echo "| 组件 | 上游 | commit | 导出日期 |"
  echo "|---|---|---|---|"
} > "$ROOT/toolchain/VENDORED.md"

for key in "${COMPONENTS[@]}"; do
  d="$SRC/${DIRNAME[$key]}"
  git -C "$d" rev-parse --git-dir >/dev/null 2>&1 || { echo "缺少 git 仓库: $d" >&2; exit 1; }
  commit="$(git -C "$d" rev-parse --short HEAD)"
  url="$(git -C "$d" remote get-url origin 2>/dev/null || echo unknown)"

  rm -rf "$VENDOR/$key"
  mkdir -p "$VENDOR/$key"
  git -C "$d" archive HEAD | tar -x -C "$VENDOR/$key"
  echo "| $key | $url | $commit | $(date +%F) |" >> "$ROOT/toolchain/VENDORED.md"

  read -r -a paths <<< "${SRCPATHS[$key]}"
  git -C "$d" add -N "${paths[@]}" 2>/dev/null || true
  git -C "$d" diff HEAD -- "${paths[@]}" > "$PATCHES/${PREFIX[$key]}-$key.patch" || true
  git -C "$d" reset -q -- "${paths[@]}" 2>/dev/null || true

  if [ -s "$PATCHES/${PREFIX[$key]}-$key.patch" ]; then
    echo "  patch ${PREFIX[$key]}-$key.patch: $(wc -l < "$PATCHES/${PREFIX[$key]}-$key.patch") 行"
  else
    rm -f "$PATCHES/${PREFIX[$key]}-$key.patch"
    echo "  $key 无源码改动"
  fi
done

echo
echo "vendor 完成："
du -sh "$VENDOR"/* 2>/dev/null || true