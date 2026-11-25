#!/usr/bin/env bash
# Bisect: does inject mode reproduce the baseline when fed the baseline's OWN placement?
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
D=/tmp/injbase; rm -rf $D; mkdir -p $D
# rebuild output: the tests below run $OUT/cgra_xml_mapper, so refresh it first
cp ~/acc-thu-ilp/build/mapper/src/cgra_xml_mapper "$OUT/cgra_xml_mapper"
echo "binary: $(sha256sum "$OUT/cgra_xml_mapper" | cut -c1-16)"
for k in array_add mac fir conv2 trmm; do
  W="$(kdir "$k")"
  bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
  # 1) baseline PathFinder run
  B=$D/${k}_base; rm -rf $B; mkdir -p $B
  cp "$W/dfg.xml" "$W/arch.json" $B/
  ( cd $B && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
      -t HyCUBE_4REG -m 0 > map.log 2>&1 )
  grep -q "Map Success" $B/map.log || { echo "$k: baseline FAIL"; continue; }
  # 2) extract its placement, feed it back through inject mode
  log=$(ls -S $B/*routeInfo.log 2>/dev/null | head -1)
  [ -s "$log" ] || { echo "$k: no routeInfo"; continue; }
  ~/acc-thu-ilp/.venv/bin/python tools/pins/extract_pins.py "$log" -o $D/$k.pins >/dev/null 2>&1
  I=$D/${k}_inj; rm -rf $I; mkdir -p $I
  cp "$W/dfg.xml" "$W/arch.json" $I/
  ( cd $I && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
      -t HyCUBE_4REG -m 0 --pins $D/$k.pins --pins_mode 6 > map.log 2>&1 )
  grep -q "Map Success" $I/map.log && r=OK || r=FAIL
  # 3) simulate both
  sim() {
    local dir=$1; local bin; bin=$(ls $dir/*_binary.bin 2>/dev/null | head -1)
    [ -s "$bin" ] || { echo -n "no-bin"; return; }
    ( cd $dir && timeout 180 "$OUT/hycube_simulator" -c "$bin" -d "$W/trace_0.txt" \
        -a "$W/mem_alloc.txt" -m 4096 -x 4 -y 4 -t 1 > sim.log 2>&1 )
    local s; s=$(grep -o "Matches::[0-9]*, Mismatches::[0-9]*" $dir/sim.log | head -1)
    echo -n "${s:-CRASH}"
  }
  echo "$k: baseline=$(sim $B)  inject=$r $(sim $I)"
  grep -E "^INJECT:" $I/map.log | head -2 | sed 's/^/    /'
done