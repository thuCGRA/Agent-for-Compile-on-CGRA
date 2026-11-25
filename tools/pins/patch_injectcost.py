#!/usr/bin/env python3
"""Give calculateCost() a clean, always-positive cost in inject mode.

Why: the stock cost blends register delay with a *port-occupancy* penalty
`(outputPorts*2 - freePorts) * UOP`. That penalty assumes incremental placement,
where most ports are still free. In inject mode (--pins_mode 6) every node is
placed before routing starts, so freePorts collapses to 0 and the term (plus the
congestion delta) drives the cost far negative -- observed `distance=-66665`.
Clamping it to 1 makes every hop cost the same, so A* degenerates into BFS and
starts colliding on ports, which corrupts operand values (array_add 223/20,
mac 165/2, conv2 153/10).

In inject mode the placement is fixed, so the honest ordering is:
    fewest registers  >  fewest PE hops  >  avoid already-used ports.
All terms are non-negative, so no clamping is needed.

Idempotent.
"""
import io
import os
import sys

CPP = os.path.expanduser("~/acc-thu-ilp/src/mapper/src/mapper/PathFinderMapper.cpp")
MARK = "// INJECTCOST:"

PROBE = "\tint distance = regDiscourageFactor * ((nextPE->T - srcPE->T + cgra->get_t_max()) % cgra->get_t_max());\n"

NEW = """	// INJECTCOST: inject mode has every node pre-placed, so the port-occupancy
	// penalty below is meaningless (freePorts == 0 for all PEs) and can go
	// negative enough to make A* degenerate into BFS. Use a clean non-negative
	// ordering instead: registers, then PE hops, then unused-port preference.
	if (pinsMode == 6)
	{
		int delayCycles = (nextPE->T - srcPE->T + cgra->get_t_max()) % cgra->get_t_max();
		int peHop = (srcPE != nextPE) ? 1 : 0;
		int portUsed = (next_to_src.second->getNode() != NULL) ? 1 : 0;
		// Reusing an occupied port makes the last writer win and corrupts the other
		// operand, so it must dominate every other term (delayCycles <= II-1).
		int injCost = delayCycles * 100 + peHop * 10 + portUsed * 100000;
		if (injCost <= 0)
			injCost = 1;
		return injCost;
	}

""" + PROBE


def main():
    c = io.open(CPP, encoding="utf-8").read()
    if MARK in c:
        print("already patched")
        return
    if PROBE not in c:
        print("ERROR: cost probe not found", file=sys.stderr)
        sys.exit(1)
    c = c.replace(PROBE, NEW, 1)
    io.open(CPP, "w", encoding="utf-8").write(c)
    print("patched inject cost")


if __name__ == "__main__":
    main()
