#!/usr/bin/env bash
# 验证 --routes 布线后端：CEGIS 出放置+走廊 -> mapper 走该走廊 -> 仿真
# 对照：同一放置只给 --pins（strict）时 mapper 的 A* 能不能布
# 走廊松弛 slack 逐档对比：0 = 精确粗路径，1 = 允许端口绕行，4 = 整阵列（无引导）
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
PY=~/acc-thu-ilp/.venv/bin/python
OUTD=/tmp/rg; mkdir -p $OUTD
TSV=/tmp/routes_backend.tsv
[ -f $TSV ] || printf "kernel\tnodes\tii\tcegis\trounds\tphops\tpregs\tpins_only\tslack\troutes\tsim\n" > $TSV
n_nodes() { grep -o '<DFG count="[0-9]*"' "out/$1/dfg.xml" 2>/dev/null | head -1 | grep -o '[0-9]*'; }
SLACKS=${SLACKS:-"0 1 2 4"}

for k in "$@"; do
  W="$(kdir "$k")"
  [ -s "$W/mem_alloc.txt" ] || { echo "$k: no mem_alloc.txt"; continue; }
  bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
  [ -s "$W/arch.json" ] || { echo "$k: inject_mem failed"; continue; }
  ii=${II:-4}
  n=$(n_nodes "$k")
  pins=$OUTD/$k.pins; routes=$OUTD/$k.routes.json
  rm -f $pins $routes

  timeout 600 $PY tools/ilp/cegis.py --dfg $W/dfg.xml --arch $W/arch.json --ii "$ii" \
      --rounds 8 --place-time-limit 20 --route-time-limit 20 \
      --pins-out $pins --routes-out $routes \
      > $OUTD/$k.cegis.log 2>&1
  cegis=$([ -s $pins ] && echo OK || echo FAIL)
  rounds=$(grep -c '^round ' $OUTD/$k.cegis.log 2>/dev/null)
  phops=$(grep -o 'hops=[0-9]*' $OUTD/$k.cegis.log | tail -1 | grep -o '[0-9]*')
  pregs=$(grep -o 'regs=[0-9]*' $OUTD/$k.cegis.log | tail -1 | grep -o '[0-9]*')

  pins_only=-
  if [ "$cegis" = OK ]; then
    R=/tmp/rg_${k}_pins; rm -rf $R; mkdir -p $R
    cp $W/dfg.xml $R/dfg.xml; cp $W/arch.json $R/arch.json
    ( cd $R && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i "$ii" \
        -t HyCUBE_4REG -m 0 --pins $pins --pins_mode 1 > map.log 2>&1 )
    grep -q "Map Success" $R/map.log && pins_only=OK || pins_only=FAIL
  fi

  for sl in $SLACKS; do
    routesv=-; sim=-
    if [ "$cegis" = OK ]; then
      guide=$OUTD/$k.guide.s$sl
      $PY tools/ilp/routes_to_guide.py $routes --slack $sl \
          --xdim 4 --ydim 4 -o $guide >/dev/null 2>&1
      R2=/tmp/rg_${k}_s$sl; rm -rf $R2; mkdir -p $R2
      cp $W/dfg.xml $R2/dfg.xml; cp $W/arch.json $R2/arch.json
      ( cd $R2 && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i "$ii" \
          -t HyCUBE_4REG -m 0 --pins $pins --pins_mode 1 --routes $guide > map.log 2>&1 )
      if grep -q "Map Success" $R2/map.log; then
        routesv=OK
        if [ -s "$W/trace_0.txt" ]; then
          bin=$(ls $R2/*_binary.bin 2>/dev/null | head -1)
          if [ -n "$bin" ]; then
            ( cd $R2 && timeout 180 "$OUT/hycube_simulator" -c "$bin" -d "$W/trace_0.txt" \
                -a "$W/mem_alloc.txt" -m 4096 -x 4 -y 4 -t 1 > sim.log 2>&1 )
            if grep -q "Mismatches::0" $R2/sim.log 2>/dev/null && ! grep -q "Matches::0," $R2/sim.log; then
              sim=PASS
            else
              sim=$(grep -o "Matches::[0-9]*, Mismatches::[0-9]*" $R2/sim.log | head -1)
              [ -z "$sim" ] && sim=CRASH
            fi
          fi
        fi
      else
        routesv=FAIL
      fi
    fi
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "$k" "$n" "$ii" "$cegis" "$rounds" "${phops:--}" "${pregs:--}" "$pins_only" "$sl" "$routesv" "$sim" >> $TSV
    echo "  $k n=$n II=$ii cegis=$cegis($rounds轮) pins_only=$pins_only slack=$sl routes=$routesv sim=$sim"
  done
done
echo "[routes backend] done"
