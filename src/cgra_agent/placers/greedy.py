"""Deterministic first-fit placer.

Not part of arm A's thesis -- the LLM places, this does not -- but the
evaluation needs a *legal* placement for kernels that have no pinned fixture,
and "greedy vs LLM" is a useful reference point when comparing arms.  The
greedy placement respects R1/R2/R4 only (capacity, op support, bounds); bank
wiring and the simulator decide the rest.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from ..interfaces import Pin, Placement
from ..tools.facts import MEMORY_OPS
from ..tools.jobdir import ToolError


class GreedyError(ToolError):
    """The graph does not fit the array at this II."""


class GreedyPlacer:
    source = "greedy"

    def __init__(self, prefer_memory_pe: bool = True, seed: int = 0):
        self.prefer_memory_pe = prefer_memory_pe
        self.seed = int(seed)

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
        placed_t: Dict[int, int] = {}
        occupied = set()
        pins: List[Pin] = []
        for node in nodes:
            idx = int(node["idx"])
            op = node.get("op") or ""
            allowed = pe_mem if op in MEMORY_OPS else pe_alu
            start = max(0, int(node.get("asap") or 0)) % ii if ii else 0
            order = list(range(start, ii)) + list(range(0, start))
            placed = False
            for t in order:
                ok = True
                for pidx, _port, ni in parents[idx]:
                    if pidx not in placed_t:
                        continue
                    d = (t - placed_t[pidx] - ni * ii) % ii
                    if d < lat[pidx]:
                        ok = False
                        break
                if not ok:
                    continue
                for x, y in allowed:
                    if (x, y, t) in occupied:
                        continue
                    pins.append(Pin(node=idx, x=x, y=y, t=t))
                    occupied.add((x, y, t))
                    placed_t[idx] = t
                    placed = True
                    break
                if placed:
                    break
            if not placed:
                raise GreedyError(
                    "no legal slot for node %d (op=%s, asap=%d) at II=%d"
                    % (idx, op, int(node.get("asap") or 0), ii)
                )
        return Placement(
            pins=pins,
            ii=ii,
            note="greedy first-fit (evaluation reference, not an LLM placement)",
            source="greedy",
        )
