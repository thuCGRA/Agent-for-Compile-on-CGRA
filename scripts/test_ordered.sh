#!/usr/bin/env bash
# 有序 waypoint 走廊：slack=0 的走廊现在是"必须按这个 PE 序列前进"
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
PY=~/acc-thu-ilp/.venv/bin/python
export CP_SAT_THREADS=1 CP_SAT_SEED=0
OUTD=/tmp/ord; mkdir -p $OUTD
for k in "$@"; do
  W="$(kdir "$k")"
  bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
  ii=${II:-4}
  pins=$OUTD/$k.pins; routes=$OUTD/$k.routes.json; guide=$OUTD/$k.guide
  rm -f "$pins" "$routes" "$guide"
  timeout 600 $PY tools/ilp/cegis.py --dfg "$W/dfg.xml" --arch "$W/arch.json" --ii "$ii" \
      --rounds 8 --place-time-limit 30 --route-time-limit 20 \
      --pins-out "$pins" --routes-out "$routes" > $OUTD/$k.cegis.log 2>&1
  [ -s "$pins" ] || { echo "$k: CEGIS FAIL"; continue; }
  $PY tools/ilp/routes_to_guide.py "$routes" --slack 0 --xdim 4 --ydim 4 -o "$guide" >/dev/null 2>&1
  for mode in po ord; do
    D=$OUTD/${k}_$mode; rm -rf $D; mkdir -p $D
    cp "$W/dfg.xml" "$W/arch.json" $D/
    args=(-d dfg.xml -j arch.json -x 4 -y 4 -i "$ii" -t HyCUBE_4REG -m 0 --pins "$pins" --pins_mode 1)
    [ "$mode" = ord ] && args+=(--routes "$guide")
    ( cd $D && timeout 120 "$OUT/cgra_xml_mapper" "${args[@]}" > map.log 2>&1 )
    if grep -q "Map Success" $D/map.log; then r=OK; else r=FAIL; fi
    sim=-
    if [ "$r" = OK ] && [ -s "$W/trace_0.txt" ]; then
      bin=$(ls $D/*_binary.bin 2>/dev/null | head -1)
      ( cd $D && timeout 180 "$OUT/hycube_simulator" -c "$bin" -d "$W/trace_0.txt" \
          -a "$W/mem_alloc.txt" -m 4096 -x 4 -y 4 -t 1 > sim.log 2>&1 )
      if grep -q "Mismatches::0" $D/sim.log 2>/dev/null && ! grep -q "Matches::0," $D/sim.log; then
        sim=$(grep -o "Matches::[0-9]*, Mismatches::[0-9]*" $D/sim.log | head -1)
      else
        sim=$(grep -o "Matches::[0-9]*, Mismatches::[0-9]*" $D/sim.log | head -1); [ -z "$sim" ] && sim=CRASH
      fi
    fi
    echo "  $k $mode: $r  $sim"
    [ "$r" = FAIL ] && grep -m1 "not among\|Map Failed" $D/map.log | sed 's/^/      /'
  done
done