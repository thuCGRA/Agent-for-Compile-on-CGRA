#!/usr/bin/env python3
"""CEGIS: CP-SAT placement -> CP-SAT routing -> nogood -> re-solve.

The two-stage decomposition is unsound on its own: the placement model charges a
route as a hop count and never says which PEs the value crosses, so a placement
can be perfectly legal (one node per PE-phase slot, ops supported, banks right)
and still be unroutable.  Measured on array_add squeezed into one row: 20 nodes
in 4 PE x II=5 slots is legal and *infeasible* to route, while the same
placement at II=8 routes with 46 hops / 63 registers.

This loop closes the gap without collapsing the two models into one (which is
what CGRA-ME's ILPMapper does, at the cost of O(E * PE * II * dir) variables):

    solve placement  ->  solve routing  ->  routable?  -> done
                             |                  |
                             +-- INFEASIBLE ----+--> add "not this exact
                                                      placement" and retry

The nogood is a single clause over the full placement, which is sound: it
removes exactly the point the router disproved, nothing more.  Convergence is
therefore a real measurement, not an assumption -- `--rounds` bounds it and the
report says how many rounds it actually took.

Usage
-----
    python cegis.py --dfg out/array_add/dfg.xml --arch out/array_add/arch.json \\
        --ii 4 --rounds 8 --pins-out /tmp/pins.txt --guide-out /tmp/guide.txt
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ilp_mapper import build_and_solve, parse_arch, parse_dfg  # noqa: E402
from ilp_router import solve_routes  # noqa: E402
from routes_to_guide import to_guide  # noqa: E402


def write_pins(path, pins):
    with open(path, "w") as f:
        f.write("# idx x y t  (CEGIS)\n")
        for idx in sorted(pins):
            x, y, t = pins[idx]
            f.write("%d %d %d %d\n" % (idx, x, y, t))


def write_guide(path, routes, slack, arch):
    with open(path, "w") as f:
        f.write("# src dst max_hops n_pes x0 y0 x1 y1 ...  (slack=%d)\n" % slack)
        for ln in to_guide(routes, slack, arch["xdim"], arch["ydim"]):
            f.write(ln + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dfg", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--ii", type=int, required=True)
    ap.add_argument("--rounds", type=int, default=8, help="max CEGIS iterations")
    ap.add_argument("--max-hops", type=int, default=4)
    ap.add_argument("--place-time-limit", type=float, default=20.0)
    ap.add_argument("--route-time-limit", type=float, default=20.0)
    ap.add_argument("--timing", choices=("bypass", "register"), default="bypass")
    ap.add_argument("--select-coloc",
                    choices=("exactly", "atmost", "atleast", "off"),
                    default="exactly")
    ap.add_argument("--route-cycles", type=int, default=-1,
                    help="-1 = auto (d <= manhattan+1); see ilp_mapper.py")
    ap.add_argument("--pins-out", default=None)
    ap.add_argument("--guide-out", default=None)
    ap.add_argument("--guide-slack", type=int, default=1,
                    help="PE corridor expansion radius for the mapper guide "
                         "(0 = exact coarse path; the mapper needs >=1 because its "
                         "port graph is finer than the router's PE graph)")
    ap.add_argument("--routes-out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    nodes = parse_dfg(args.dfg)
    arch = parse_arch(args.arch)
    print("kernel: %d node(s), arch %dx%d, II=%d, timing=%s"
          % (len(nodes), arch["xdim"], arch["ydim"], args.ii, args.timing))

    forbidden = []
    t0 = time.time()
    rows = []
    for rnd in range(1, args.rounds + 1):
        pins, info = build_and_solve(
            nodes, arch, args.ii, args.max_hops, args.place_time_limit,
            "hops", args.route_cycles, args.timing, forbidden,
            args.select_coloc,
        )
        if pins is None:
            print("round %d: placement %s  -> stop (no more placements)" % (rnd, info))
            rows.append((rnd, "place-fail", info, "", ""))
            break

        routes, rinfo = solve_routes(
            nodes, arch, pins, args.ii, args.max_hops, 1, 1,
            args.route_time_limit, "hops",
        )
        routable = routes is not None
        rows.append((rnd, "routable" if routable else "UNROUTABLE", info, rinfo, len(forbidden)))
        if not args.quiet:
            print("round %d: place %s" % (rnd, info))
            print("         route %s" % rinfo)

        if routable:
            if args.pins_out:
                write_pins(args.pins_out, pins)
                print("wrote pins  -> %s" % args.pins_out)
            if args.guide_out:
                write_guide(args.guide_out, routes, args.guide_slack, arch)
                print("wrote guide -> %s" % args.guide_out)
            if args.routes_out:
                json.dump(routes, open(args.routes_out, "w"), indent=1)
                print("wrote routes-> %s" % args.routes_out)
            break

        # Sound cut: forbid exactly this placement, nothing else.
        forbidden.append(dict(pins))
    else:
        print("round %d: budget exhausted, still unroutable" % args.rounds)

    print("\n%-6s %-12s %-46s %-40s" % ("round", "verdict", "placement", "routing"))
    for rnd, verdict, info, rinfo, _nf in rows:
        print("%-6d %-12s %-46s %-40s" % (rnd, verdict, info, rinfo))
    ok = bool(rows) and rows[-1][1] == "routable"
    print("\n%s after %d round(s), %.1fs total"
          % ("ROUTABLE" if ok else "NOT ROUTABLE", len(rows), time.time() - t0))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
