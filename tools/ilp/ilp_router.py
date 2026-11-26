"""Route a *fixed* placement on the HyCUBE 4x4 fabric with CP-SAT.

Why this exists
---------------
`ilp_mapper.py` decides *where* each DFG node runs and *when* (its phase), but it
only charges a route as a hop count -- it never says which PEs the value passes
through.  The mapper's own A* then has to invent the path, and it does so
greedily, one node at a time, so a placement that looks fine in aggregate can
still be rejected ("pin (1,1,1) not among 16 candidate(s)").  This module closes
that gap: given a placement, it *routes every edge* and either produces concrete
paths or proves that none exist.

Model (time-expanded PE grid, faithful to the HyCUBE micro-architecture)
-----------------------------------------------------------------------
From `arch.json` -> `PE.CONNECTIONS`:

    FU0.DP0_T -> {NORTH_O, EAST_O, WEST_O, SOUTH_O, TREG}       (no cost for O)
    X_I       -> {XR, X_XBARI}                                  (XR = 1 cycle)
    X_XBARI   -> {FU0.DP0_I1, FU0.DP0_I2, FU0.DP0_P,
                  NORTH_O, EAST_O, WEST_O, SOUTH_O}
    TREG      -> {FU0.DP0_I1, FU0.DP0_I2, FU0.DP0_P, * _O}

so a value leaves a PE's FU output combinationally on any of the four output
ports, arrives at the neighbour's input port combinationally, and crosses the
crossbar to the FU input or straight back out -- all in the *same* cycle.  The
only way to spend a cycle is to cross a register: the output-side `TREG` or one
of the input-side `NR/ER/WR/SR`.

That gives exactly two kinds of arc on the resource graph whose node is
``(x, y, t)`` with ``t`` modulo II:

* **bypass arc**  ``(x, y, t) -> (x + dx, y + dy, t)``   cost 1 hop, 0 cycles
* **register arc** ``(x, y, t) -> (x, y, t + 1)``         cost 0 hops, 1 cycle

An edge ``u -> v`` is one unit of flow from the producer's *output time*
``(t_u + lat_u) mod II`` on ``PE(u)`` to the consumer's phase ``t_v`` on
``PE(v)``.  The number of register arcs the flow uses is therefore exactly the
``d`` of `ilp_mapper.py`, and the number of bypass arcs is ``h``.

Multicast
---------
HyCUBE fans a value out for free: in the baseline `routeInfo.log`, node 0's
three children all share the very first hop ``PE_X1_Y2-T0.EAST_O ->
PE_X2_Y2-T0.WEST_I``.  So capacity is charged **per producer**, not per edge:
one producer's value may broadcast on an arc, two different producers may not.
That is the same thing CGRA-ME models with its fanout `S` variables.

CLI
---
    python ilp_router.py --dfg out/array_add/dfg.xml --arch out/array_add/arch.json \\
        --pins /tmp/pins_array_add_bypass.txt --ii 4 -o /tmp/routes.json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ilp_mapper import parse_dfg, parse_arch  # noqa: E402


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def load_pins(path):
    """Read ``# node x y t`` lines.  ``t < 0`` is not routeable here."""
    pins = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        idx, x, y, t = (int(v) for v in parts[:4])
        pins[idx] = (x, y, t)
    return pins


def lat_of(arch, pe, op):
    table = arch["mem"] if arch["kind"].get(pe) == "PE_MEM" else arch["alu"]
    return table.get(op, 2 if arch["kind"].get(pe) == "PE_MEM" else 1)


