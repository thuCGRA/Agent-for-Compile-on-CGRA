#!/usr/bin/env python3
"""routes.json -> the --route-path format read by the mapper.

The ILP router emits a list of (x, y, t) steps per DFG edge, where t is a phase
modulo II. The mapper's A* works in absolute latency, so the file keeps the
phase and the C++ side matches it against `lat % II`.

Usage:
  routes_to_path.py routes.json -o edges.path [--ii 4]
"""
import argparse
import json
import re
import sys

STEP_RE = re.compile(r"^(-?\d+),(-?\d+),t(-?\d+)$")


def _step(s):
    """'2,0,t3' -> (2, 0, 3)."""
    m = STEP_RE.match(s.strip())
    if not m:
        raise ValueError("bad route step %r" % s)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def to_path(routes, ii=None):
    """Return [(src, dst, [(x, y, t), ...]), ...] with phases normalised."""
    out = []
    for r in routes:
        steps = [_step(s) for s in r["path"]]
        if not steps:
            raise ValueError("edge %s->%s has no steps" % (r["src"], r["dst"]))
        if ii:
            steps = [(x, y, t % ii) for (x, y, t) in steps]
        out.append((r["src"], r["dst"], steps))
    return out


def write_path(path, edges):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# src dst nsteps x0 y0 t0 x1 y1 t1 ...  (phases modulo II)\n")
        for src, dst, steps in edges:
            parts = [str(src), str(dst), str(len(steps))]
            for (x, y, t) in steps:
                parts += [str(x), str(y), str(t)]
            f.write(" ".join(parts) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("routes", help="routes.json from ilp_router.py")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--ii", type=int, default=None,
                    help="normalise phases modulo this II")
    a = ap.parse_args(argv)

    with open(a.routes, encoding="utf-8") as f:
        routes = json.load(f)
    edges = to_path(routes, a.ii)
    write_path(a.out, edges)
    hops = sum(1 for _, _, s in edges for _ in range(0))
    print("wrote %d edge path(s) to %s" % (len(edges), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
