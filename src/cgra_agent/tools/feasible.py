"""Turn a failure into a *repairable* instruction.

The retry loop used to feed the model prose ("the pin was rejected") plus one
false rule (phase difference *equal* to the manhattan distance).  This module
fixes both: it names the nodes that actually conflict, lists the (x, y)
positions those nodes are allowed to take, and forbids touching the rest, so a
retry is a repair instead of a fresh draw.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

_MAX_NODES = 6
_MAX_PES = 8


def is_mem_op(op: str) -> bool:
    return str(op or "").upper().startswith(("LOAD", "STORE"))


def feasible_pes(facts: Dict[str, Any]) -> Dict[int, List[Tuple[int, int]]]:
    """(x, y) candidates the mapper accepts, per node.

    The mapper gates a pin on the *column*: LOAD/STORE only on x=0, every other
    op only off it.  Probed on array_add: a STORE pinned at y=0 and y=3 both
    mapped, so the bank is not what rejects a pin.
    """
    alu = [tuple(p) for p in (facts.get("pe_alu") or [])]
    mem = [tuple(p) for p in (facts.get("pe_mem") or [])]
    out: Dict[int, List[Tuple[int, int]]] = {}
    for node in facts.get("nodes") or []:
        pool = mem if is_mem_op(node.get("op", "")) else alu
        out[int(node["idx"])] = pool
    return out


def nodes_in_text(text: str) -> List[int]:
    seen: List[int] = []
    for raw in re.findall(r"node[ _#]?([0-9]+)", text or "", flags=re.I):
        idx = int(raw)
        if idx not in seen:
            seen.append(idx)
    return seen


def nodes_at(text: str, placement: Any) -> List[int]:
    """Nodes pinned on the PE coordinates quoted in the failure text."""
    coords = [(int(a), int(b))
              for a, b in re.findall(r"\(\s*([0-9]+)\s*,\s*([0-9]+)\s*\)", text or "")]
    if not coords or placement is None:
        return []
    hits: List[int] = []
    for pin in getattr(placement, "pins", []) or []:
        if (int(pin.x), int(pin.y)) in coords and int(pin.node) not in hits:
            hits.append(int(pin.node))
    return hits


def describe(
    facts: Dict[str, Any],
    ii: int,
    conflict: Sequence[int],
    placement: Any = None,
    *,
    header: str = "",
) -> str:
    """Header + conflict list + allowed positions + a do-not-touch clause."""
    ops = {int(n["idx"]): str(n.get("op", "")) for n in (facts.get("nodes") or [])}
    allowed = feasible_pes(facts)
    current: Dict[int, Any] = {}
    for pin in getattr(placement, "pins", []) or []:
        current[int(pin.node)] = pin

    lines: List[str] = []
    if header:
        lines.append(header)
    picked = [n for n in conflict if n in ops][:_MAX_NODES]
    if not picked:
        return "\n".join(lines)

    for node in picked:
        pes = allowed.get(node) or []
        shown = ",".join("(%d,%d)" % xy for xy in pes[:_MAX_PES])
        pin = current.get(node)
        here = " current (%d,%d,t=%d)" % (pin.x, pin.y, pin.t) if pin is not None else ""
        lines.append("node %d (%s):%s allowed (x,y): %s" % (node, ops[node], here, shown))
    if len(conflict) > len(picked):
        lines.append("(+%d more conflicting nodes)" % (len(conflict) - len(picked)))

    lines.append(
        "Change ONLY these nodes; repeat every other pin unchanged. "
        "For an edge parent->child keep (t_child - t_parent - nextiter*II) mod II "
        ">= the parent latency (larger is legal, smaller is not). II stays %d." % ii
    )
    return "\n".join(lines)