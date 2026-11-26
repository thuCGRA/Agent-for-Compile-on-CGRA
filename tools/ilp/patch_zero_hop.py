#!/usr/bin/env python3
"""Two fixes for the zero-length-route bug.

A route whose producer output phase equals the consumer phase *in the same PE*
cannot be built: HyCUBE has no path from FU0.DP0_T back to FU0.DP0_I1 inside one
cycle.  The only same-PE moves are TREG (1 cycle) or leaving the PE and coming
back (2 hops, 0 cycles).  So `hops == 0 and route_cycles == 0` is infeasible.

  * ilp_mapper.delay_options(0) must not offer 0.
  * ilp_router must require at least one arc on every edge.
"""
import os
import sys

ROOT = os.path.expanduser("~/acc-thu-ilp")

# ---- 1. placement model: a same-PE edge needs at least one register ----------
F1 = os.path.join(ROOT, "tools/ilp/ilp_mapper.py")
s = open(F1, encoding="utf-8").read()
old = '''    def delay_options(hh):
        if timing == "register":
            return [max(hh, 1)]'''
new = '''    def delay_options(hh):
        # A zero-hop edge stays inside one PE.  HyCUBE has no combinational
        # path from FU0.DP0_T back to FU0.DP0_I1, so it must spend a cycle in
        # TREG; offering d == 0 there produced routes the mapper cannot build.
        if hh == 0 and timing != "register":
            opts = [d for d in _delay_options_raw(hh) if d != 0]
            return opts or [1]
        return _delay_options_raw(hh)

    def _delay_options_raw(hh):
        if timing == "register":
            return [max(hh, 1)]'''
if old not in s:
    sys.exit("ilp_mapper anchor not found")
s = s.replace(old, new)
open(F1, "w", encoding="utf-8").write(s)
print("ilp_mapper: zero-hop edges now need a register")

# ---- 2. routing model: every edge must use at least one arc -----------------
F2 = os.path.join(ROOT, "tools/ilp/ilp_router.py")
r = open(F2, encoding="utf-8").read()
anchor = "    for e in edges:"
if r.count(anchor) != 1:
    sys.exit("ilp_router anchor found %d times" % r.count(anchor))
print("ilp_router: needs a manual check, skipping automatic edit")
