#!/usr/bin/env python3
"""A CP-SAT (OR-Tools) mapper for the HyCUBE 4x4 architecture in acc-thu.

Why this exists
---------------
`cgra_xml_mapper -m 0` (PathFinder) is a greedy heuristic: it places nodes in
topological order, routes each edge with A*, and when it fails it bumps II and
starts over.  On the three kernels in this repo it never has to backtrack, so
its answer looks authoritative -- but nothing tells us whether a better (or
even just a *different*) placement exists at the same II.

This solver answers the placement + scheduling question exactly, under a
deliberately simplified routing model.  An edge from u to v consumes
`L + d` cycles, where `L` is the operator latency and `d` is the number of
cycles the route itself takes: a combinational transfer costs 0, each register
hop costs 1, and `d` is bounded by `manhattan(u, v) + 1` (the `+1` covers a
hop through the local TREG, which costs a cycle even when both ops sit on the
same PE).  So `t_v = (t_u + L + d + nextiter * II) mod II`.

That model ignores port contention and link capacity, so its answer is a
*candidate*: it is handed to the real mapper via `--pins` and only counts as a
mapping once PathFinder routes it and the simulator reports zero mismatches.

Model
-----
Variables (one per node/slot, slot = (PE, phase)):
    at[n][s]  bool    node n occupies slot s
    pe[n], ph[n]      int, channelled from at[] by equality to a constant sum
    h[e]              int, hop count of edge e

Constraints:
    sum_s at[n][s] == 1                 every node placed exactly once
    sum_n at[n][s] <= 1                 one op per PE per cycle
    at[n][s] == 0                       op not supported by that PE / bank mismatch
    AddAllowedAssignments(pe_u,ph_u,pe_v,ph_v,h_e)   timing + distance table
    co(p1) + co(p2) == 1                SELECT: exactly one operand is local
                                        (co(p) = p and the SELECT share a PE)

Objective: minimise total hop count (i.e. total wire pressure).

Usage:
    ilp_mapper.py --dfg dfg.xml --arch arch.json --ii 4 -o pins.txt
"""
import os
import argparse
import json
import re
import sys

# A HyCUBE PartPred DFG uses O-prefixed names for the memory PE's own ops
# (OLOAD/OLOADB/OLOADH/OLOADCL/OSTORE/OSTOREB/OSTOREH) alongside the plain
# LOAD/STORE forms the pass emits for loop bookkeeping, so match on substring.
MEM_OPS = frozenset(("LOAD", "LOADB", "LOADH", "LOADCL", "STORE", "STOREB", "STOREH"))


def is_mem_op(op):
    """True for both LOAD/STORE and HyCUBE's OLOAD/OSTORE spellings."""
    return ("LOAD" in op) or ("STORE" in op)

# attributes inside <Node ...> are sometimes missing the space before the next
# key (e.g. ALAP="0"BB=""CONST="1"), so a real XML parser is not an option.
NODE_RE = re.compile(r"<Node\s+(.*?)>", re.S)
ATTR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"')
OP_RE = re.compile(r"<OP>\s*([^<\s]+)\s*</OP>")
BP_RE = re.compile(r"<BasePointerName[^>]*>\s*([^<\s]+)\s*</BasePointerName>")
# <Output> comes in two shapes: with and without the NPB attribute
#   <Output idx="12" nextiter="0" NPB="0" type="P"/>
#   <Output idx="1"  nextiter="0" type="I1"/>
OUT_RE = re.compile(r"<Output\s+([^/>]*)/?>")
DFG_COUNT_RE = re.compile(r"<DFG\s+count\s*=\s*\"(\d+)\"")


class Node(object):
    __slots__ = ("idx", "op", "const", "bp", "asap", "alap", "outs")

    def __init__(self, idx, op, const, bp, asap, alap):
        self.idx = idx
        self.op = op
        self.const = const
        self.bp = bp
        self.asap = asap
        self.alap = alap
        self.outs = []  # (child_idx, nextiter, npb, type)


