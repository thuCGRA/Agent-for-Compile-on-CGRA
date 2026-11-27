#!/usr/bin/env bash
# Is the II inflation the placement's fault or the router's?
# Route the *mapper's own* placement with our MRRG router at the mapper's II.
# Baseline register hops come from the mapper's own routeInfo.log.
set -u
cd "$(dirname "$0")/.." || exit 1
source scripts/lib.sh
activate_tc >/dev/null 2>&1
set +eu

PY=.venv/bin/python
printf '%-10s %5s %-5s  %-34s %s\n' kernel nodes II "mapper routes it (baseline)" "our MRRG routes same placement"
for k in ${*:-array_add mac fir conv2 trmm}; do
	W=$PWD/out/$k
	L=$(ls -S "$W"/m0/*routeInfo.log 2>/dev/null | head -1)
	[ -n "${L:-}" ] || { echo "$k: no baseline routeInfo"; continue; }
	N=$(grep -c '<Node idx=' "$W/dfg.xml")
	II=$(awk '/Initial target II = /{for(i=1;i<=NF;i++) if($i=="=") {print $(i+1); exit}}' "$W/m0/map.log" 2>/dev/null)
	[ -z "${II:-}" ] && II=4
	BASE=$($PY - "$L" <<'PY'
import sys
sys.path.insert(0,'tools/ilp')
import validate_mrrg as V
ch=V.parse_routeinfo(sys.argv[1])
regs=[sum(1 for a,b in zip(c,c[1:]) if b[2]!=a[2]) for _n,c in ch if c]
print("edges=%d regs=%d max=%d" % (len(regs), sum(regs), max(regs) if regs else 0))
PY
)
	$PY tools/pins/extract_pins.py "$L" -o /tmp/rq_${k}.pins >/dev/null 2>&1
	OURS=-
	if [ -s /tmp/rq_${k}.pins ]; then
		$PY tools/ilp/mrrg_router.py --dfg "$W/dfg.xml" --arch "$W/arch.json" \
			--pins /tmp/rq_${k}.pins --ii "$II" --slack 1 --time-limit 45 \
			-o /tmp/rq_${k}.json > /tmp/rq_${k}.log 2>&1
		OURS=$(grep -oE "routed [0-9]+ edge\(s\), [0-9]+ register step\(s\)" /tmp/rq_${k}.log | tail -1)
		[ -z "${OURS:-}" ] && OURS=$(grep -oE "INFEASIBLE|UNKNOWN|FEASIBLE" /tmp/rq_${k}.log | tail -1)
	fi
	printf '%-10s %5s %-5s  %-34s %s\n' "$k" "$N" "$II" "$BASE" "$OURS"
done
