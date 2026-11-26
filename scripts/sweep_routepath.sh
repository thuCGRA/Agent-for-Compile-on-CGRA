#!/usr/bin/env bash
# Loop the single-kernel replay self-check over the kernels that have traces.
set -u
cd ~/acc-thu-ilp || exit 1
for k in ${*:-array_add mac fir conv2 trmm}; do
	printf '### %s\n' "$k"
	bash scripts/test_routepath.sh "$k" 2>&1 | grep -E "Map Success|Map Failed|differing bytes|no routeInfo|no bitstream|could not build"
done
