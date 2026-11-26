#!/usr/bin/env bash
# End-to-end: CP-SAT placement + CP-SAT routing -> mapper --pins --route-path -> sim.
# This is the real thing (not a replay): the paths come from ilp_router.py.
set -u
cd ~/acc-thu-ilp || exit 1
source scripts/lib.sh
activate_tc
set +eu
PY=.venv/bin/python
II=${II:-4}
TL=${TL:-20}
printf '%-14s %-6s %-8s %-9s %-8s %s\n' kernel nodes ilp map sim verdict
for k in ${*:-array_add mac fir conv2 trmm}; do
	W=$(kdir "$k")
	[ -f "$W/dfg.xml" ] || { echo "$k: no dfg"; continue; }
	bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
	D=/tmp/ip/$k
	rm -rf "$D"; mkdir -p "$D"; cp "$W/dfg.xml" "$W/arch.json" "$W/trace_0.txt" "$W/mem_alloc.txt" "$D/" 2>/dev/null
	N=$(grep -c '<Node idx=' "$D/dfg.xml")
	# 1. placement
	$PY tools/ilp/ilp_mapper.py --dfg "$D/dfg.xml" --arch "$D/arch.json" --ii $II --max-ii $II \
		--timing bypass --time-limit $TL -o "$D/pins" > "$D/ilp.log" 2>&1
	[ -s "$D/pins" ] || { printf '%-14s %-6s %-8s %s\n' "$k" "$N" FAIL "placement infeasible"; continue; }
	# 2. routing
	$PY tools/ilp/ilp_router.py --dfg "$D/dfg.xml" --arch "$D/arch.json" --pins "$D/pins" --ii $II \
		--max-hops 4 --time-limit $TL -o "$D/routes.json" > "$D/route.log" 2>&1
	[ -s "$D/routes.json" ] || { printf '%-14s %-6s %-8s %-9s %s\n' "$k" "$N" OK FAIL "unroutable"; continue; }
	$PY tools/ilp/routes_to_path.py "$D/routes.json" -o "$D/path" >/dev/null 2>&1
	# 3. mapper consumes both
	( cd "$D" && timeout 300 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i $II \
		-t HyCUBE_4REG -m 0 --pins pins --pins_mode 6 --route-path path > map.log 2>&1 )
	BIN=$(ls -S "$D"/*binary.bin 2>/dev/null | head -1)
	if [ -z "$BIN" ]; then
		E=$(grep -hE "could not build|routing failed" "$D/map.log" | head -1)
		printf '%-14s %-6s %-8s %-9s %s\n' "$k" "$N" OK ROUTED "map FAIL: ${E:-no bin}"
		continue
	fi
	# 4. simulate
	cp "$BIN" "$D/cgra.bin"
	R=$( cd "$D" && timeout 300 "$OUT/hycube_simulator" -c cgra.bin -d trace_0.txt -a mem_alloc.txt \
		-m 4096 -x 4 -y 4 -t 1 2>&1 | grep -o "Matches::[0-9]*, Mismatches::[0-9]*" | tail -1 )
	printf '%-14s %-6s %-8s %-9s %-8s %s\n' "$k" "$N" OK ROUTED "${R:-nosim}" \
		"$(echo "$R" | grep -q 'Mismatches::0' && echo PASS || echo MISMATCH)"
done
