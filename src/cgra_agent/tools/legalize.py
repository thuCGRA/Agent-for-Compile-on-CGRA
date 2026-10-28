"""Give the LLM's spatial placement a legal schedule.

The failure we kept hitting is not spatial: the mapper accepts every pin, emits a
bitstream, and the simulator aborts because a SELECT receives both operands in
the same cycle.  Asking the model to get modulo-time right has now failed three
times (free-form, feedback, targeted repair), so we stop asking: the model picks
where a node lives, this module decides when it fires.

Two hard constraints, both taken from the simulator rather than invented here:

  * dependency:  (t_child - t_parent - lat_parent) mod II >= 0
  * select:      the two data parents of a SELECT/CMERGE must not share a phase

The schedule only ever pushes nodes *later*, so it terminates: a monotone
schedule over a DAG with a bounded phase domain either settles or proves there
is no legal assignment for this spatial placement.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

SELECT_OPS = ("SELECT", "CMERGE")


def _slack(tc: int, tp: int, ni: int, latp: int, ii: int) -> int:
    """Mapper's dependency rule: (tc - tp - ni*II) mod II >= lat_p."""
    return (tc - tp - ni * ii) % ii - latp


def _pid(p) -> int:
    """DFG edges are stored as [idx, port, nextiter] by facts.py."""
    if isinstance(p, (list, tuple)):
        return int(p[0])
    if isinstance(p, dict):
        return int(p["idx"])
    return int(p)


def _ni(p) -> int:
    """Loop-carried edges carry nextiter=1; they are not intra-iteration order."""
    if isinstance(p, (list, tuple)) and len(p) > 2:
        return int(p[2])
    return 0


def _topo(nodes: Sequence[dict]) -> List[int]:
    idx = {n["idx"]: n for n in nodes}
    indeg = {i: 0 for i in idx}
    for n in nodes:
        for p in n.get("parents", []):
            if _ni(p) == 0 and _pid(p) in indeg:
                indeg[n["idx"]] += 1
    ready = sorted(i for i, d in indeg.items() if d == 0)
    order: List[int] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for c in idx[cur].get("children", []):
            if _ni(c) != 0:
                continue
            cid = _pid(c)
            if cid in indeg:
                indeg[cid] -= 1
                if indeg[cid] == 0:
                    ready.append(cid)
        ready.sort()
    if len(order) != len(idx):
        raise ValueError("DFG has a cycle")
    return order


def _intra_parents(n: dict) -> List[int]:
    return [_pid(p) for p in n.get("parents", []) if _ni(p) == 0]


def schedule(facts: dict, ii: int, spatial: Dict[int, Tuple[int, int]],
             max_rounds: int = 400) -> dict:
    """Return ``{node: {"x","y","t"}}`` with a legal phase assignment."""
    nodes = facts["nodes"]
    idx = {n["idx"]: n for n in nodes}
    order = _topo(nodes)
    lat = {n["idx"]: int(n.get("lat", 1)) for n in nodes}
    t: Dict[int, int] = {i: 0 for i in order}

    def earliest(i: int) -> int:
        return max([t[p] + lat[p] for p in _intra_parents(idx[i]) if p in t], default=0)

    for _ in range(max_rounds):
        changed = False
        for i in order:  # keep every node at or after its earliest start
            need = earliest(i)
            if t[i] < need:
                t[i] = need
                changed = True
        for i in order:
            for p in _intra_parents(idx[i]):
                if p in t and _slack(t[i], t[p], 0, lat[p], ii) < 0:
                    t[i] += 1
                    changed = True
        for i in order:
            if idx[i].get("op") not in SELECT_OPS:
                continue
            ps = sorted(set(_intra_parents(idx[i])))
            if len(ps) < 2:
                continue
            for a in range(len(ps)):
                for b in range(a + 1, len(ps)):
                    if t[ps[a]] % ii == t[ps[b]] % ii:
                        # push the higher-id parent one cycle, keeping its own
                        # dependency constraint intact; never move it back
                        q = ps[b]
                        t[q] += 1
                        need = earliest(q)
                        while (t[q] - need) % ii != 0 or t[q] < need:
                            t[q] += 1
                        changed = True
        if not changed:
            break

    for _ in range(4):
        if _pos_conflicts(spatial, t, ii) == 0:
            break
        for node, xy in sorted(spatial.items()):
            key = (int(xy[0]), int(xy[1]), t[node] % ii)
            for other, xy2 in spatial.items():
                if other == node:
                    continue
                if (int(xy2[0]), int(xy2[1]), t[other] % ii) == key and other > node:
                    t[other] = (t[other] + 1) % ii
    if _pos_conflicts(spatial, t, ii):
        raise ValueError("spatial collisions cannot be resolved by re-timing")
    bad = [i for i in order if _violates(i, idx, t, ii)]
    if bad:
        raise ValueError("no legal schedule for this spatial placement; "
                         "unresolved nodes %r" % bad[:8])
    return {node: {"x": int(x), "y": int(y), "t": int(t[node]) % ii}
            for node, (x, y) in spatial.items() if node in t}


