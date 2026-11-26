#!/usr/bin/env bash
# Replay a baseline's own route through --route-path and diff the bitstream.
# If this is not byte identical, the waypoint constraint is wrong.
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
D=/tmp/rp; rm -rf $D; mkdir -p $D
k=${1:-array_add}
W="$(kdir "$k")"
bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
B=$D/${k}_base; I=$D/${k}_rp
for d in $B $I; do rm -rf $d; mkdir -p $d; cp "$W/dfg.xml" "$W/arch.json" $d/; done

# 1. baseline
( cd $B && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
    -t HyCUBE_4REG -m 0 > map.log 2>&1 )
log=$(ls -S $B/*routeInfo.log 2>/dev/null | head -1)
[ -n "$log" ] || { echo "$k: baseline produced no routeInfo.log"; exit 1; }
PY=.venv/bin/python
$PY tools/pins/extract_pins.py "$log" -o $D/$k.pins >/dev/null 2>&1
$PY tools/ilp/routeinfo_to_path.py "$log" -o $D/$k.path

# 2. replay the same placement AND the same route
( cd $I && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
    -t HyCUBE_4REG -m 0 --pins $D/$k.pins --pins_mode 6 \
    --route-path $D/$k.path > map.log 2>&1 )
grep -h "ROUTEPATH" $I/map.log | head -3
grep -h "Map Success\|Map Failed" $I/map.log | head -2

bb=$(ls $B/*binary.bin 2>/dev/null | head -1)
ib=$(ls $I/*binary.bin 2>/dev/null | head -1)
if [ -z "$ib" ]; then echo "$k: inject produced no bitstream"; exit 1; fi
n=$(cmp -l "$bb" "$ib" 2>/dev/null | wc -l)
echo "$k: differing bytes = $n  (size $(stat -c%s "$bb") vs $(stat -c%s "$ib"))"

# 3. simulate the replayed bitstream
bash scripts/sim.sh "$k" >/dev/null 2>&1
s=$(grep -h "Simulation Result" "$W"/m0/sim_result.txt 2>/dev/null | tail -1)
echo "$k: baseline sim = $(grep -h "Simulation Result" $B/sim_result.txt 2>/dev/null | tail -1)"
