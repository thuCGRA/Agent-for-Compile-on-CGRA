"""Route a fixed placement on the port-level MRRG.

This is the Plan-B router.  Unlike ``ilp_router.py`` -- which modelled HyCUBE
with a hand-written set of 0/1 arcs and therefore disagreed with the mapper
about which routes exist -- this one solves a multi-commodity flow directly on
the graph that ``mrrg.py`` expands from the ADL.  A route found here is a route
the mapper can build, because it is the same set of ports.

Model
-----
One unit of flow per DFG edge, from the producer's output port at its output
cycle to the consumer's operand port at its scheduled cycle::

    src = (x_u, y_u, (t_u + lat_u) % II, "FU0.DP0_T")
    dst = (x_v, y_v,  t_v,               "FU0.DP0_<operand>")

* ``f[e][arc]`` is 1 when edge ``e`` uses that arc.
* ``y[producer][node]`` is 1 when that producer's value is readable at that
  port in that cycle.  Capacity is counted per *producer*, not per edge, which
  is what lets one value feed several consumers through the same port -- the
  mapper broadcasts, and the baseline dumps show it.
* Every resource node admits at most one producer.

Cycles are not a separate variable: an arc into a register's write side costs
one cycle, so a path from ``src`` to ``dst`` automatically spends
``(t_v - t_u - lat_u) mod II`` of them.

usage: mrrg_router.py --dfg X.xml --arch arch.json --pins pins.txt --ii 4
"""

import argparse
import json
import sys
import time

import mrrg

try:
    from ortools.sat.python import cp_model
except ImportError:  # pragma: no cover
    cp_model = None


class RouterError(Exception):
    pass


# ---------------------------------------------------------------- DFG reading

import re

NODE_RE = re.compile(r"<Node\s+([^>]*)>", re.S)
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
OP_RE = re.compile(r"<OP>([^<]*)</OP>")
OUT_RE = re.compile(r"<Output\s+([^/>]*)/?>")
IN_RE = re.compile(r"<Input\s+([^/>]*)/?>")


class Node(object):
    def __init__(self, idx, op):
        self.idx = idx
        self.op = op
        self.outs = []  # (child_idx, nextiter, npb, operand_type)

    def __repr__(self):
        return "Node(%s,%s)" % (self.idx, self.op)


def parse_dfg(path):
    """Return ``{idx: Node}``.  The XML is malformed on purpose (attributes run
    together: ``ALAP="0"BB=""``), so it is scanned with regexes."""
    with open(path) as fh:
        text = fh.read()
    nodes = {}
    for m in NODE_RE.finditer(text):
        attrs = dict(ATTR_RE.findall(m.group(1)))
        if "idx" not in attrs:
            continue
        idx = int(attrs["idx"])
        tail = text[m.end():m.end() + 400]
        opm = OP_RE.search(tail)
        nodes[idx] = Node(idx, opm.group(1).strip() if opm else "")
        nodes[idx]._tail = tail
    for idx, n in nodes.items():
        tail = n._tail
        stop = tail.find("</Node>")
        if stop >= 0:
            tail = tail[:stop]
        for om in OUT_RE.finditer(tail):
            a = dict(ATTR_RE.findall(om.group(1)))
            if "idx" not in a:
                continue
            n.outs.append((
                int(a["idx"]),
                int(a.get("nextiter", 0) or 0),
                int(a.get("NPB", 0) or 0),
                a.get("type", "I1") or "I1",
            ))
        del n._tail
    return nodes


