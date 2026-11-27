#!/usr/bin/env bash
# Does the ILP's own phase schedule survive, or does handing time to the mapper
# inflate II?  Same placement, two time owners:
#   A  : t = -1  (spatial only)  -> estimateRouting() owns the cycle
#   B  : t kept  (inject mode 6) -> the ILP owns the cycle
# usage: bash scripts/e2e_phase_owner.sh [kernel ...]
set -u
cd "$(dirname "$0")/.." || exit 1
source scripts/lib.sh
activate_tc >/dev/null 2>&1
set +eu

PY=.venv/bin/python
OUTDIR=/tmp/ph
mkdir -p "$OUTDIR"

printf '%-12s %5s %5s  %-18s %-18s\n' kernel nodes baseII "A: t from mapper" "B: t from ILP"
for k in ${*:-array_add mac fir conv2 trmm}; do
	W=$PWD/out/$k
	[ -f "$W/dfg.xml" ] || { echo "$k: no dfg"; continue; }
	[ -f "$W/trace_0.txt" ] || { echo "$k: no trace"; continue; }
	N=$(grep -c '<Node idx=' "$W/dfg.xml")
	II=$(awk '/Initial target II = /{for(i=1;i<=NF;i++) if($i=="=") {print $(i+1); exit}}' "$W/m0/map.log" 2>/dev/null)
	[ -z "${II:-}" ] && II=4

	D=$OUTDIR/$k
	rm -rf "$D"; mkdir -p "$D"
	cp "$W/dfg.xml" "$W/arch.json" "$W/mem_alloc.txt" "$W/trace_0.txt" "$D/"

	$PY tools/ilp/ilp_mapper.py --dfg "$D/dfg.xml" --arch "$D/arch.json" --ii "$II" \
		--timing bypass --route-cycles -1 --select-coloc exactly \
		--time-limit 30 -o "$D/pins_t" > "$D/place.log" 2>&1
	if [ ! -s "$D/pins_t" ]; then
		printf '%-12s %5s %5s  %-18s %-18s\n' "$k" "$N" "$II" "place FAIL" "-"
		continue
	fi

	run_one() {   # $1=tag $2=pinsfile $3=mode
		local T=$D/$1
		rm -rf "$T"; mkdir -p "$T"
		cp "$D/dfg.xml" "$D/arch.json" "$D/mem_alloc.txt" "$D/trace_0.txt" "$T/"
		cp "$2" "$T/pins"
		( cd "$T" && timeout 300 stdbuf -oL "$OUT/cgra_xml_mapper" \
			-d dfg.xml -j arch.json -x 4 -y 4 -i "$II" -t HyCUBE_4REG -m 0 \
			--pins pins --pins_mode "$3" > map.log 2>&1 )
		local GOT R
		GOT=$(grep -oE "Map Success with II = [0-9]+" "$T/map.log" | tail -1 | grep -oE "[0-9]+$")
		if [ -n "${GOT:-}" ]; then
			cp "$(ls -S "$T"/*binary.bin | head -1)" "$T/cgra.bin" 2>/dev/null
			R=$( cd "$T" && timeout 300 "$OUT/hycube_simulator" -c cgra.bin -d trace_0.txt \
				-a mem_alloc.txt -m 4096 -x 4 -y 4 -t 1 2>&1 \
				| grep -o "Matches::[0-9]*, Mismatches::[0-9]*" | tail -1 )
			echo "II=${GOT} ${R:-nosim}"
		else
			echo "FAIL"
		fi
	}

	awk 'BEGIN{OFS=" "} /^#/{print; next} NF>=4{$4=-1} {print}' "$D/pins_t" > "$D/pins_spatial"
	cp "$D/pins_t" "$D/pins_phase"
	A=$(run_one A "$D/pins_spatial" 7)
	B=$(run_one B "$D/pins_phase" 6)
	printf '%-12s %5s %5s  %-18s %-18s\n' "$k" "$N" "$II" "$A" "$B"
done
