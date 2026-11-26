#!/usr/bin/env python3
"""Every DFG edge must consume at least one arc.

With the original `>= 0` bounds a zero-length route was legal whenever the
producer output node and the consumer operand node were the same (PE, phase)
node in the resource graph.  Such a "route" has no port chain to replay, and
HyCUBE has no combinational path from FU0.DP0_T back to FU0.DP0_I1 anyway, so
the mapper cannot build it.  Requiring one arc turns the degenerate case into a
real detour (leave the PE and come back, or park in TREG for a cycle).
"""
import os
import sys

F = os.path.expanduser("~/acc-thu-ilp/tools/ilp/ilp_router.py")
s = open(F, encoding="utf-8").read()
old = """        model.Add(sum(b_arcs) >= 0)
        model.Add(sum(r_arcs) >= 0)"""
new = """        # At least one arc: a route with no arcs is not a route.
        model.Add(sum(b_arcs) + sum(r_arcs) >= 1)"""
if old not in s:
    sys.exit("router anchor not found")
s = s.replace(old, new)
open(F, "w", encoding="utf-8").write(s)
print("ilp_router: every edge needs at least one arc")
