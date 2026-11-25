#!/usr/bin/env bash
# Same placement, two routers: diff the emitted bitstreams byte by byte.
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
D=/tmp/cmpb; rm -rf $D; mkdir -p $D
k=${1:-array_add}
W="$(kdir "$k")"
bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
B=$D/${k}_base; I=$D/${k}_inj
for d in $B $I; do rm -rf $d; mkdir -p $d; cp "$W/dfg.xml" "$W/arch.json" $d/; done
( cd $B && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
    -t HyCUBE_4REG -m 0 > map.log 2>&1 )
log=$(ls -S $B/*routeInfo.log 2>/dev/null | head -1)
~/acc-thu-ilp/.venv/bin/python tools/pins/extract_pins.py "$log" -o $D/$k.pins >/dev/null 2>&1
( cd $I && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
    -t HyCUBE_4REG -m 0 --pins $D/$k.pins --pins_mode 6 > map.log 2>&1 )
echo "--- inject log tail"; tail -4 $I/map.log
bb=$(ls $B/*binary.bin 2>/dev/null | head -1)
ib=$(ls $I/*binary.bin 2>/dev/null | head -1)
echo "base bin=$bb ($(stat -c%s "$bb" 2>/dev/null) B)"
echo "inj  bin=$ib ($(stat -c%s "$ib" 2>/dev/null) B)"
if [ -n "$bb" ] && [ -n "$ib" ]; then
  echo "--- differing bytes (offset: base inj)"
  cmp -l "$bb" "$ib" 2>/dev/null | head -25
  echo "total differing: $(cmp -l "$bb" "$ib" 2>/dev/null | wc -l)"
fi
