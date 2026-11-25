#!/usr/bin/env bash
# cme_* (CGRA-ME microbench) 专项 sweep：小图，II=0 递增应该很快
set -uo pipefail
source "$(dirname "$0")/lib.sh"
set +e
rm -f /tmp/cme.tsv
printf 'kernel\tnodes\tmethod\tok\tii\tsecs\n' > /tmp/cme.tsv
for d in "$OUT"/cme_*; do
  [ -d "$d" ] || continue
  k=$(basename "$d")
  nodes=$(grep -c '<Node ' "$d/dfg.xml")
  bash scripts/inject_mem.sh "$k" > /tmp/cme_$k.inj 2>&1
  for M in 0 1; do
    R="$d/m$M"; mkdir -p "$R"
    ln -sf ../dfg.xml "$R/dfg.xml"; ln -sf ../arch.json "$R/arch.json"
    ok=FAIL; ii='?'
    t0=$(date +%s.%N)
    ( cd "$R" && timeout 60 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json \
        -x 4 -y 4 -i 0 -t HyCUBE_4REG -m "$M" ) > /tmp/cme_$k.m$M.log 2>&1
    rc=$?
    t1=$(date +%s.%N)
    secs=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')
    got=$(grep -o 'Map Success with II = [0-9]*' /tmp/cme_$k.m$M.log | tail -1 | grep -o '[0-9]*$')
    if [ -n "$got" ]; then ok=ok; ii=$got; elif [ "$rc" = 124 ]; then ok=TIMEOUT; fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$k" "$nodes" "$M" "$ok" "$ii" "$secs" | tee -a /tmp/cme.tsv
  done
done
column -t -s$'\t' /tmp/cme.tsv
