#!/usr/bin/env python3
"""Make `estimateRouting` offer *all* phases when an external placement is loaded.

The problem
-----------
`PathFinderMapper::estimateRouting` builds one candidate per (PE, phase) -- in
HyCUBE each `(x, y, t)` is a separate `PE` object, so a 4x4/II=4 array yields 63
usable candidates -- and then throws almost all of them away:

    while (!estimatedRoutesTemp.empty())
    {
        dest_with_cost top = estimatedRoutesTemp.top();
        estimatedRoutesTemp.pop();
        if (minLatDests[top.dest] == minLatSucc || !changed)
            estimatedRoutes.push(top);
    }

`minLatSucc` is the smallest arrival latency over every candidate, so the queue
that reaches `applyPins` keeps only the destinations that sit at the *earliest*
arrival phase (11 of the 63 in the array_add trace).  Every later-phase
destination is discarded before routing is ever attempted.

That is why an externally computed placement is rejected with

    PIN: node 1 (ADD) pin (1,0,2) not among 11 candidate(s); fail

even though `(1, 0, t=2)` was one of the 63 candidates the estimator had already
proven routable from its parents.  The pin is not infeasible -- it is invisible.
It also explains why the `--routes` corridor hook cannot help: the rejection
happens here, upstream of `LeastCostPathAstar`.

The fix
-------
When a pin file is loaded (`pinsMode != 0`), keep every candidate and let
`applyPins` do the filtering.  Baseline behaviour (`pinsMode == 0`) is untouched,
so plain `-m 0` results stay bit-identical.

`Route()` also asserts that a candidate destination is unmapped.  With 63
candidates that assertion is reachable in a Debug build, so it becomes a `skip`
under pins mode.
"""
import io
import sys

CPP = "src/mapper/src/mapper/PathFinderMapper.cpp"

PRUNE_OLD = """	while (!estimatedRoutesTemp.empty())
	{
		dest_with_cost top = estimatedRoutesTemp.top();
		estimatedRoutesTemp.pop();
		if (minLatDests[top.dest] == minLatSucc || !changed)
			estimatedRoutes.push(top);
	}
"""

PRUNE_NEW = """	while (!estimatedRoutesTemp.empty())
	{
		dest_with_cost top = estimatedRoutesTemp.top();
		estimatedRoutesTemp.pop();
		// ALLPHASE: with an external placement every phase must stay reachable.
		// The minLatSucc filter below keeps only the earliest arrival phase, which
		// silently hides later-phase destinations from applyPins().
		if (pinsMode != 0 || minLatDests[top.dest] == minLatSucc || !changed)
			estimatedRoutes.push(top);
	}
"""

ASSERT_OLD = """		assert(currDest.dest->getMappedNode() == NULL);
"""
ASSERT_NEW = """		// ALLPHASE: with 63 candidates a destination may already be taken; skip it
		// instead of asserting (Debug builds used to abort here).
		if (pinsMode != 0 && currDest.dest->getMappedNode() != NULL)
			continue;
		assert(currDest.dest->getMappedNode() == NULL);
"""


def main():
    src = io.open(CPP, encoding="utf-8").read()
    if "ALLPHASE" in src:
        print("already patched")
        return 0
    for old, new, name in ((PRUNE_OLD, PRUNE_NEW, "prune"), (ASSERT_OLD, ASSERT_NEW, "assert")):
        if src.count(old) != 1:
            print("ERROR: %s anchor found %d times" % (name, src.count(old)), file=sys.stderr)
            return 1
        src = src.replace(old, new)
    io.open(CPP, "w", encoding="utf-8").write(src)
    print("patched", CPP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
