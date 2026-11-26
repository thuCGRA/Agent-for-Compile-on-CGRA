#!/usr/bin/env python3
"""*.routeInfo.log -> the --route-path format.

This is the self-check for the route-path mechanism: replaying a baseline's own
route through A* must reproduce that baseline's bitstream byte for byte. If it
does not, the waypoint constraint is wrong, not the ILP.

Port names look like `CGRA_Ins.PE_X1|_Y2|-T2.WEST_I` (the `|` is literal) or
`CGRA_Ins.PE_MEM_X0_Y2-T2.FU0.DP0_T` for the memory column.
"""
import argparse
import re
import sys

PE_RE = re.compile(r"CGRA_Ins\.PE(_MEM)?_X(\d+)[|_]?_Y(\d+)[|_]?-T(\d+)\.")
EDGE_RE = re.compile(r"^\s*parent:(\d+)\s*$")
NODE_RE = re.compile(r"^node=(\d+),mapped=")


def parse(log_path):
    """-> {(src, dst): [(x, y, t), ...]} with consecutive duplicates removed."""
    edges = {}
    consumer = None
    parent = None
    cur = None
    for line in open(log_path, encoding="utf-8", errors="replace"):
        m = NODE_RE.match(line)
        if m:
            consumer = int(m.group(1))
            parent = None
            cur = None
            continue
        m = EDGE_RE.match(line)
        if m:
            parent = int(m.group(1))
            cur = edges.setdefault((parent, consumer), [])
            continue
        if cur is None or parent is None:
            continue
        m = PE_RE.search(line)
        if not m:
            continue
        step = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
        if not cur or cur[-1] != step:
            cur.append(step)
    return {k: v for k, v in edges.items() if len(v) >= 2}


def write_path(path, edges):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# src dst nsteps x0 y0 t0 ...  (absolute latencies, from routeInfo)\n")
        for (src, dst), steps in sorted(edges.items()):
            parts = [str(src), str(dst), str(len(steps))]
            for (x, y, t) in steps:
                parts += [str(x), str(y), str(t)]
            f.write(" ".join(parts) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)
    edges = parse(a.log)
    if not edges:
        sys.exit("no edges parsed from %s" % a.log)
    write_path(a.out, edges)
    print("wrote %d edge path(s) to %s" % (len(edges), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
