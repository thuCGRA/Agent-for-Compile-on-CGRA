#!/usr/bin/env bash
# Plan A end-to-end: CP-SAT placement (x, y) -> mapper routes it natively.
#
# The ILP owns *where* each node sits; morpher's own estimateRouting()/Route()
# own *when* and *how it is wired*.  Pins are spatial only (t = -1) because the
# cycle is the mapper's scheduling result -- pinning it makes estimateRouting()
# reject the placement (see docs/09).
#
# usage: bash scripts/e2e_plan_a.sh [kernel ...]
set -u
cd "$(dirname "$0")/.." || exit 1
source scripts/lib.sh
activate_tc >/dev/null 2>&1
set +eu

PY=.venv/bin/python
OUTDIR=/tmp/pa
mkdir -p "$OUTDIR"

KERNELS=${*:-$(kernels)}
printf '%-22s %-5s %-5s %-24s %s\n' kernel nodes II result verdict

for k in $KERNELS; do
	W=$PWD/out/$k
	[ -f "$W/dfg.xml" ] || { echo "$k: no dfg"; continue; }
	[ -f "$W/arch.json" ] || inject_mem.sh "$k" >/dev/null 2>&1
	[ -f "$W/trace_0.txt" ] || { printf '%-22s %-5s %-5s %-24s %s\n' "$k" "?" "-" "no trace" "-"; continue; }
	N=$(grep -c '<Node idx=' "$W/dfg.xml")
	# baseline II from the last PathFinder run, else the DFG's RecMin
	II=$(awk '/Initial target II = /{for(i=1;i<=NF;i++) if($i=="=") {print $(i+1); exit}}' "$W/m0/map.log" 2>/dev/null)
	[ -z "${II:-}" ] && II=4

	D=$OUTDIR/$k
	rm -rf "$D"; mkdir -p "$D"
	cp "$W/dfg.xml" "$W/arch.json" "$W/mem_alloc.txt" "$W/trace_0.txt" "$D/"

	# 1. placement: minimise hops/regs at the baseline II
	$PY tools/ilp/ilp_mapper.py --dfg "$D/dfg.xml" --arch "$D/arch.json" --ii "$II" \
		--timing bypass --route-cycles -1 --select-coloc exactly \
		--time-limit 30 -o "$D/pins_t" > "$D/place.log" 2>&1
	if [ ! -s "$D/pins_t" ]; then
		printf '%-22s %-5s %-5s %-24s %s\n' "$k" "$N" "$II" "place FAIL" "-"
		continue
	fi
	# 2. spatial only: the cycle belongs to the mapper
	awk 'BEGIN{OFS=" "} /^#/{print; next} NF>=4{$4=-1} {print}' "$D/pins_t" > "$D/pins"

	# 3. mapper routes the placement with its native A*
	( cd "$D" && rm -f *.bin && timeout 300 stdbuf -oL "$OUT/cgra_xml_mapper" \
		-d dfg.xml -j arch.json -x 4 -y 4 -i "$II" -t HyCUBE_4REG -m 0 \
		--pins pins --pins_mode 7 > map.log 2>&1 )
	BIN=$(ls -S "$D"/*binary.bin 2>/dev/null | head -1)
	if [ -z "$BIN" ]; then
		E=$(grep -hoE "PINONLY: node [0-9]+.*|Map Failed" "$D/map.log" | tail -1)
		printf '%-22s %-5s %-5s %-24s %s\n' "$k" "$N" "$II" "map FAIL" "${E:-no bin}"
		continue
	fi
	cp "$BIN" "$D/cgra.bin"
	# 4. correctness gate
	R=$( cd "$D" && timeout 300 "$OUT/hycube_simulator" -c cgra.bin -d trace_0.txt -a mem_alloc.txt \
		-m 4096 -x 4 -y 4 -t 1 2>&1 | grep -o "Matches::[0-9]*, Mismatches::[0-9]*" | tail -1 )
	GOT=$(grep -oE "Map Success with II = [0-9]+" "$D/map.log" | tail -1 | grep -oE "[0-9]+")
	printf '%-22s %-5s %-5s %-24s %s (II %s->%s)\n' "$k" "$N" "$II" "${R:-nosim}" \
		"$(echo "$R" | grep -q 'Mismatches::0' && echo PASS || echo MISMATCH)" "$II" "${GOT:-?}"
done
