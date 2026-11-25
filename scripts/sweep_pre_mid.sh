#!/usr/bin/env bash
# 中等规模预生成 DFG sweep（38–60 节点），II 阶梯
set -uo pipefail
source "$(dirname "$0")/lib.sh"
set +e
TO="${1:-45}"
TAG="${2:-mid}"
OUT_TSV=/tmp/sweep_pre.tsv
[ -f "$OUT_TSV" ] || printf 'kernel\tnodes\tmethod\tok\tii\tsecs\ttries\n' > "$OUT_TSV"
mkdir -p /tmp/swp
LADDER="4 5 6 7 8 10 12 16 20"
KERNELS_LIST="pre_compare_neighb pre_finalSAD pre_fix_fft pre_gemm_nt pre_nw pre_pedometer pre_adpcm_decoder pre_dct"
for k in $KERNELS_LIST; do
  d="$OUT/$k"
  [ -f "$d/dfg.xml" ] || continue
  nodes=$(grep -c '<Node ' "$d/dfg.xml")
  bash scripts/inject_mem.sh "$k" > /tmp/swp/$k.inject 2>&1
  for M in 0 1; do
    if awk -F'\t' -v k="$k" -v m="$M" '$1==k && $3==m {f=1} END{exit !f}' "$OUT_TSV"; then continue; fi
    R="$d/m$M"; mkdir -p "$R"
    ln -sf ../dfg.xml "$R/dfg.xml"; ln -sf ../arch.json "$R/arch.json"
    ok=FAIL; ii='?'; secs=0; tries=0
    for I in $LADDER; do
      tries=$((tries+1))
      t0=$(date +%s.%N)
      ( cd "$R" && timeout "$TO" "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json \
          -x 4 -y 4 -i "$I" -t HyCUBE_4REG -m "$M" ) > /tmp/swp/$k.m$M.i$I.log 2>&1
      rc=$?; t1=$(date +%s.%N)
      secs=$(awk -v a="$secs" -v x="$t0" -v y="$t1" 'BEGIN{printf "%.1f", a + (y-x)}')
      got=$(grep -o 'Map Success with II = [0-9]*' /tmp/swp/$k.m$M.i$I.log | tail -1 | grep -o '[0-9]*$')
      if [ -n "$got" ]; then ok=ok; ii=$got; cp /tmp/swp/$k.m$M.i$I.log /tmp/swp/$k.m$M.log; break; fi
      [ "$rc" = 124 ] && ok=TIMEOUT || ok=FAIL
    done
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$k" "$nodes" "$M" "$ok" "$ii" "$secs" "$tries" | tee -a "$OUT_TSV"
  done
done
echo done
