#!/usr/bin/env python3
"""Convert `ilp_router.py`'s routes.json into the mapper's `--routes` guide.

The mapper does not need the exact port-level path -- it re-derives that itself
with its own A* over the real port graph, which is what keeps the result a legal
HyCUBE mapping.  What it *does* need is the corridor: which PEs the value is
allowed to cross, and how many hops it may take.

Why the corridor needs slack
----------------------------
`ilp_router.py` models the fabric at `(x, y, t)` granularity and folds port-level
contention away: it charges capacity per PE-to-PE link, not per crossbar port.
The mapper's own graph is finer -- it tracks `NORTH_XBARI`, the four input
registers and the "conflict ports" of the destination datapath, so a value that
the coarse model sends straight through one PE may in reality have to step to a
neighbour and back to reach a free input port.  A corridor of exactly the coarse
path therefore rejects legal routes.

`--slack` fixes that: every PE on the coarse path is expanded to all PEs within
`slack` Manhattan distance, and the hop budget grows by the same amount.  Slack
0 reproduces the coarse path exactly (useful for measuring the gap); slack 1 is
the default and covers the port-detour case; slack >= 4 degenerates to "the
whole array", i.e. no guidance at all.

Usage
-----
    python routes_to_guide.py routes.json --slack 1 -o guide.txt
"""

import argparse
import json
import re
import sys


def _step_pe(step):
    """`"2,0,t3"` -> `(2, 0)`."""
    parts = step.split(",")
    return int(re.sub(r"[^0-9-]", "", parts[0])), int(re.sub(r"[^0-9-]", "", parts[1]))


def to_guide(routes, slack=1, xdim=None, ydim=None):
    """routes.json (list of {src,dst,n_hops,path}) -> list of guide lines."""
    lines = []
    for r in routes:
        path_pes = []
        for step in r["path"]:
            pe = _step_pe(step)
            if not path_pes or path_pes[-1] != pe:
                path_pes.append(pe)
        if not path_pes:
            continue

        if slack > 0:
            expanded = set()
            for (px, py) in path_pes:
                for dx in range(-slack, slack + 1):
                    for dy in range(-slack, slack + 1):
                        if abs(dx) + abs(dy) > slack:
                            continue
                        x, y = px + dx, py + dy
                        if xdim is not None and not (0 <= x < xdim):
                            continue
                        if ydim is not None and not (0 <= y < ydim):
                            continue
                        expanded.add((x, y))
            pes = sorted(expanded)
            budget = r["n_hops"] + slack
        else:
            pes = path_pes
            budget = r["n_hops"]

        flat = " ".join("%d %d" % p for p in pes)
        lines.append("%d %d %d %d %s" % (r["src"], r["dst"], budget, len(pes), flat))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("routes", help="routes.json produced by ilp_router.py")
    ap.add_argument("--slack", type=int, default=1,
                    help="PE corridor expansion radius (0 = exact coarse path)")
    ap.add_argument("--xdim", type=int, default=None)
    ap.add_argument("--ydim", type=int, default=None)
    ap.add_argument("-o", "--out", default="guide.txt")
    args = ap.parse_args(argv)

    routes = json.load(open(args.routes))
    lines = to_guide(routes, args.slack, args.xdim, args.ydim)
    with open(args.out, "w") as f:
        f.write("# src dst max_hops n_pes x0 y0 x1 y1 ...  (slack=%d)\n" % args.slack)
        f.write("# generated from %s by routes_to_guide.py\n" % args.routes)
        for ln in lines:
            f.write(ln + "\n")
    hops = sum(int(ln.split()[2]) for ln in lines)
    print("wrote %d corridor(s) to %s (slack=%d, total hops=%d)"
          % (len(lines), args.out, args.slack, hops))
    return 0


if __name__ == "__main__":
    sys.exit(main())
