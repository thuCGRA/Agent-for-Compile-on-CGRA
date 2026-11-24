#!/usr/bin/env bash
# stage C：ILP（CP-SAT）放置 + CP-SAT 布线，并与 mapper 的 A* 布线对照
# 输出 /tmp/sweep_ilp.tsv  （可重入：已完成的 kernel 会跳过）
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +e
PY=~/acc-thu-ilp/.venv/bin/python
OUTD=/tmp/ilp; mkdir -p $OUTD
TSV=/tmp/sweep_ilp.tsv
[ -f $TSV ] || printf "kernel\tnodes\tii\tilp\tsecs\thops\tregs\troute\trsecs\tprefer\tpii\n" > $TSV
done_already() { awk -F'\t' -v k="$1" '$1==k{found=1} END{exit !found}' $TSV; }
iifor() { awk -F'\t' -v k="$1" '$1==k && $3=="m0" && $4==1 {print $5}' /tmp/sweep.tsv; }
n_nodes() { grep -o '<DFG count="[0-9]*"' "out/$1/dfg.xml" 2>/dev/null | head -1 | grep -o '[0-9]*'; }

for k in $(kernels); do
  done_already "$k" && { echo "  $k (done, skip)"; continue; }
  [ -s "out/$k/arch.json" ] || continue
  [ -s "out/$k/dfg.xml" ]   || continue
  ii=$(iifor "$k"); [ -z "$ii" ] && ii=4
  n=$(n_nodes "$k"); W="$(kdir "$k")"
  pins=$OUTD/$k.pins; routes=$OUTD/$k.routes.json
  rm -f $pins $routes

  t0=$(date +%s)
  timeout 40 $PY tools/ilp/ilp_mapper.py --dfg $W/dfg.xml --arch $W/arch.json \
      --ii "$ii" --max-ii "$ii" --time-limit 20 --timing bypass -o $pins >$OUTD/$k.ilp.log 2>&1
  t1=$(date +%s); secs=$((t1-t0))
  if [ -s $pins ]; then ilp=OK; else ilp=FAIL; fi

  hops=-; regs=-; route=-; rsecs=-
  if [ "$ilp" = OK ]; then
    t2=$(date +%s)
    timeout 40 $PY tools/ilp/ilp_router.py --dfg $W/dfg.xml --arch $W/arch.json \
        --pins $pins --ii "$ii" --max-hops 4 --time-limit 20 -o $routes >$OUTD/$k.route.log 2>&1
    t3=$(date +%s); rsecs=$((t3-t2))
    route=$(grep -o 'route: .*' $OUTD/$k.route.log | head -1 | sed 's/route: //')
    hops=$(echo "$route" | grep -o 'hops=[0-9]*' | grep -o '[0-9]*')
    regs=$(echo "$route" | grep -o 'regs=[0-9]*' | grep -o '[0-9]*')
    [ -z "$hops" ] && hops=-
    [ -z "$regs" ] && regs=-
    # 真实 mapper + prefer pins（注意：必须在干净目录里 cp 真实文件，用软链会段错误）
    R=/tmp/pref_$k; rm -rf $R; mkdir -p $R
    cp $W/dfg.xml $R/dfg.xml; cp $W/arch.json $R/arch.json
    ( cd $R && timeout 45 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i "$ii" \
        -t HyCUBE_4REG -m 0 --pins $pins --pins_mode 3 > map.log 2>&1 )
    if grep -q "Map Success" $R/map.log 2>/dev/null; then
      prefer=OK
      pii=$(grep -o 'Map Success with II = [0-9]*' $R/map.log | head -1 | grep -o '[0-9]*$')
    else prefer=FAIL; pii=-; fi
  else
    prefer=-; pii=-
  fi
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$k" "$n" "$ii" "$ilp" "$secs" "$hops" "$regs" "${route:--}" "$rsecs" "$prefer" "$pii" >> $TSV
  echo "  $k n=$n II=$ii ilp=$ilp ${secs}s route=$route prefer=$prefer($pii)"
done
echo "[stage C] done"
