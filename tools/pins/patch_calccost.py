#!/usr/bin/env python3
"""Make calculateCost() tolerate a non-positive cost.

`assert(distance > 0)` is a sanity check on a *heuristic* cost, not on mapping
correctness.  In inject mode (--pins_mode 6) every node is placed before routing
starts, which drives the port-occupancy term negative -- something the normal
greedy flow never produces.  Clamping keeps the A* ordering sane instead of
aborting; it prints once per occurrence so the cause stays visible.

Idempotent.
"""
import io
import os
import sys

CPP = os.path.expanduser("~/acc-thu-ilp/src/mapper/src/mapper/PathFinderMapper.cpp")
MARK = "// CALCCOST:"

NEW = """	// CALCCOST: a non-positive heuristic cost is possible in inject mode
	if (distance <= 0)
	{
		std::cout << "CALCCOST: distance=" << distance << " at " << __LINE__ << "\\n";
		distance = 1;
	}
"""


def main():
    c = io.open(CPP, encoding="utf-8").read()
    if MARK in c:
        print("already patched")
        return
    n = c.count("\tassert(distance > 0);\n")
    if n != 3:
        print("ERROR: expected 3 asserts, found %d" % n, file=sys.stderr)
        sys.exit(1)
    c = c.replace("\tassert(distance > 0);\n", NEW)
    io.open(CPP, "w", encoding="utf-8").write(c)
    print("patched calculateCost (%d sites)" % n)


if __name__ == "__main__":
    main()
