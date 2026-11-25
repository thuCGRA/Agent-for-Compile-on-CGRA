#!/usr/bin/env bash
# 把上游 Morpher 预生成的 DFG（`Morpher_CGRA_Mapper/applications/**/*.xml`）装进本工程，
# 让它们走同一条 map/sim 流水线。
#
# 为什么单独一条路：`kernels/*/*.c` 是靠 DFG pass 现编的；而上游 `applications/` 里那批
# 是**已经生成好的 DFG**（约 44 个，含 polybench 10 个），没有对应可编译的 .c。它们只需要
# 一份 mem_alloc.txt 就能进 mapper（`tools/prebuilt/synth_mem_alloc.py` 从 DFG 反推）。
#
# 用法:
#   scripts/import_prebuilt.sh                 # 装全部（按 scripts/prebuilt.tsv）
#   scripts/import_prebuilt.sh pb_pre_2mm cme_mac   # 只装指定的几个
#   scripts/import_prebuilt.sh --list          # 只列清单
#
# 环境变量:
#   MORPHER_HOME   上游 morpher 仓库（默认 ~/morpher）
#   FORCE=1        覆盖已存在的 out/<k>/dfg.xml
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MORPHER_HOME="${MORPHER_HOME:-$HOME/morpher}"
APPS="$MORPHER_HOME/Morpher_CGRA_Mapper/applications"
MANIFEST="$ROOT/scripts/prebuilt.tsv"

[ -d "$APPS" ] || { echo "找不到 $APPS（设 MORPHER_HOME）" >&2; exit 2; }

if [ "${1:-}" = "--list" ]; then
  awk -F'\t' '$1 !~ /^#/ && NF {printf "%-28s %s\n", $1, $2}' "$MANIFEST"
  exit 0
fi

want=("$@")
n_ok=0
n_skip=0
n_bad=0

while IFS=$'\t' read -r name rel; do
  case "$name" in ''|'#'*) continue ;; esac
  if [ ${#want[@]} -gt 0 ]; then
    hit=0
    for w in "${want[@]}"; do [ "$w" = "$name" ] && hit=1; done
    [ $hit -eq 1 ] || continue
  fi

  src="$APPS/$rel"
  if [ ! -f "$src" ]; then
    echo "  跳过 $name：上游没有 $rel" >&2
    n_bad=$((n_bad+1))
    continue
  fi

  d="$ROOT/out/$name"
  mkdir -p "$d"
  if [ -f "$d/dfg.xml" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "  $name: 已存在（FORCE=1 覆盖）"
    n_skip=$((n_skip+1))
    continue
  fi

  cp "$src" "$d/dfg.xml"
  nodes=$(grep -c '<Node ' "$d/dfg.xml" || true)

  # cgra_me_bench 那批是纯计算图（操作数是输入占位符，没有 LOAD/STORE），
  # 所以一个 BasePointerName 都没有。这种 case 写一个只有表头的 mem_alloc.txt 即可。
  if grep -q '<BasePointerName' "$d/dfg.xml"; then
    if ! python3 "$ROOT/tools/prebuilt/synth_mem_alloc.py" "$d/dfg.xml" \
          -o "$d/mem_alloc.txt" --auto --quiet; then
      echo "  $name: mem_alloc 生成失败" >&2
      rm -f "$d/dfg.xml" "$d/mem_alloc.txt"
      n_bad=$((n_bad+1))
      continue
    fi
    bs=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["bank_size"])' \
          "$d/mem_layout.json")
  else
    echo "var_name,base_addr" > "$d/mem_alloc.txt"
    echo '{"bank_size":2048,"banks":2}' > "$d/mem_layout.json"
    bs=2048
  fi

  printf '{"kernel":"%s","function":"","source":"%s","prebuilt":true,"bank_size":%s,"banks":2}\n' \
    "$name" "$rel" "$bs" > "$d/manifest.json"
  echo "  $name: ${nodes} 节点, bank_size=$bs"
  n_ok=$((n_ok+1))
done < "$MANIFEST"

echo
echo "装完：$n_ok 个，跳过 $n_skip，失败 $n_bad"
echo "接下来："
echo "  bash scripts/inject_mem.sh <kernel>      # 会读 manifest.json 里的 bank_size"
echo "  bash scripts/map.sh <kernel> 0"
