#!/usr/bin/env bash
# 预生成 DFG 的映射 sweep（II 阶梯版）
#
# 为什么要 II 阶梯：mapper 内部是 `II = max(RecMin, ResMin, userII)` 然后失败就 II++ 重跑整轮，
# 每个失败的 II 都要花满一次搜索时间。预生成的大图（2mm 28 节点）在 II=4..7 上每轮都要几十秒，
# 用 `-i 0` 一路递增会直接撞超时（实测 300 s 才到 II=8）。改成显式阶梯 `-i 4/6/8/...`，
# 命中就停，能同时拿到「最小可行 II」和「真实耗时」。
#
# 用法: scripts/sweep_prebuilt.sh [per_try_timeout] [method...]
# 产物: /tmp/sweep_pre.tsv   (kernel nodes method ok ii secs tries)
set -uo pipefail
source "$(dirname "$0")/lib.sh"
set +e

TO="${1:-60}"
shift || true
METHODS=("$@")
[ ${#METHODS[@]} -eq 0 ] && METHODS=(0)

OUT_TSV=/tmp/sweep_pre.tsv
[ -f "$OUT_TSV" ] || printf 'kernel\tnodes\tmethod\tok\tii\tsecs\ttries\n' > "$OUT_TSV"
mkdir -p /tmp/swp
LADDER="4 5 6 7 8 10 12 16 20 24"

for d in "$OUT"/pb_pre_* "$OUT"/cme_* "$OUT"/pre_*; do
  [ -d "$d" ] || continue
  k=$(basename "$d")
  [ -f "$d/dfg.xml" ] || continue
  [ -f "$d/mem_alloc.txt" ] || continue

  nodes=$(grep -c '<Node ' "$d/dfg.xml")
  bash scripts/inject_mem.sh "$k" > /tmp/swp/$k.inject 2>&1

  for M in "${METHODS[@]}"; do
    if awk -F'\t' -v k="$k" -v m="$M" '$1==k && $3==m {f=1} END{exit !f}' "$OUT_TSV"; then
      continue
    fi
    R="$d/m$M"
    mkdir -p "$R"
    ln -sf ../dfg.xml "$R/dfg.xml"; ln -sf ../arch.json "$R/arch.json"

    ok=FAIL; ii='?'; secs=0; tries=0
    for I in $LADDER; do
      tries=$((tries+1))
      t0=$(date +%s.%N)
      ( cd "$R" && timeout "$TO" "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json \
          -x 4 -y 4 -i "$I" -t HyCUBE_4REG -m "$M" ) > /tmp/swp/$k.m$M.i$I.log 2>&1
      rc=$?
      t1=$(date +%s.%N)
      secs=$(awk -v a="$secs" -v x="$t0" -v y="$t1" 'BEGIN{printf "%.1f", a + (y-x)}')
      got=$(grep -o 'Map Success with II = [0-9]*' /tmp/swp/$k.m$M.i$I.log | tail -1 | grep -o '[0-9]*$')
      if [ -n "$got" ]; then
        ok=ok; ii=$got
        cp /tmp/swp/$k.m$M.i$I.log /tmp/swp/$k.m$M.log
        break
      fi
      [ "$rc" = 124 ] && ok=TIMEOUT || ok=FAIL
    done
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$k" "$nodes" "$M" "$ok" "$ii" "$secs" "$tries" | tee -a "$OUT_TSV"
  done
done
echo "--- 汇总 ---"
column -t -s$'\t' "$OUT_TSV" 2>/dev/null || cat "$OUT_TSV"