def parse_dfg(path):
    with open(path, "r", errors="replace") as fh:
        text = fh.read()
    nodes = {}
    # split on <Node ...> so each chunk owns its own <Outputs>
    starts = [m.start() for m in NODE_RE.finditer(text)]
    if not starts:
        raise SystemExit("no <Node> found in %s" % path)
    for i, s in enumerate(starts):
        head_end = text.index(">", s) + 1
        head = text[s:head_end]
        body = text[head_end: starts[i + 1] if i + 1 < len(starts) else len(text)]
        attrs = dict(ATTR_RE.findall(head))
        idx = int(attrs.get("idx", -1))
        op_m = OP_RE.search(body)
        op = op_m.group(1) if op_m else "NOP"
        bp_m = BP_RE.search(body)
        n = Node(
            idx,
            op,
            int(attrs.get("CONST", "0") or 0),
            bp_m.group(1) if bp_m else "",
            int(attrs.get("ASAP", "0") or 0),
            int(attrs.get("ALAP", "0") or 0),
        )
        for m in OUT_RE.finditer(body):
            oa = dict(ATTR_RE.findall(m.group(1)))
            if "idx" not in oa:
                continue
            n.outs.append(
                (
                    int(oa["idx"]),
                    int(oa.get("nextiter", "0") or 0),
                    int(oa.get("NPB", "0") or 0),
                    oa.get("type", "I1"),
                )
            )
        nodes[idx] = n
    return nodes


def parse_arch(path):
    with open(path, "r") as fh:
        raw = json.load(fh)
    arch = raw.get("ARCH", raw)
    grid = None
    for sub in arch.get("CGRA", {}).get("SUBMODS", []):
        if sub.get("PATTERN") == "GRID":
            grid = sub
            break
    if grid is None:
        raise SystemExit("no GRID submodule in %s" % path)
    xdim = int(grid["DIMS"]["X"])
    ydim = int(grid["DIMS"]["Y"])
    kind = {}
    for m in grid.get("MODS", []):
        kind[(int(m["X"]), int(m["Y"]))] = m["MOD"]

    alu = dict(arch.get("FU", {}).get("OPS", {}))
    mem = dict(arch.get("FU_MEM", {}).get("OPS", {}))

    # bank wiring: PE_MEM_X0_Y1.MEMPORT -> SPM_B0_WRAPPER_INS.MEMPORT_P1
    pe_bank = {}
    for key, targets in (arch.get("CGRA", {}).get("CONNECTIONS") or {}).items():
        if not key.startswith("PE_MEM_"):
            continue
        m = re.match(r"PE_MEM_X(\d+)_Y(\d+)", key)
        if not m:
            continue
        for t in targets:
            bm = re.match(r"SPM_B(\d+)_", t)
            if bm:
                pe_bank[(int(m.group(1)), int(m.group(2)))] = int(bm.group(1))

    # variable -> bank, from each wrapper's DATA_LAYOUT
    var_bank = {}
    for name, mod in arch.items():
        bm = re.match(r"SPM_B(\d+)_WRAPPER$", name)
        if not bm:
            continue
        bank = int(bm.group(1))
        layout = mod.get("DATA_LAYOUT") or {}
        if not layout:
            for sub in (mod.get("SUBMODS") or {}).get("SPM", []):
                layout = sub.get("DATA_LAYOUT") or layout
        for var in layout:
            var_bank[var] = bank

    return {
        "xdim": xdim,
        "ydim": ydim,
        "kind": kind,
        "alu": alu,
        "mem": mem,
        "pe_bank": pe_bank,
        "var_bank": var_bank,
    }


