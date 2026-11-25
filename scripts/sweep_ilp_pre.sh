#!/usr/bin/env bash
# 预生成 DFG 的 ILP 横评：CP-SAT 放置 → CP-SAT 全局布线 → 软锚点回灌 A*
#
# 三个数字分别回答三个问题：
#   hops/regs  —— CP-SAT 认为这组放置要多少走线代价
#   route      —— 同一放置能否被全局布线器布通（OPTIMAL 就是"确实布得通"）
#   a*         —— mapper 自己的逐节点 A* 吃不吃这套放置（软锚点 mode 3）
set -uo pipefail
source "$(dirname "$0")/lib.sh"
set +e
PY="$ROOT/.venv/bin/python"
TSV=/tmp/sweep_ilp_pre.tsv
[ -f "$TSV" ] || printf 'kernel\tnodes\tii\tilp\tsecs\thops\tregs\troute\ta_star\n' > "$TSV"
mkdir -p /tmp/ilppre

LIST="${*:-pb_pre_2mm pb_pre_atax pb_pre_bicg pb_pre_cholesky pb_pre_doitgen pre_compare_neighb pre_finalSAD pre_gemm_nt pre_pedometer pre_fix_fft pre_nw}"

for k in $LIST; do
  d="$OUT/$k"
  [ -f "$d/dfg.xml" ] || continue
  [ -f "$d/arch.json" ] || bash scripts/inject_mem.sh "$k" > /dev/null 2>&1
  nodes=$(grep -c '<Node ' "$d/dfg.xml")
  if awk -F'\t' -v k="$k" '$1==k {f=1} END{exit !f}' "$TSV"; then continue; fi

  # 用基线 II 作为起点（表 D 里已经有）。PathFinder 超时但 SA 过了的 kernel
  # （pre_pedometer/pre_nw 等）用 SA 的 II —— 它同样是"这台 mapper 能找到的可行 II"。
  ii=$(awk -F'\t' -v k="$k" '$1==k && $3==0 && $4=="ok" {print $5}' /tmp/sweep_pre.tsv | head -1)
  if [ -z "$ii" ]; then
    ii=$(awk -F'\t' -v k="$k" '$1==k && $3==1 && $4=="ok" {print $5}' /tmp/sweep_pre.tsv | head -1)
    [ -n "$ii" ] && echo "  $k: PathFinder 超时，用 SA 的 II=$ii" >&2
  fi
  [ -n "$ii" ] || { echo "$k: 没有基线 II，跳过" >&2; continue; }

  t0=$(date +%s.%N)
  timeout 90 "$PY" tools/ilp/ilp_mapper.py --dfg "$d/dfg.xml" --arch "$d/arch.json" \
    --ii "$ii" --max-ii "$ii" --hops 4 --time-limit 45 --timing bypass \
    -o /tmp/ilppre/$k.pins.txt > /tmp/ilppre/$k.ilp.log 2>&1
  rc=$?
  t1=$(date +%s.%N)
  secs=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')
  line=$(grep -E '^II = ' /tmp/ilppre/$k.ilp.log | tail -1)
  ilp=FAIL; hops='-'; regs='-'
  case "$line" in
    *FEASIBLE*|*OPTIMAL*)
      ilp=OK
      hops=$(echo "$line" | grep -o 'hops=[0-9]*' | cut -d= -f2)
      regs=$(echo "$line" | grep -o 'route_cycles=[0-9]*' | cut -d= -f2) ;;
    *) ilp=$(echo "$line" | sed 's/^II = [0-9]*  //' | cut -c1-20) ;;
  esac

  route='-'
  if [ "$ilp" = OK ]; then
    timeout 90 "$PY" tools/ilp/ilp_router.py --dfg "$d/dfg.xml" --arch "$d/arch.json" \
      --pins /tmp/ilppre/$k.pins.txt --ii "$ii" --max-hops 4 --time-limit 45 \
      -o /tmp/ilppre/$k.routes.json > /tmp/ilppre/$k.router.log 2>&1
    if grep -q 'OPTIMAL\|FEASIBLE' /tmp/ilppre/$k.router.log; then route=OPTIMAL; else route=INFEAS; fi
  fi

  a_star='-'
  if [ "$route" = OPTIMAL ]; then
    P=/tmp/ilppre/$k.m3; rm -rf "$P"; mkdir -p "$P"
    cp "$d/dfg.xml" "$P/dfg.xml"; cp "$d/arch.json" "$P/arch.json"
    cp /tmp/ilppre/$k.pins.txt "$P/pins.txt"
    ( cd "$P" && timeout 120 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json \
        -x 4 -y 4 -i "$ii" -t HyCUBE_4REG -m 0 --pins pins.txt --pins_mode 3 ) \
        > /tmp/ilppre/$k.a_star.log 2>&1
    if grep -q 'Map Success' /tmp/ilppre/$k.a_star.log; then a_star=YES; else a_star=NO; fi
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$k" "$nodes" "$ii" "$ilp" "$secs" "$hops" "$regs" "$route" "$a_star" | tee -a "$TSV"
done
echo
column -t -s$'\t' "$TSV"
