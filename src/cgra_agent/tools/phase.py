"""Modulo-time assignment consistent with the mapper's own latency model.

The mapper keeps an *absolute* latency on every port and asserts
    arrival % t_max == destination PE's T
(CGRA_Mapper src/mapper/PathFinderMapper.cpp, "assert((minLatDestVal) % ... == ...T)").
A producer at (x,y,T) with op latency lat emits at T+lat; each register hop the
value crosses adds one cycle.  So the only constraint between two placed nodes is

    (T_child - T_parent - lat_parent - hops) % II == 0

with `hops` free (the router picks the path).  It is an equality modulo II, not
an inequality: a phase is *not* illegal merely because it is "too early" --
the router can always pad the difference with whole laps of registers.

We therefore assign phases by walking the DFG and setting
    T_child = (T_parent + lat_parent + hops0) % II
where hops0 is the Manhattan distance between the two PEs (the shortest path the
router will take).  The router may still fail to realise a path of that exact
length, but that is a routing question, not a scheduling one.
"""
from __future__ import annotations

from collections import deque


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def assign_phases(facts, ii, spatial, seed=0):
    """Return {node: {"x","y","t"}} or raise ValueError with the blocking nodes."""
    nodes = {n["idx"]: n for n in facts["nodes"]}
    missing = [i for i in nodes if i not in spatial]
    if missing:
        raise ValueError("spatial placement missing nodes: %s" % missing[:8])

    order, indeg, children = [], {}, {i: [] for i in nodes}
    for i, n in nodes.items():
        parents = [p[0] for p in (n.get("parents") or []) if p[0] in nodes]
        indeg[i] = len(parents)
        for p in parents:
            children[p].append(i)
    q = deque(sorted(i for i in nodes if indeg[i] == 0))
    while q:
        i = q.popleft()
        order.append(i)
        for c in children[i]:
            indeg[c] -= 1
            if indeg[c] == 0:
                q.append(c)
    # loop-carried back edges leave a residual cycle: append the rest by index
    order += [i for i in sorted(nodes) if i not in set(order)]

    t, used = {}, {}
    for i in order:
        n = nodes[i]
        base = None
        for par in (n.get("parents") or []):
            p, ni = par[0], (par[2] if len(par) > 2 else 0)
            if p not in t:
                continue
            lat = nodes[p].get("lat") or 1
            hops = _manhattan(spatial[p], spatial[i])
            cand = (t[p] + lat + hops + ni * ii) % ii
            base = cand if base is None else base
        if base is None:
            base = (spatial[i][0] + spatial[i][1] + seed) % ii
        # a DP slot may host one node per phase column
        while (spatial[i], base) in used:
            base = (base + 1) % ii
        t[i] = base
        used[(spatial[i], base)] = i

    return {i: {"x": spatial[i][0], "y": spatial[i][1], "t": t[i]} for i in nodes}
