"""mrrg_router routes.json -> --port-path file.

The MRRG router already speaks ports, so this is a pure reformat: the mapper's
--port-path hook wants one line per edge with an explicit step count, while the
router stores the steps as a list of ``(x, y, t, port)`` tuples.
"""

import argparse
import json
import sys


def _collapse_single_pe(steps):
    # SINGLE_PORT_COLLAPSE: a value that never leaves its PE and never waits a
    # cycle is recorded by the mapper as a bare sink port, e.g. a chain of one
    # element ``FU0.DP0.I1`` (see routeInfo.log).  Emitting the full
    # ``FU0.DP0_T -> ... -> FU0.DP0.I1`` walk instead marks FU0.DP0_T as owned
    # by this edge at the sink latency, which perturbs the emitter's ALU_I
    # detection (it compares dp->getInPort(op) against fu->getOutPort("DP0_T")).
    if len(steps) > 1 and len(set((x, y, t) for (x, y, t, _p) in steps)) == 1:
        return [steps[-1]]
    return steps


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("routes")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--ii", type=int, default=0, help="only for the sanity check")
    args = ap.parse_args(argv)

    with open(args.routes) as fh:
        data = json.load(fh)
    routes = data.get("routes") or []

    lines = ["# src dst nsteps x y t port ..."]
    worst = 0
    for r in routes:
        steps = r.get("path") or r.get("steps") or []
        if not steps:
            continue
        steps = _collapse_single_pe(steps)
        parts = [str(r["src"]), str(r["dst"]), str(len(steps))]
        for st in steps:
            if isinstance(st, dict):
                x, y, t, port = st["x"], st["y"], st["t"], st["port"]
            else:
                x, y, t, port = st
            parts += [str(x), str(y), str(t), port]
        lines.append(" ".join(parts))
        worst = max(worst, r.get("regs", 0))

    with open(args.out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("port-path: %d edge(s) -> %s (worst edge %d register step(s))"
          % (len(lines) - 1, args.out, worst))
    if args.ii and worst > args.ii - 1:
        print("WARNING: an edge uses more register steps than II-1; the phase "
              "reconstruction in --port-path will reject it", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