def hops(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


# --------------------------------------------------------------------------- #
# the routing model
# --------------------------------------------------------------------------- #
def build_edges(nodes, arch, pins, ii):
    """One flow unit per DFG edge, with its fixed source and sink node."""
    specs = []
    for n in nodes.values():
        if n.idx not in pins:
            continue
        px, py, pt = pins[n.idx]
        src_t = (pt + lat_of(arch, (px, py), n.op)) % ii
        for (v, nextiter, _npb, _typ) in n.outs:
            if v not in nodes or v not in pins:
                continue
            qx, qy, qt = pins[v]
            dst_t = (qt + nextiter * ii) % ii
            specs.append({
                "src": n.idx,
                "dst": v,
                "src_node": (px, py, src_t),
                "dst_node": (qx, qy, dst_t),
                "min_hops": hops((px, py), (qx, qy)),
            })
    return specs


def solve_routes(nodes, arch, pins, ii, max_hops=4, link_cap=1, reg_cap=1,
                 time_limit=60.0, objective="hops"):
    from ortools.sat.python import cp_model

    xdim, ydim = arch["xdim"], arch["ydim"]
    specs = build_edges(nodes, arch, pins, ii)
    if not specs:
        return None, "no routeable edge (placement missing nodes?)"

    # ---- resource graph -------------------------------------------------- #
    grid = [(x, y) for y in range(ydim) for x in range(xdim)]
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    bypass = []   # (u, v)  u=(x,y,t) v=(x+dx,y+dy,t)
    for (x, y) in grid:
        for (dx, dy) in dirs:
            nx, ny = x + dx, y + dy
            if (nx, ny) not in arch["kind"]:
                continue
            for t in range(ii):
                bypass.append(((x, y, t), (nx, ny, t)))
    register = [((x, y, t), (x, y, (t + 1) % ii))
                for (x, y) in grid for t in range(ii)]

    arcs = [(a, "b", 1, 0) for a in bypass] + [(a, "r", 0, 1) for a in register]

    model = cp_model.CpModel()

    # ---- flow variables --------------------------------------------------- #
    f = {}          # (edge_i, arc_i) -> BoolVar
    by_prod_arc = {}  # (producer, arc_i) -> list of edge_i
    for ei, sp in enumerate(specs):
        for ai, (_arc, _kind, _hc, _rc) in enumerate(arcs):
            v = model.NewBoolVar("f_%d_%d" % (ei, ai))
            f[(ei, ai)] = v
            by_prod_arc.setdefault((sp["src"], ai), []).append(ei)

    # ---- flow conservation ------------------------------------------------ #
    for ei, sp in enumerate(specs):
        if sp["src_node"] == sp["dst_node"]:
            for ai in range(len(arcs)):
                model.Add(f[(ei, ai)] == 0)
            continue
        in_at, out_at = {}, {}
        for ai, (arc, _k, _hc, _rc) in enumerate(arcs):
            u, v = arc
            out_at.setdefault(u, []).append(ai)
            in_at.setdefault(v, []).append(ai)
        for node in set(list(in_at) + list(out_at)):
            rhs = 0
            if node == sp["src_node"]:
                rhs += 1
            if node == sp["dst_node"]:
                rhs -= 1
            # outflow - inflow == supply (a "+1" at the source, "-1" at the
            # sink).  Getting this sign the wrong way round silently routes
            # every value backwards, from consumer to producer.
            model.Add(
                sum(f[(ei, ai)] for ai in out_at.get(node, []))
                - sum(f[(ei, ai)] for ai in in_at.get(node, []))
                == rhs
            )

    # ---- per-producer capacity (multicast is free) ------------------------ #
    used = {}
    for (prod, ai), eis in by_prod_arc.items():
        cap = link_cap if arcs[ai][1] == "b" else reg_cap
        y = model.NewBoolVar("y_%d_%d" % (prod, ai))
        for ei in eis:
            model.Add(f[(ei, ai)] <= y)
        model.Add(y <= sum(f[(ei, ai)] for ei in eis))
        used[(prod, ai)] = y
    for ai, (_arc, kind, _hc, _rc) in enumerate(arcs):
        cap = link_cap if kind == "b" else reg_cap
        users = [used[(prod, ai)] for (prod, a2) in used if a2 == ai]
        if len(users) > 1:
            model.Add(sum(users) <= cap)

    # ---- per-edge length bounds ------------------------------------------- #
    for ei, sp in enumerate(specs):
        b_arcs = [f[(ei, ai)] for ai, (_a, k, _h, _r) in enumerate(arcs) if k == "b"]
        r_arcs = [f[(ei, ai)] for ai, (_a, k, _h, _r) in enumerate(arcs) if k == "r"]
        model.Add(sum(b_arcs) <= max_hops)
        model.Add(sum(r_arcs) <= ii - 1)
        # At least one arc: a route with no arcs is not a route.
        model.Add(sum(b_arcs) + sum(r_arcs) >= 1)

    # ---- operand exclusivity at SELECT (# SELECTEXCL) ---------------------- #
    # The reference simulator asserts ``!(I1isvalid && I2isvalid)`` for
    # SELECT (src/simulator/CGRATile.cpp:168 and CGRATile::select()), i.e. at
    # most one data operand may be *delivered through the crossbar* in the
    # cycle the SELECT executes.  A value that entered the PE one cycle
    # earlier through an input register is already local and does not count,
    # so the rule is about the LAST arc of each parent: at most one parent may
    # enter ``dst_node`` through a bypass arc.
    last_bypass = {}
    for ei, sp in enumerate(specs):
        lits = [f[(ei, ai)] for ai, (arc, k, _h, _r) in enumerate(arcs)
                if k == "b" and arc[1] == sp["dst_node"]]
        if lits:
            last_bypass[ei] = lits
    sel_parents = {}
    for ei, sp in enumerate(specs):
        if nodes[sp["dst"]].op == "SELECT":
            sel_parents.setdefault(sp["dst"], []).append(ei)
    for _dst, eis in sel_parents.items():
        lits = []
        for ei in eis:
            lits += last_bypass.get(ei, [])
        if len(lits) > 1:
            model.Add(sum(lits) <= 1)

    # ---- objective -------------------------------------------------------- #
    if objective == "hops":
        terms = [10 * f[(ei, ai)] for ei in range(len(specs))
                 for ai, (_a, k, _h, _r) in enumerate(arcs) if k == "b"]
        terms += [f[(ei, ai)] for ei in range(len(specs))
                  for ai, (_a, k, _h, _r) in enumerate(arcs) if k == "r"]
        if terms:
            model.Minimize(sum(terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, name

    routes = []
    for ei, sp in enumerate(specs):
        path, cur, steps = [], sp["src_node"], 0
        while cur != sp["dst_node"] and steps <= max_hops + ii:
            path.append(cur)
            nxt = None
            for ai, (arc, _k, _hc, _rc) in enumerate(arcs):
                if arc[0] == cur and solver.Value(f[(ei, ai)]) == 1:
                    nxt = arc[1]
                    break
            if nxt is None:
                break
            cur = nxt
            steps += 1
        path.append(cur)
        if cur != sp["dst_node"]:
            return None, ("routing self-check failed: edge %d->%d stopped at %s, "
                          "expected %s" % (sp["src"], sp["dst"], cur, sp["dst_node"]))
        nh = sum(1 for a, b in zip(path, path[1:]) if a[:2] != b[:2])
        nr = sum(1 for a, b in zip(path, path[1:]) if a[:2] == b[:2])
        routes.append({
            "src": sp["src"], "dst": sp["dst"],
            "n_hops": nh, "n_regs": nr,
            "path": ["%d,%d,t%d" % (p[0], p[1], p[2]) for p in path],
        })

    info = "%-9s edges=%-3d hops=%-3d regs=%-3d wall=%.2fs" % (
        name, len(routes),
        sum(r["n_hops"] for r in routes),
        sum(r["n_regs"] for r in routes),
        solver.WallTime(),
    )
    return routes, info


# --------------------------------------------------------------------------- #
# comparison against the mapper's own routeInfo.log
# --------------------------------------------------------------------------- #
PE_RE = re.compile(r"PE(?:_MEM)?_X(\d+)\|?_Y(\d+)\|?-T(\d+)")


def parse_routeinfo(path):
    """Return {child_idx: [(n_hops, n_regs), ...]} -- one entry per parent edge.

    A *spatial hop* is a change of ``(x, y)`` between two consecutive lines of
    the chain; a *register crossing* is the same ``(x, y)`` at a different
    ``t`` (``TREG_RO`` -> ``TREG_RI``, or ``X_I`` -> ``XR``).  Counting both is
    what makes this comparable with the CP-SAT model, whose cost is
    ``hops + regs``.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return {}
    res, cur_child, cur_parent, chain = {}, None, None, []

    def flush():
        if cur_child is None or cur_parent is None:
            return
        seq = []
        for ln in chain:
            m = PE_RE.search(ln)
            if m:
                seq.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        if seq:
            nh = sum(1 for a, b in zip(seq, seq[1:]) if a[:2] != b[:2])
            nr = sum(1 for a, b in zip(seq, seq[1:]) if a[:2] == b[:2])
            res.setdefault(cur_child, []).append((nh, nr))

    for line in open(path, errors="replace"):
        s = line.strip()
        m = re.match(r"^node=(\d+),mapped=", s)
        if m:
            flush()
            cur_child, cur_parent, chain = int(m.group(1)), None, []
            continue
        m = re.match(r"^parent:(\d+)", s)
        if m:
            flush()
            cur_parent, chain = int(m.group(1)), []
            continue
        if s.startswith("CGRA_Ins."):
            chain.append(s)
    flush()
    return {k: (sum(a for a, _ in v), sum(b for _, b in v)) for k, v in res.items()}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dfg", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--pins", required=True, help="placement: 'idx x y t' per line")
    ap.add_argument("--ii", type=int, required=True)
    ap.add_argument("--max-hops", type=int, default=4)
    ap.add_argument("--link-capacity", type=int, default=1)
    ap.add_argument("--reg-capacity", type=int, default=1)
    ap.add_argument("--time-limit", type=float, default=60.0)
    ap.add_argument("--objective", choices=("hops", "feasible"), default="hops")
    ap.add_argument("--compare-routeinfo", default=None,
                    help="optional *.routeInfo.log to print a side-by-side hop count")
    ap.add_argument("-o", "--out", default="routes.json")
    args = ap.parse_args(argv)

    nodes = parse_dfg(args.dfg)
    arch = parse_arch(args.arch)
    pins = load_pins(args.pins)
    missing = [n.idx for n in nodes.values() if n.idx not in pins]
    if missing:
        print("placement covers %d/%d nodes, missing %s"
              % (len(pins), len(nodes), missing[:8]))
        return 2

    routes, info = solve_routes(nodes, arch, pins, args.ii, args.max_hops,
                               args.link_capacity, args.reg_capacity,
                               args.time_limit, args.objective)
    print("route: %s" % info)
    if routes is None:
        return 1

    json.dump(routes, open(args.out, "w"), indent=1)
    print("wrote %d route(s) to %s" % (len(routes), args.out))

    if args.compare_routeinfo:
        ref = parse_routeinfo(args.compare_routeinfo)
        mine = {}
        for r in routes:
            if r["n_hops"] or r["n_regs"]:
                mine.setdefault(r["dst"], []).append((r["n_hops"], r["n_regs"]))
        mine = {k: (sum(a for a, _ in v), sum(b for _, b in v))
                for k, v in mine.items()}
        # Only the *spatial* hop count is compared: the routeInfo.log port
        # list repeats ports and interleaves parents, so counting register
        # crossings out of it is not reliable.  Hops are, and they are what
        # says whether the two routers agree about geometry.
        print("\nspatial hops only (register counting out of routeInfo is unreliable)")
        print("%-6s %-10s %-10s" % ("dst", "cp-sat", "mapper"))
        diff = 0
        for k in sorted(set(mine) | set(ref)):
            a = mine.get(k, (0, 0))[0]
            b = ref.get(k, (0, 0))[0]
            if a != b:
                diff += 1
            print("%-6d %-10d %-10d %s" % (k, a, b, "" if a == b else "<-"))
        print("\n%d edge(s) differ in spatial hops" % diff)
    return 0


if __name__ == "__main__":
    sys.exit(main())
