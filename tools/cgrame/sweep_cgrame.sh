#!/usr/bin/env bash
# CGRA-ME on its own microbench DFGs.
#
# Only ClusteredMapper is available in this build: every other registered
# mapper (BothSmallTimeThenHeurFullTime, ILPHeuristicMapper, ILPMapper) needs
# Gurobi.  ClusteredMapper is the "SA placement + negotiated routing" path --
# the same family of algorithm as our evo-mapper arm, so it is the fair
# comparison anyway.
#
# usage: bash tools/cgrame/sweep_cgrame.sh [kernel ...]
set -u
CME_HOME=${CME_HOME:-$HOME/cgra-me-release-v2.0.1}
CME=$CME_HOME/build/bin/cgrame
CFG=$CME_HOME/build/bin/mapper_config.ini
OUTD=${OUTD:-/tmp/cme_bench}
mkdir -p "$OUTD"

export LD_LIBRARY_PATH="$CME_HOME/build/lib:${CONDA_PREFIX:-}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
[ -f "$CFG" ] || { echo "missing $CFG -- run: cgrame --gen-config-file $CFG"; exit 1; }

KERNELS=${*:-"mac mac2 mults1 mults2 sum conv2 conv3 accumulate cap matrixmultiply"}
printf '%-16s %-6s %-6s %-22s %s\n' kernel nodes ii result secs

for k in $KERNELS; do
	d=$CME_HOME/benchmarks/microbench/$k
	[ -d "$d" ] || continue
	dot=$(ls "$d"/*loop*.dot 2>/dev/null | head -1)
	[ -n "$dot" ] || dot=$(ls "$d"/*.dot 2>/dev/null | head -1)
	[ -n "$dot" ] || continue
	N=$(grep -c "opcode=" "$dot")
	done_ii=""
	for ii in 1 2 3 4 5 6 8 10 12 16; do
		L=$OUTD/${k}_ii${ii}.log
		S=$(date +%s)
		timeout 150 "$CME" -c 2 -i "$ii" -g "$dot" -m ClusteredMapper -t 120 \
			> "$L" 2>&1
		rc=$?
		E=$(( $(date +%s) - S ))
		if grep -qiE "Mapping Success|successful_?mapping|Mapping was successful" "$L"; then
			printf '%-16s %-6s %-6s %-22s %s\n' "$k" "$N" "$ii" "OK" "$E"
			done_ii=$ii
			break
		fi
		if [ $rc -eq 124 ]; then
			printf '%-16s %-6s %-6s %-22s %s\n' "$k" "$N" "$ii" "TIMEOUT" "$E"
			done_ii=T
			break
		fi
	done
	[ -z "$done_ii" ] && printf '%-16s %-6s %-6s %-22s %s\n' "$k" "$N" "-" "FAIL all II" "-"
done
