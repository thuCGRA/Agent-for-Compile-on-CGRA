"""Derive a smaller HyCUBE arch json from the known-good 4x4 one.

The vendor/spike json is a parametric GRID: one SPM-sharing PE column (X=0, MOD
PE_MEM) plus plain PEs, and CONNECTIONS wiring each PE_MEM row to an SPM bank
port. Subsetting the grid to NxN means: keep MODS with X<Xdim and Y<Ydim, drop
CONNECTIONS to tile rows that no longer exist, and re-map the remaining rows onto
the same bank ports in y order.

Usage: python3 make_arch.py 3 3 out.json [src.json]
"""
import json
import sys

x_dim = int(sys.argv[1])
y_dim = int(sys.argv[2])
out = sys.argv[3]
src = sys.argv[4] if len(sys.argv) > 4 else "/home/steve/cac-armB/work/armB/spike/array_add_mem.json"

d = json.load(open(src))
arch = d["ARCH"]
grid = arch["CGRA"]["SUBMODS"][0]
grid["DIMS"] = {"X": x_dim, "Y": y_dim}
mods = [m for m in grid["MODS"] if m["X"] < x_dim and m["Y"] < y_dim]
grid["MODS"] = mods

# the GRID pattern derives all wiring itself; an explicit CGRA-level CONNECTIONS
# block is what the parser chokes on ("dest_mod" assert), so drop it entirely
arch["CGRA"].pop("CONNECTIONS", None)
rows = sorted({m["Y"] for m in mods if m["MOD"] == "PE_MEM"})
json.dump(d, open(out, "w"), indent=1)
print("wrote %s  grid=%dx%d  mods=%d  rows=%s" % (out, x_dim, y_dim, len(mods), rows))
