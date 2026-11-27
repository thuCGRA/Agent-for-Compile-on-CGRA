#!/usr/bin/env bash
# Plan B end-to-end: CP-SAT placement -> MRRG port-level routing -> mapper replay -> sim.
#
# Unlike plan A (which hands the routing back to the mapper's greedy A*), here the
# mapper executes the exact port chain the router chose, so the result is our
# placement AND our routing, and the simulator is the only judge.
set -u
cd ~/acc-thu-ilp || exit 1
source scripts/lib.sh
activate_tc
set +eu
PY=.venv/bin/python
II=${II:-4}
TL=${TL:-30}
SLACK=${SLACK:-1}
printf '%-14s %-6s %-8s %-9s %-8s %-9s %s\n' kernel nodes ilp route regs map sim
for k in ${*:-array_add mac fir conv2 trmm}; do
	W=$(kdir "$k")
	[ -f "$W/dfg.xml" ] || { echo "$k: no dfg"; continue; }
	bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
	D=/tmp/pb/$k
	rm -rf "$D"; mkdir -p "$D"
	cp "$W/dfg.xml" "$W/arch.json" "$W/trace_0.txt" "$W/mem_alloc.txt" "$D/" 2>/dev/null
	N=$(grep -c '<Node idx=' "$D/dfg.xml")

	# 1. placement: the ILP owns both where and when
	$PY tools/ilp/ilp_mapper.py --dfg "$D/dfg.xml" --arch "$D/arch.json" \
		--ii $II --max-ii $II --timing bypass --route-cycles -1 \
		--select-coloc exactly --time-limit $TL -o "$D/pins" > "$D/ilp.log" 2>&1
	[ -s "$D/pins" ] || { printf '%-14s %-6s %-8s\n' "$k" "$N" FAIL; continue; }

	# 2. routing on the real port graph, cycle budget pinned per edge
	$PY tools/ilp/mrrg_router.py --dfg "$D/dfg.xml" --arch "$D/arch.json" \
		--pins "$D/pins" --ii $II --slack $SLACK --time-limit $TL \
		-o "$D/routes.json" > "$D/route.log" 2>&1
	ST=$(grep -oE 'OPTIMAL|FEASIBLE|INFEASIBLE|UNKNOWN' "$D/route.log" | tail -1)
	[ -s "$D/routes.json" ] || { printf '%-14s %-6s %-8s %-9s\n' "$k" "$N" OK "${ST:-FAIL}"; continue; }
	REGS=$($PY - "$D/routes.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(sum(1 for r in d["routes"] for a, b in zip(r["path"], r["path"][1:]) if a[2] != b[2]))
PY
)
	$PY tools/ilp/routes_to_portpath.py "$D/routes.json" -o "$D/path" --ii $II >/dev/null 2>&1

	# 3. mapper replays the chain verbatim (no A*, no II++ retry)
	( cd "$D" && timeout 300 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i $II \
		-t HyCUBE_4REG -m 0 --pins pins --pins_mode 6 --port-path path > map.log 2>&1 )
	BIN=$(ls -S "$D"/*binary.bin 2>/dev/null | head -1)
	if [ -z "$BIN" ]; then
		E=$(grep -hE "could not build|routing failed" "$D/map.log" | head -1)
		printf '%-14s %-6s %-8s %-9s %-8s %s\n' "$k" "$N" OK "${ST:-?}" "$REGS" "FAIL: ${E:-no bin}"
		continue
	fi
	cp "$BIN" "$D/cgra.bin"

	# 4. simulate -- the only correctness gate
	S=$( cd "$D" && timeout 300 "$OUT/hycube_simulator" -c cgra.bin -d trace_0.txt \
		-a mem_alloc.txt -m 4096 -x 4 -y 4 -t 1 2>&1 | grep -oE 'Matches::[0-9]+, Mismatches::[0-9]+' | tail -1 )
	case "$S" in
		*Mismatches::0) V=PASS ;;
		"") V=nosim ;;
		*) V=MISMATCH ;;
	esac
	printf '%-14s %-6s %-8s %-9s %-8s %-9s %s %s\n' \
		"$k" "$N" OK "${ST:-?}" "$REGS" ok "$S" "$V"
done