def load_pins(path):
    pins = {}
    with open(path) as fh:
        for line in fh:
            line = line.split("#")[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            pins[int(parts[0])] = (int(parts[1]), int(parts[2]), int(parts[3]))
    return pins


def lat_of(arch, op, is_mem_pe):
    ops = arch["FU_MEM"]["OPS"] if is_mem_pe else arch["FU"]["OPS"]
    if op in ops:
        return int(ops[op])
    for table in (arch["FU"]["OPS"], arch["FU_MEM"]["OPS"]):
        if op in table:
            return int(table[op])
    return 1


# ---------------------------------------------------------------- the model

def build_specs(nodes, arch, pins, ii):
    """One flow unit per DFG edge, with its operand-carrying sink."""
    specs = []
    for n in nodes.values():
        if n.idx not in pins:
            continue
        px, py, pt = pins[n.idx]
        mem = n.op.upper().find("LOAD") >= 0 or n.op.upper().find("STORE") >= 0
        src_t = (pt + lat_of(arch, n.op, mem)) % ii
        for (vid, nextiter, _npb, typ) in n.outs:
            if vid not in nodes or vid not in pins:
                continue
            qx, qy, qt = pins[vid]
            dst_t = (qt + nextiter * ii) % ii
            specs.append({
                "src": n.idx,
                "dst": vid,
                "src_port": (px, py, src_t),
                "dst_port": (qx, qy, dst_t),
                "operand": typ if typ in ("I1", "I2", "P") else "I1",
                "min_hops": abs(px - qx) + abs(py - qy),
            })
    return specs


def _arcs_within(arcs, lo_x, hi_x, lo_y, hi_y):
    """Keep the arcs whose endpoints sit inside the given PE window."""
    out = {}
    for (u, v), cost in arcs.items():
        if lo_x <= u[0] <= hi_x and lo_x <= v[0] <= hi_x and \
           lo_y <= u[1] <= hi_y and lo_y <= v[1] <= hi_y:
            out[(u, v)] = cost
    return out


def solve(dfg, arch_path, pins, ii, slack=1, time_limit=60, max_regs=None,
          objective="hops"):
    if cp_model is None:
        raise RouterError("ortools is not installed")
    nodes_raw = parse_dfg(dfg)
    with open(arch_path) as fh:
        arch = json.load(fh)
    arch = arch.get("ARCH", arch)

    all_nodes, all_arcs, meta = mrrg.build(arch, ii)
    src_name = meta["source"]
    specs = build_specs(nodes_raw, arch, pins, ii)
    if not specs:
        raise RouterError("no edge to route (empty placement?)")

    model = cp_model.CpModel()

    # per-edge candidate arcs, restricted to the bounding box + slack
    edge_arcs = []
    for sp in specs:
        sx, sy, _ = sp["src_port"]
        dx, dy, _ = sp["dst_port"]
        lo_x, hi_x = min(sx, dx) - slack, max(sx, dx) + slack
        lo_y, hi_y = min(sy, dy) - slack, max(sy, dy) + slack
        sub = _arcs_within(all_arcs, lo_x, hi_x, lo_y, hi_y)
        edge_arcs.append(sub)

    # flow variables
    flow = []
    for ei, sp in enumerate(specs):
        f = {}
        for (u, v) in edge_arcs[ei]:
            f[(u, v)] = model.NewBoolVar("f_%d_%d_%d_%d_%d_%d" % (
                ei, u[0], u[1], u[2], hash(u[3]) % 1000, hash(v[3]) % 1000))
        flow.append(f)

    # y[producer][node]: is this producer readable at that port/cycle
    producer_of = [sp["src"] for sp in specs]
    y = {}
    for ei, sp in enumerate(specs):
        p = producer_of[ei]
        for (u, v) in edge_arcs[ei]:
            for nd in (u, v):
                key = (p, nd)
                if key not in y:
                    y[key] = model.NewBoolVar("y_%d_%d_%d_%d_%s" % (
                        p, nd[0], nd[1], nd[2], str(abs(hash(nd[3])) % 10000)))
                model.Add(flow[ei][(u, v)] <= y[key])

    # capacity: at most one producer per resource node
    users = {}
    for (p, nd), var in y.items():
        users.setdefault(nd, []).append(var)
    for nd, vs in users.items():
        if len(vs) > 1:
            model.Add(sum(vs) <= 1)

    # flow conservation
    for ei, sp in enumerate(specs):
        sx, sy, st = sp["src_port"]
        dx, dy, dt = sp["dst_port"]
        src_node = (sx, sy, st, src_name)
        dst_node = (dx, dy, dt, meta["sinks"][sp["operand"]])
        out_at = {}
        in_at = {}
        for (u, v) in edge_arcs[ei]:
            out_at.setdefault(u, []).append(flow[ei][(u, v)])
            in_at.setdefault(v, []).append(flow[ei][(u, v)])
        seen = set(out_at) | set(in_at) | {src_node, dst_node}
        for nd in seen:
            expr = sum(out_at.get(nd, [])) - sum(in_at.get(nd, []))
            if nd == src_node:
                model.Add(expr == 1)
            elif nd == dst_node:
                model.Add(expr == -1)
            else:
                model.Add(expr == 0)

    # objective: fewest register steps (each costs a cycle), then fewest arcs
    reg_terms = []
    for ei, sp in enumerate(specs):
        for (u, v), var in flow[ei].items():
            if edge_arcs[ei][(u, v)]:
                reg_terms.append(var)
    model.Minimize(sum(reg_terms) if reg_terms else 0)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    t0 = time.time()
    status = solver.Solve(model)
    wall = time.time() - t0

    names = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.UNKNOWN: "UNKNOWN",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
    }
    result = {
        "status": names.get(status, str(status)),
        "wall": round(wall, 2),
        "edges": len(specs),
        "routes": [],
    }
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return result

    for ei, sp in enumerate(specs):
        adj = {}
        for (u, v), var in flow[ei].items():
            if solver.Value(var):
                adj.setdefault(u, []).append(v)
        start = (sp["src_port"][0], sp["src_port"][1], sp["src_port"][2], src_name)
        path = [start]
        cur = start
        guard = 0
        while cur in adj and guard < 4 * ii * 16:
            nxt = adj[cur][0]
            path.append(nxt)
            cur = nxt
            guard += 1
        result["routes"].append({
            "src": sp["src"],
            "dst": sp["dst"],
            "operand": sp["operand"],
            "path": [[p[0], p[1], p[2], p[3]] for p in path],
            "regs": sum(1 for a, b in zip(path, path[1:])
                        if all_arcs.get((a, b)) == 1),
        })
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dfg", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--pins", required=True)
    ap.add_argument("--ii", type=int, required=True)
    ap.add_argument("--slack", type=int, default=1)
    ap.add_argument("--time-limit", type=float, default=60.0)
    ap.add_argument("-o", "--out", default="")
    args = ap.parse_args(argv)

    pins = load_pins(args.pins)
    res = solve(args.dfg, args.arch, pins, args.ii, slack=args.slack,
                time_limit=args.time_limit)
    print("MRRG router: %s edges=%d wall=%ss" % (res["status"], res["edges"], res["wall"]))
    if res["routes"]:
        tot = sum(r["regs"] for r in res["routes"])
        print("  routed %d edge(s), %d register step(s)" % (len(res["routes"]), tot))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(res, fh, indent=1)
        print("  wrote", args.out)
    return 0 if res["routes"] else 1


if __name__ == "__main__":
    sys.exit(main())
