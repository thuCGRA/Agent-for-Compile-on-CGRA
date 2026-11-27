#!/usr/bin/env bash
# Quantify the value of combinational bypass.
#
# CGRA-ME's HyCUBE model forbids it: src/sched/hycube_supported_ops.dot gives
# every op->op pair LOWER_BOUND_NETWORK_LATENCY >= 1, so a value always costs at
# least one cycle to move.  Morpher's DataPath::getOutputPort() advances the
# phase by the operator latency only, so a PE-to-PE hop can be free (d = 0).
#
# --timing bypass   : d in [0, h]        (HyCUBE / morpher)
# --timing register : d == max(h, 1)     (the conventional, no-bypass CGRA)
#
# usage: bash scripts/exp_bypass_value.sh [kernel ...]
set -u
cd "$(dirname "$0")/.." || exit 1
source scripts/lib.sh
activate_tc >/dev/null 2>&1
set +eu

PY=.venv/bin/python
printf '%-10s %5s %-5s  %-30s %s\n' kernel nodes II "bypass (HyCUBE)" "register (no bypass)"
for k in ${*:-array_add mac fir conv2 trmm}; do
	W=$PWD/out/$k
	[ -f "$W/dfg.xml" ] || { echo "$k: no dfg"; continue; }
	N=$(grep -c '<Node idx=' "$W/dfg.xml")
	II=$(awk '/Initial target II = /{for(i=1;i<=NF;i++) if($i=="=") {print $(i+1); exit}}' "$W/m0/map.log" 2>/dev/null)
	[ -z "${II:-}" ] && II=4
	for T in bypass register; do
		$PY tools/ilp/ilp_mapper.py --dfg "$W/dfg.xml" --arch "$W/arch.json" --ii "$II" \
			--timing $T --route-cycles -1 --select-coloc exactly \
			--time-limit 20 -o "/tmp/bv_${k}_${T}.pins" > "/tmp/bv_${k}_${T}.log" 2>&1
	done
	B=$(grep -oE "$(echo $II)\s+bypass\s+\w+\s+hops=[0-9]+\s+route_cycles=[0-9]+" "/tmp/bv_${k}_bypass.log" | tail -1 | sed -E 's/^[0-9]+\s+bypass\s+//')
	R=$(grep -oE "$(echo $II)\s+register\s+\w+\s+hops=[0-9]+\s+route_cycles=[0-9]+" "/tmp/bv_${k}_register.log" | tail -1 | sed -E 's/^[0-9]+\s+register\s+//')
	printf '%-10s %5s %-5s  %-30s %s\n' "$k" "$N" "$II" "${B:-fail}" "${R:-fail}"
done
