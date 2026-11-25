#!/usr/bin/env bash
# mode 6 = inject: replay an external (pins) solution, routing with A* only.
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
PY=~/acc-thu-ilp/.venv/bin/python
export CP_SAT_THREADS=1 CP_SAT_SEED=0
D=/tmp/inj; rm -rf $D; mkdir -p $D
for k in "$@"; do
  W="$(kdir "$k")"
  bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
  ii=${II:-4}
  pins=$D/$k.pins
  timeout 600 $PY tools/ilp/cegis.py --dfg "$W/dfg.xml" --arch "$W/arch.json" --ii "$ii" \
      --rounds 8 --place-time-limit 30 --route-time-limit 20 \
      --pins-out "$pins" > $D/$k.cegis.log 2>&1
  [ -s "$pins" ] || { echo "$k: CEGIS FAIL"; continue; }
  M=$D/$k; rm -rf $M; mkdir -p $M
  cp "$W/dfg.xml" "$W/arch.json" $M/
  ( cd $M && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i "$ii" \
      -t HyCUBE_4REG -m 0 --pins "$pins" --pins_mode 6 > map.log 2>&1 )
  rc=$?
  r=FAIL; grep -q "Map Success" $M/map.log && r=OK
  sim=-
  if [ "$r" = OK ] && [ -s "$W/trace_0.txt" ]; then
    bin=$(ls $M/*_binary.bin 2>/dev/null | head -1)
    ( cd $M && timeout 180 "$OUT/hycube_simulator" -c "$bin" -d "$W/trace_0.txt" \
        -a "$W/mem_alloc.txt" -m 4096 -x 4 -y 4 -t 1 > sim.log 2>&1 )
    sim=$(grep -o "Matches::[0-9]*, Mismatches::[0-9]*" $M/sim.log | head -1); [ -z "$sim" ] && sim=CRASH
  fi
  echo "$k: rc=$rc $r  $sim"
  grep -E "^INJECT:|not among" $M/map.log | head -4 | sed 's/^/    /'
done