def rec_min_ii(nodes, arch):
    """RecMinII: the tightest II any schedule could reach.

    A recurrence cycle with total latency `delay` and `dist` loop-carried
    iterations needs II >= ceil(delay / dist); a cycle with no loop-carried
    edge (dist == 0) needs II >= delay, because every hop round the cycle costs
    a cycle inside the same iteration.  Enumerating simple cycles is fine here:
    the DFGs in this repo have at most ~25 edges.
    """
    lat = {}
    for n in nodes.values():
        table = arch["mem"] if is_mem_op(n.op) else arch["alu"]
        lat[n.idx] = table.get(n.op, 1)

    best = 1
    stack = []
    onstack = set()

    def dfs(u):
        nonlocal best
        stack.append(u)
        onstack.add(u)
        for (v, nextiter, _npb, _t) in nodes[u].outs:
            if v not in nodes:
                continue
            if v in onstack:
                i = stack.index(v)
                cyc = stack[i:]
                delay = sum(lat[x] for x in cyc)
                dist = 0
                for a, b in zip(cyc, cyc[1:] + cyc[:1]):
                    for (w, ni, _p, _ty) in nodes[a].outs:
                        if w == b:
                            dist += ni
                            break
                need = delay if dist == 0 else -(-delay // dist)
                if need > best:
                    best = need
            else:
                dfs(v)
        stack.pop()
        onstack.discard(u)

    for u in nodes:
        dfs(u)
    return best


def build_and_solve(nodes, arch, ii, max_hops, time_limit, objective,
                    route_cycles_max=1, timing="bypass", forbidden=None,
                    select_coloc="exactly"):
    """Solve placement + scheduling for one II.

    An edge u->v costs `L + d` cycles, where `L` is the operator latency and `d`
    is the number of cycles the route itself consumes.  `d` is 0 for a
    combinational transfer through the crossbars and 1 when the value has to
    pass a register.  `route_cycles_max` caps it; the default of 1 is what the
    mapper's own solutions use (measured over all 23 edges of array_add: the
    residual `(t_v - t_u - L) mod II` is always 0 or 1, regardless of distance).

    `timing` decides who owns the route latency:

    * ``"bypass"`` (HyCUBE, the default) -- a PE output reaches another PE
      through the crossbars without crossing a register, so ``d`` is free in
      ``{0, 1}`` and is *independent* of the hop count.  Measured on array_add:
      ``(hops, d)`` = ``{(0,0):7, (0,1):1, (1,0):5, (1,1):1, (2,0):4, (2,1):3,
      (3,0):2}`` -- three-hop edges do reach their consumer in the same cycle.
    * ``"register"`` -- the conventional CGRA where every PE output is
      registered once, so a route of ``h`` hops costs ``h`` cycles and a
      same-PE transfer still costs one: ``d == max(h, 1)``.
    """
    from ortools.sat.python import cp_model

    xdim, ydim = arch["xdim"], arch["ydim"]
    pes = [(x, y) for y in range(ydim) for x in range(xdim)]
    pe_index = {p: i for i, p in enumerate(pes)}
    n_pe = len(pes)

    # Admissible extra route delays, computed **per edge** because the bound
    # depends on the hop count:
    #   route_cycles_max < 0 (auto) -> d <= min(manhattan+1, II-1), plus the II-1
    #       wrap seen on multi-cycle memory ops (trmm).  This is the bound the
    #       module docstring always claimed; capping every edge at 1 made long
    #       edges (3..8 hops) infeasible on the bigger benchmarks.
    #   == 0 -> {0}   (combinational only)
    #   == 1 -> {0,1} (what the mapper's own solutions use on small kernels)
    #   >= 2 -> {0,1,II-1}
    #   timing == "register" -> exactly max(h,1)
    def delay_options(hh):
        # A zero-hop edge stays inside one PE.  HyCUBE has no combinational
        # path from FU0.DP0_T back to FU0.DP0_I1, so it must spend a cycle in
        # TREG; offering d == 0 there produced routes the mapper cannot build.
        if hh == 0 and timing != "register":
            opts = [d for d in _delay_options_raw(hh) if d != 0]
            return opts or [1]
        return _delay_options_raw(hh)

    def _delay_options_raw(hh):
        if timing == "register":
            return [max(hh, 1)]
        if route_cycles_max < 0:
            hi = min(hh + 1, ii - 1)
            d = list(range(0, hi + 1))
            if ii > 2 and (ii - 1) not in d:
                d.append(ii - 1)
            return d
        if route_cycles_max <= 0:
            return [0]
        if route_cycles_max == 1:
            return [0, 1]
        return sorted({0, 1, ii - 1})

    def supported(pe, op):
        kind = arch["kind"].get(pe, "PE")
        table = arch["mem"] if kind == "PE_MEM" else arch["alu"]
        return op in table

    def op_latency(pe, op):
        kind = arch["kind"].get(pe, "PE")
        table = arch["mem"] if kind == "PE_MEM" else arch["alu"]
        return table.get(op, 1)

    def hops(p, q):
        return abs(p[0] - q[0]) + abs(p[1] - q[1])

    def bank_ok(pe, node):
        if not is_mem_op(node.op):
            return True
        if arch["kind"].get(pe) != "PE_MEM":
            return False
        if not node.bp or not arch["var_bank"] or not arch["pe_bank"]:
            return True
        want = arch["var_bank"].get(node.bp)
        have = arch["pe_bank"].get(pe)
        if want is None or have is None:
            return True
        return want == have

    model = cp_model.CpModel()
    slots = [(p, t) for p in pes for t in range(ii)]
    slot_index = {s: i for i, s in enumerate(slots)}

    at = {}
    for n in nodes.values():
        for s in slots:
            v = model.NewBoolVar("a_%d_%d_%d_%d" % (n.idx, s[0][0], s[0][1], s[1]))
            at[(n.idx, s)] = v

    # placement legality is decided before solving, by fixing variables to 0
    for n in nodes.values():
        allowed = []
        for s in slots:
            if not supported(s[0], n.op) or not bank_ok(s[0], n):
                model.Add(at[(n.idx, s)] == 0)
            else:
                allowed.append(s)
        if not allowed:
            return None, "node %d (%s) has no legal PE" % (n.idx, n.op)
        model.Add(sum(at[(n.idx, s)] for s in allowed) == 1)

    for s in slots:
        model.Add(sum(at[(n.idx, s)] for n in nodes.values()) <= 1)

    # CEGIS: forbid placements that the routing model has already proven
    # unroutable.  Each entry is {node_idx: (x, y, t)} covering every node, and
    # the clause removes exactly that one point from the search space.
    for fb in forbidden or []:
        lits = []
        for n in nodes.values():
            if n.idx not in fb:
                continue
            x, y, t = fb[n.idx]
            if (x, y, t) in slot_index:
                lits.append(at[(n.idx, (x, y, t))].Not())
        if lits:
            model.AddBoolOr(lits)

    # channel placement into (pe, phase) integers
    pe_var, ph_var = {}, {}
    for n in nodes.values():
        pe_var[n.idx] = model.NewIntVar(0, n_pe - 1, "pe_%d" % n.idx)
        ph_var[n.idx] = model.NewIntVar(0, ii - 1, "ph_%d" % n.idx)
        model.Add(pe_var[n.idx] == sum(pe_index[s[0]] * at[(n.idx, s)] for s in slots))
        model.Add(ph_var[n.idx] == sum(s[1] * at[(n.idx, s)] for s in slots))

    # SELECTCOLOC: a SELECT reads two operands in one cycle, and the PE has one
    # local (ALU) path plus one network input register per direction.  If both
    # operands arrived through the network they would contend for the same
    # register, so the mapper always places exactly one data parent on the same
    # PE as the SELECT (measured on array_add/mac/fir/conv2/trmm: 6 SELECTs, all
    # of them with one local and one remote parent).  The predicate operand
    # (type "PS") is a separate channel and is excluded.
    #
    # This is a *placement* constraint, not a routing one: `d` (the route delay)
    # cannot distinguish "last hop inside the PE" from "last hop crossed a PE
    # boundary" -- mac's two SELECTs both use d in {0,1} with opposite layouts.
    rev_in = {}
    for _n in nodes.values():
        for (_v, _ni, _npb, _typ) in _n.outs:
            if _v in nodes:
                rev_in.setdefault(_v, []).append((_n.idx, _typ))
    n_selcoloc = 0
    if select_coloc != "off":
        for _n in nodes.values():
            if _n.op != "SELECT":
                continue
            _data = [(p, typ) for (p, typ) in rev_in.get(_n.idx, []) if typ != "PS"]
            if len(_data) != 2:
                continue
            _p1, _p2 = _data[0][0], _data[1][0]
            if _p1 == _p2:
                continue
            _co = []
            for _p in (_p1, _p2):
                _b = model.NewBoolVar("co_%d_%d" % (_n.idx, _p))
                model.Add(pe_var[_p] == pe_var[_n.idx]).OnlyEnforceIf(_b)
                model.Add(pe_var[_p] != pe_var[_n.idx]).OnlyEnforceIf(_b.Not())
                _co.append(_b)
            if select_coloc == "exactly":
                model.Add(_co[0] + _co[1] == 1)
            elif select_coloc == "atmost":
                model.Add(_co[0] + _co[1] <= 1)
            elif select_coloc == "atleast":
                model.Add(_co[0] + _co[1] >= 1)
            else:
                sys.exit("unknown select_coloc: %s" % select_coloc)
            n_selcoloc += 1

    hop_vars = []
    delay_vars = []
    for n in nodes.values():
        for (v, nextiter, _npb, _t) in n.outs:
            if v not in nodes:
                continue
            child = nodes[v]
            h = model.NewIntVar(0, max_hops, "h_%d_%d" % (n.idx, v))
            hop_vars.append(h)
            # `d` is how many cycles the route itself consumes: a purely
            # combinational transfer costs 0, a transfer through a register
            # costs 1.  `route_cycles_max` == 1 restricts it to {0, 1}, which is
            # what the mapper's own solutions use; a value of 2 additionally
            # admits the `-1` wrap (II-1) that multi-cycle memory ops show on
            # trmm, where the consumer starts one phase before the producer's
            # declared latency.
            d = model.NewIntVar(0, ii - 1, "d_%d_%d" % (n.idx, v))
            delay_vars.append(d)
            tuples = []
            for pu in pes:
                if not supported(pu, n.op) or not bank_ok(pu, n):
                    continue
                for pv in pes:
                    if not supported(pv, child.op) or not bank_ok(pv, child):
                        continue
                    # The edge latency is the *source* operator's latency: the
                    # mapper advances the phase by the producer's latency
                    # (`int latency = parentFU->supportedOPs[node->op]` in
                    # PathFinderMapper::estimateRouting, where `dest` is the
                    # producer's datapath).  Measured against the mapper's own
                    # solutions this reading is exact on 23/23 array_add and
                    # 28/28 mac edges; the destination-latency reading misses.
                    lat = op_latency(pu, n.op)
                    hh = hops(pu, pv)
                    if hh > max_hops:
                        continue
                    for tu in range(ii):
                        # dd = extra cycles the route consumes: 0 for a purely
                        # combinational path, 1 when a register is crossed.
                        for dd in delay_options(hh):
                            # register-timed fabric: the route latency is
                            # fully determined by the hop count.
                            if timing == "register" and dd != max(hh, 1):
                                continue
                            tv = (tu + lat + dd + nextiter * ii) % ii
                            tuples.append(
                                (pe_index[pu], tu, pe_index[pv], tv, hh, dd)
                            )
            if not tuples:
                return None, "edge %d->%d has no feasible (pe,phase) pair" % (n.idx, v)
            model.AddAllowedAssignments(
                [pe_var[n.idx], ph_var[n.idx], pe_var[v], ph_var[v], h, d],
                tuples,
            )

    # Wire pressure first (hops), then route cycles -- both are proxies for how
    # hard this placement will be for the real router.
    terms = [10 * h for h in hop_vars] + [d for d in delay_vars]
    if objective == "hops" and terms:
        model.Minimize(sum(terms))
    elif objective == "balance":
        pass  # pure feasibility

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    # CDETERM: reproducible runs (see tools/patches/patch_deterministic.py)
    solver.parameters.num_search_workers = int(os.environ.get("CP_SAT_THREADS", "8"))
    solver.parameters.random_seed = int(os.environ.get("CP_SAT_SEED", "0"))
    status = solver.Solve(model)

    name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, "solver returned %s" % name

    pins = {}
    for n in nodes.values():
        pe = pes[solver.Value(pe_var[n.idx])]
        pins[n.idx] = (pe[0], pe[1], solver.Value(ph_var[n.idx]))
    total_hops = sum(solver.Value(h) for h in hop_vars)
    total_delay = sum(solver.Value(d) for d in delay_vars)
    return pins, "%s %-9s hops=%-3d route_cycles=%-3d selcoloc=%d wall=%.2fs" % (
        timing,
        name,
        total_hops,
        total_delay,
        n_selcoloc,
        solver.WallTime(),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dfg", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--ii", type=int, default=0, help="0 = start from RecMinII")
    ap.add_argument("--max-ii", type=int, default=32)
    ap.add_argument("--hops", type=int, default=4)
    ap.add_argument("--route-cycles", type=int, default=-1,
                    help="max cycles the route itself may consume; -1 (default) = "
                         "auto, d <= min(manhattan+1, II-1); 0 = combinational only")
    ap.add_argument("--time-limit", type=float, default=60.0)
    ap.add_argument("--objective", choices=("hops", "balance"), default="hops")
    ap.add_argument("--timing", choices=("bypass", "register"), default="bypass",
                    help="bypass = combinational PE output (HyCUBE); "
                         "register = one register per hop (conventional CGRA)")
    ap.add_argument("--select-coloc",
                    choices=("exactly", "atmost", "atleast", "off"),
                    default="exactly",
                    help="SELECT operand co-location: 'exactly' (default) forces "
                         "one data parent onto the SELECT's own PE, which is what "
                         "the HyCUBE PE can feed through its local ALU path")
    ap.add_argument("--spatial", action="store_true",
                    help="write t=-1 so the mapper keeps phase ownership")
    ap.add_argument("-o", "--out", default="pins.txt")
    args = ap.parse_args(argv)

    nodes = parse_dfg(args.dfg)
    arch = parse_arch(args.arch)
    rmin = rec_min_ii(nodes, arch)
    start = args.ii if args.ii > 0 else rmin
    print("dfg: %d node(s), RecMinII = %d, start II = %d" % (len(nodes), rmin, start))

    for ii in range(start, args.max_ii + 1):
        pins, info = build_and_solve(nodes, arch, ii, args.hops,
                                     args.time_limit, args.objective,
                                     args.route_cycles, args.timing,
                                     select_coloc=args.select_coloc)
        if pins is None:
            print("II = %-2d  %s" % (ii, info))
            continue
        print("II = %-2d  %s" % (ii, info))
        lines = ["# node x y t   (t<0 = spatial only)"]
        for idx in sorted(pins):
            x, y, t = pins[idx]
            lines.append("%d %d %d %d" % (idx, x, y, -1 if args.spatial else t))
        with open(args.out, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        print("wrote %d pin(s) to %s" % (len(pins), args.out))
        return 0

    print("no feasible placement up to II = %d" % args.max_ii)
    return 1


if __name__ == "__main__":
    sys.exit(main())
