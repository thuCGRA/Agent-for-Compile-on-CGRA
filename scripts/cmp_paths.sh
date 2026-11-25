#!/usr/bin/env bash
# Same placement, two routers: diff the per-edge paths to see where inject diverges.
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
D=/tmp/cmp; rm -rf $D; mkdir -p $D
k=${1:-array_add}
W="$(kdir "$k")"
bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
B=$D/${k}_base; I=$D/${k}_inj
for d in $B $I; do rm -rf $d; mkdir -p $d; cp "$W/dfg.xml" "$W/arch.json" $d/; done
( cd $B && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
    -t HyCUBE_4REG -m 0 > map.log 2>&1 )
log=$(ls -S $B/*routeInfo.log | head -1)
~/acc-thu-ilp/.venv/bin/python tools/pins/extract_pins.py "$log" -o $D/$k.pins >/dev/null 2>&1
( cd $I && timeout 180 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
    -t HyCUBE_4REG -m 0 --pins $D/$k.pins --pins_mode 6 > map.log 2>&1 )
ilog=$(ls -S $I/*routeInfo.log | head -1)
echo "base=$log"
echo "inj =$ilog"
~/acc-thu-ilp/.venv/bin/python - "$log" "$ilog" <<'PY'
import re, sys

def parse(path):
    """edge -> ordered list of hop names (the CGRA_Ins.<port> tokens)."""
    edges = {}
    cur = None
    for line in open(path, errors="replace"):
        m = re.match(r"node=(\d+),mapped=", line)
        if m:
            cur = None
            continue
        m = re.search(r"assigning path from:(\d+) to:(\d+)", line)
        if m:
            cur = (int(m.group(1)), int(m.group(2)))
            edges.setdefault(cur, [])
            continue
        m = re.search(r"CGRA_Ins\.([^\s,]+)", line)
        if m and cur is not None:
            tok = m.group(1)
            if not edges[cur] or edges[cur][-1] != tok:
                edges[cur].append(tok)
    return edges

a, b = parse(sys.argv[1]), parse(sys.argv[2])
common = sorted(set(a) & set(b))
print("edges base=%d inj=%d common=%d" % (len(a), len(b), len(common)))
diff = [e for e in common if a[e] != b[e]]
print("differing edges: %d" % len(diff))
for e in diff[:8]:
    print("  %s -> %s" % e)
    print("    base:", " > ".join(a[e]))
    print("    inj :", " > ".join(b[e]))
PY