"""Deterministic placer with bounded backtracking.

Not part of arm A's thesis -- the LLM places, this does not -- but evaluation
needs a *legal* placement for kernels without a pinned fixture, and "greedy vs
LLM" is the reference point that keeps the LLM's contribution honest.

The search is a depth-first first-fit walk over the nodes in (asap, idx) order
with a bounded number of backtracks.  A couple of undo steps is usually enough
to get past the local dead-ends of a pure single-pass walk (gemm node 9 at II=4
is the canonical case); the bound keeps runtime predictable.
"""

from __future__ import annotations

import random
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..interfaces import Pin, Placement
from ..tools.facts import MEMORY_OPS
from ..tools.jobdir import ToolError

Candidate = Tuple[int, int, int]


class GreedyError(ToolError):
    """The heuristic could not place the graph at this II.

    This is `placement_not_found`, not a proof of `placement_infeasible`: the
    walk has a bounded backtrack budget and may give up while a legal layout
    exists (the mapper finds one for gemm at II=4, see ARM.md section 2.6).
    """


class GreedyPlacer:
    source = "greedy"

    def __init__(self, prefer_memory_pe: bool = True, seed: int = 0,
                 max_backtracks: int = 200):
        self.prefer_memory_pe = prefer_memory_pe
        self.seed = int(seed)
        self.max_backtracks = int(max_backtracks)

    def place(
        self,
        facts: Dict[str, Any],
        arch: Optional[Dict[str, Any]] = None,
        ii: int = 1,
        budget: Any = None,
        feedback: Optional[str] = None,
        previous: Optional[Any] = None,
    ) -> Placement:
        ii = int(ii)
        pe_alu = [tuple(p) for p in facts.get("pe_alu") or []]
        pe_mem = [tuple(p) for p in facts.get("pe_mem") or []]
        rng = random.Random(self.seed)
        rng.shuffle(pe_alu)
        rng.shuffle(pe_mem)
        if not pe_alu or not pe_mem:
            raise GreedyError("architecture has no PE list")

        nodes = sorted(
            facts.get("nodes") or [],
            key=lambda n: (int(n.get("asap") or 0), int(n["idx"])),
        )
        lat = {int(n["idx"]): int(n.get("lat") or 1) for n in nodes}
        parents = {
            int(n["idx"]): [(int(p), str(port or ""), int(ni))
                            for p, port, ni in (n.get("parents") or [])]
            for n in nodes
        }
        by_idx = {int(n["idx"]): n for n in nodes}
        order = [int(n["idx"]) for n in nodes]

        placed: Dict[int, Candidate] = {}
        occupied = set()
        pins: List[Pin] = []

        def candidates(idx: int) -> Iterator[Candidate]:
            node = by_idx[idx]
            op = node.get("op") or ""
            allowed = pe_mem if op in MEMORY_OPS else pe_alu
            start = max(0, int(node.get("asap") or 0)) % ii if ii else 0
            phases = list(range(start, ii)) + list(range(0, start))
            for t in phases:
                for x, y in allowed:
                    if (x, y, t) in occupied:
                        continue
                    if not self._deps_ok(idx, t, parents, placed, lat, ii):
                        continue
                    yield x, y, t

        iters: List[Optional[Iterator[Candidate]]] = [None] * len(order)
        pos = 0
        backtracks = 0
        while pos < len(order):
            if iters[pos] is None:
                iters[pos] = candidates(order[pos])
            idx = order[pos]
            found = False
            for x, y, t in iters[pos]:  # type: ignore[union-attr]
                if (x, y, t) in occupied:
                    continue
                if not self._deps_ok(idx, t, parents, placed, lat, ii):
                    continue
                pins.append(Pin(node=idx, x=x, y=y, t=t))
                occupied.add((x, y, t))
                placed[idx] = (x, y, t)
                found = True
                break
            if found:
                pos += 1
                continue
            iters[pos] = None
            if pos == 0 or backtracks >= self.max_backtracks:
                break
            backtracks += 1
            pos -= 1
            undo = order[pos]
            x, y, t = placed.pop(undo)
            occupied.discard((x, y, t))
            pins.pop()

        if pos < len(order):
            idx = order[pos]
            node = by_idx[idx]
            raise GreedyError(
                "no legal slot for node %d (op=%s, asap=%d) at II=%d after %d backtrack(s)"
                % (idx, node.get("op") or "", int(node.get("asap") or 0), ii, backtracks)
            )
        return Placement(
            pins=pins,
            ii=ii,
            note="greedy first-fit, %d backtrack budget (evaluation reference)"
            % self.max_backtracks,
            source="greedy",
        )

    @staticmethod
    def _deps_ok(idx, t, parents, placed, lat, ii) -> bool:
        for pidx, _port, ni in parents.get(idx, []):
            if pidx not in placed:
                continue
            d = (t - placed[pidx][2] - ni * ii) % ii
            if d < lat[pidx]:
                return False
        return True
