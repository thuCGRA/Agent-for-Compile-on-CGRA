#!/usr/bin/env bash
# Replay each kernel's own baseline route through --port-path and demand a
# byte-identical bitstream plus a clean simulation.  This is the self-test for
# resolvePort() and for the absolute-latency reconstruction in the hook.
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT=$PWD
. scripts/lib.sh 2>/dev/null || true
set +eu
OUT=$ROOT/out
MORPHER=$ROOT/out/cgra_xml_mapper

printf "%-10s %5s %6s %8s %6s %s\n" kernel nodes edges bytes diff sim
for k in "$@"; do
  W=$OUT/$k
  LOG=$(ls -S "$W"/m0/*routeInfo.log 2>/dev/null | head -1)
  [ -s "$LOG" ] || { printf "%-10s %5s %6s %8s %6s %s\n" "$k" ? ? ? ? "no routeInfo"; continue; }
  BASE=$(ls "$W"/m0/*binary.bin 2>/dev/null | head -1)
  [ -f "$BASE" ] || { printf "%-10s %5s %6s %8s %6s %s\n" "$k" ? ? ? ? "no baseline bin"; continue; }

  T=/tmp/pp_$k
  rm -rf "$T"; mkdir -p "$T"
  cp "$W/dfg.xml" "$T/dfg.xml"; cp "$W/arch.json" "$T/arch.json"
  .venv/bin/python tools/pins/extract_pins.py "$LOG" -o "$T/pins.txt" >/dev/null 2>&1
  .venv/bin/python tools/ilp/routeinfo_to_portpath.py "$LOG" -o "$T/route.pp" >/dev/null 2>&1
  NODES=$(grep -c '^0 \|^[0-9]* [0-9-]' "$T/pins.txt" 2>/dev/null || echo ?)
  EDGES=$(grep -vc '^#' "$T/route.pp" 2>/dev/null || echo ?)
  II=$(basename "$BASE" | grep -o 'II=[0-9]*' | cut -d= -f2)

  ( cd "$T" && timeout 300 "$MORPHER" -d dfg.xml -j arch.json -x 4 -y 4 -i "${II:-4}" \
      -t HyCUBE_4REG -m 0 --pins pins.txt --pins_mode 6 --port-path route.pp \
      > map.log 2>&1 )
  NEW=$(ls "$T"/*binary.bin 2>/dev/null | head -1)
  if [ -z "$NEW" ]; then
    printf "%-10s %5s %6s %8s %6s %s\n" "$k" "$NODES" "$EDGES" - - "map FAIL"
    grep -E "PORTPATH|INJECT: could|Map Failed" "$T/map.log" | tail -2 | sed 's/^/    /'
    continue
  fi
  DIFF=$(cmp -l "$BASE" "$NEW" 2>/dev/null | wc -l)
  SIM=$(bash scripts/sim.sh "$k" 0 2>/dev/null | grep -o "Matches::[0-9]*, Mismatches::[0-9]*" | head -1)
  printf "%-10s %5s %6s %8s %6s %s\n" "$k" "$NODES" "$EDGES" "$(stat -c%s "$NEW")" "$DIFF" "${SIM:-nosim}"
done