def _violates(i: int, idx: dict, t: dict, ii: int) -> bool:
    n = idx[i]
    for p in _intra_parents(n):
        if p in t:
            if _slack(t[i], t[p], 0, int(idx[p].get("lat", 1)), ii) < 0:
                return True
    if n.get("op") in SELECT_OPS:
        ps = [_pid(p) for p in n.get("parents", []) if _ni(p) == 0 and _pid(p) in t]
        phases = [t[p] % ii for p in ps]
        if len(phases) != len(set(phases)):
            return True
    return False


def _pos_conflicts(spatial: dict, t: dict, ii: int) -> int:
    """Two nodes on one PE must not share a phase (the mapper asserts otherwise)."""
    seen = {}
    bad = 0
    for node, xy in spatial.items():
        if node not in t:
            continue
        key = (int(xy[0]), int(xy[1]), t[node] % ii)
        if key in seen:
            bad += 1
        seen[key] = node
    return bad


def to_pins(placement: dict) -> List[dict]:
    return [{"node": int(n), "x": v["x"], "y": v["y"], "t": v["t"]}
            for n, v in sorted(placement.items())]


def schedule_anneal(facts: dict, ii: int, spatial: Dict[int, Tuple[int, int]],
                    iters: int = 40000, seed: int = 0) -> dict:
    """Fallback: local search over phases only, cost = violated constraints.

    The greedy scheduler can paint itself into a corner because a SELECT fix
    moves a parent, which moves its children, which can re-collide elsewhere.
    Annealing over ``t`` alone is cheap (n_nodes * II states) and answers the
    question we actually care about: does a legal schedule exist at all for this
    spatial placement?
    """
    import random

    rng = random.Random(seed)
    nodes = facts["nodes"]
    idx = {n["idx"]: n for n in nodes}
    lat = {n["idx"]: int(n.get("lat", 1)) for n in nodes}
    ids = [n["idx"] for n in nodes]
    t = {i: rng.randrange(ii) for i in ids}

    def cost_one(i: int) -> int:
        c = 0
        n = idx[i]
        for p in _intra_parents(n):
            if p in t:
                if _slack(t[i], t[p], 0, lat[p], ii) < 0:
                    c += 1
        if n.get("op") in SELECT_OPS:
            ps = sorted(set(_intra_parents(n)))
            for a in range(len(ps)):
                for b in range(a + 1, len(ps)):
                    if t[ps[a]] % ii == t[ps[b]] % ii:
                        c += 1
        return c

    def pos_cost() -> int:
        return _pos_conflicts(spatial, t, ii)

    def total() -> int:
        return sum(cost_one(i) for i in ids) + pos_cost()

    cur = total()
    best = cur
    for step in range(iters):
        if cur == 0:
            break
        i = rng.choice(ids)
        old = t[i]
        t[i] = (old + rng.choice((-1, 1))) % ii
        new = total()
        temp = max(0.01, 2.0 * (1.0 - step / float(iters)))
        if new <= cur or rng.random() < pow(2.718, -(new - cur) / temp):
            cur = new
            best = min(best, cur)
        else:
            t[i] = old
    if cur != 0:
        raise ValueError("no legal schedule found by annealing (cost=%d)" % cur)
    return {node: {"x": int(x), "y": int(y), "t": int(t[node]) % ii}
            for node, (x, y) in spatial.items() if node in t}
