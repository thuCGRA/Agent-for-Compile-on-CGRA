"""Cheap rule placer: no search, milliseconds.

Deliberately naive, to measure how much of the LLM's job is "free" from the DFG
alone (the "cheap predictor" baseline):

- phase = ASAP % II;
- LOAD/STORE go to the memory PE of their bank (bank -> PE mapping from the
  arch when wired, otherwise bank order over the memory PE list);
- other nodes go to a compatible PE nearest to their parents, ties by PE order;
  conflicts are resolved by re-processing the unplaced nodes in degree-descending
  order.

There is no backtracking and no dependency-slack check (that is the point: it
measures what a trivial heuristic gets).  `RuleError` means the heuristic gave
up: `placement_not_found`, not a proof of infeasibility.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..interfaces import Pin, Placement
from ..tools.facts import MEMORY_OPS
from ..tools.jobdir import ToolError


class RuleError(ToolError):
    """The rule heuristic could not finish (no search, so no infeasibility claim)."""


class RulePlacer:
    source = "rules"

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
        if not pe_alu or not pe_mem:
            raise RuleError("architecture has no PE list")
        nodes = sorted(facts.get("nodes") or [], key=lambda n: int(n["idx"]))
        bank_pe = self._bank_to_pe(facts, pe_mem)
        occupied = set()
        pins: List[Pin] = []
        placed: Dict[int, Tuple[int, int]] = {}

        def assign(idx: int, x: int, y: int, t: int) -> bool:
            if (x, y, t) in occupied:
                return False
            pins.append(Pin(node=idx, x=x, y=y, t=t))
            occupied.add((x, y, t))
            placed[idx] = (x, y)
            return True

        mem_nodes = [n for n in nodes if (n.get("op") or "") in MEMORY_OPS]
        for n in mem_nodes:
            idx = int(n["idx"])
            t = int(n.get("asap") or 0) % ii if ii else 0
            bank = (facts.get("node_bank") or {}).get(str(idx)) or (facts.get("node_bank") or {}).get(idx)
            cand = bank_pe.get(bank)
            options = [cand] if cand else []
            options += [p for p in pe_mem if p not in options]
            for x, y in options:
                if assign(idx, x, y, t):
                    break
            else:
                raise RuleError("no free memory PE for node %d at phase %d" % (idx, t))

        degree = {}
        for n in nodes:
            degree[int(n["idx"])] = len(n.get("parents") or []) + len(n.get("children") or [])

        def parent_anchor(n) -> Optional[Tuple[float, float]]:
            seen = [placed.get(int(p)) for p, _port, _ni in (n.get("parents") or [])]
            seen = [p for p in seen if p]
            if not seen:
                return None
            return (sum(p[0] for p in seen) / len(seen), sum(p[1] for p in seen) / len(seen))

        pending = [n for n in nodes if (n.get("op") or "") not in MEMORY_OPS]
        for _ in range(2):
            if not pending:
                break
            pending.sort(key=lambda n: (-degree.get(int(n["idx"]), 0),
                                        int(n.get("asap") or 0), int(n["idx"])))
            still = []
            for n in pending:
                idx = int(n["idx"])
                t = int(n.get("asap") or 0) % ii if ii else 0
                anchor = parent_anchor(n)
                cands = sorted(pe_alu, key=lambda p: (
                    (abs(p[0] - anchor[0]) + abs(p[1] - anchor[1])) if anchor else 0,
                    p[1], p[0]))
                if not any(assign(idx, x, y, t) for x, y in cands):
                    still.append(n)
            pending = still

        if pending:
            n = pending[0]
            raise RuleError("no free PE for node %d (op=%s) at phase %d"
                            % (int(n["idx"]), n.get("op"), int(n.get("asap") or 0) % max(ii, 1)))
        return Placement(pins=pins, ii=ii,
                         note="rule placement: t=asap%%II, memory nodes on their bank PE, "
                              "others nearest to parents (no search)", source="rules")

    @staticmethod
    def _bank_to_pe(facts, pe_mem):
        bank_pes = facts.get("bank_pes") or {}
        out = {}
        if isinstance(bank_pes, dict) and bank_pes:
            for bank, pes in bank_pes.items():
                if pes:
                    out[bank] = tuple(pes[0]) if isinstance(pes[0], (list, tuple)) else tuple(pes)
            if out:
                return out
        banks = sorted((facts.get("bank_vars") or {}).keys())
        for i, bank in enumerate(banks):
            out[bank] = pe_mem[i % len(pe_mem)]
        return out
