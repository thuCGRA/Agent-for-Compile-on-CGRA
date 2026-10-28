"""Route-level static check: replay the mapper's own route to compute when each
operand actually lands in a PE, then look for the one-operand-per-cycle rule.

The placement-level validator cannot see this: two parents with different
phases can still arrive in the same cycle once hop counts are added in.

Usage: python scripts/verify_routes.py [kernel ...]
Reads the newest ``*routeInfo*.log`` under work/verify2/<kernel>/.
"""
import glob
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NODE_RE = re.compile(r"^node=(\d+),mapped=PE(.*?)-T(\d+)$")
PARENT_RE = re.compile(r"^\tparent:(\d+)$")
# the final config entry of a parent block names the destination operand slot
ARRIVE_RE = re.compile(r"CGRA_Ins\.PE(.*?)-T(\d+)\.FU0\.DP0\.(I1|I2|P)$")


def parse(log_path):
    nodes = {}          # node -> (pe, t)
    arrivals = []       # (node, parent, pe, cycle, slot)
    cur = None
    par = None
    for line in open(log_path, errors="replace"):
        line = line.rstrip("\n")
        m = NODE_RE.match(line)
        if m:
            cur = int(m.group(1))
            nodes[cur] = (m.group(2).replace("|", "").replace("_", ""), int(m.group(3)))
            par = None
            continue
        m = PARENT_RE.match(line)
        if m:
            par = int(m.group(1))
            continue
        m = ARRIVE_RE.search(line)
        if m and cur is not None and par is not None:
            arrivals.append((cur, par, m.group(1).replace("|", "").replace("_", ""),
                             int(m.group(2)), m.group(3)))
    return nodes, arrivals


OPS = {}


def load_ops(kernel):
    OPS.clear()
    for p in glob.glob(os.path.join(REPO, "work", "verify2", kernel, "*", "dfg", "*PartPredDFG.xml")):
        txt = open(p, errors="replace").read()
        for m in re.finditer(r'<Node idx="(\d+)".*?<OP>([^<]*)</OP>', txt, re.S):
            OPS[int(m.group(1))] = m.group(2)
        break


def check(kernel, log_path):
    nodes, arrivals = parse(log_path)
    out = []
    # 1) same PE, same cycle, same operand slot: the input register can hold one
    slot_use = {}
    for node, parent, pe, cyc, slot in arrivals:
        slot_use.setdefault((pe, cyc, slot), []).append((node, parent))
    for (pe, cyc, slot), users in sorted(slot_use.items()):
        if len(users) > 1:
            out.append(("slot-conflict", pe, cyc, slot, users))
    # 2) only mux-style ops (SELECT/CMERGE) use P to choose between I1 and I2,
    #    so only they are illegal when both arrive together.  The simulator
    #    asserts at CGRATile.cpp:175 for exactly this case.
    per_node = {}
    for node, parent, pe, cyc, slot in arrivals:
        per_node.setdefault(node, {}).setdefault(cyc, []).append(slot)
    for node, by_cycle in per_node.items():
        if OPS.get(node) not in ("SELECT", "CMERGE"):
            continue
        for cyc, slots in by_cycle.items():
            if len(slots) > 1:
                out.append(("operands-same-cycle", nodes[node][0], cyc, node, sorted(slots)))
    return nodes, arrivals, out


def main(argv):
    kernels = argv[1:] or sorted(os.path.basename(p) for p in glob.glob(os.path.join(REPO, "work/verify2/*")))
    for k in kernels:
        logs = sorted(glob.glob(os.path.join(REPO, "work/verify2", k, "*", "dfg", "*routeInfo*.log")))
        if not logs:
            print("%-11s no routeInfo" % k)
            continue
        log = logs[-1]
        load_ops(k)
        nodes, arrivals, viol = check(k, log)
        print("%-11s nodes=%-3d arrivals=%-4d violations=%d  %s"
              % (k, len(nodes), len(arrivals), len(viol),
                 "" if not viol else str(viol[0])[:110]))


if __name__ == "__main__":
    main(sys.argv)
