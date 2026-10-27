"""Cheap local validation of a placement before paying for a mapper run.

Errors mean "definitely illegal, re-ask the model"; warnings mean "legal but
likely to fail routing", which the mapper remains the judge of.  The rule
numbers follow docs/WORKFLOW.md R1..R5 plus the per-phase capacity check.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from .facts import CONST_MAX, CONST_MIN, MEMORY_OPS


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise ValueError("not an integer: %r" % (value,))


def _pins_list(pins: Any) -> List[dict]:
    if hasattr(pins, "pins"):
        return list(pins.pins)  # Placement
    return list(pins or [])


def _pe_key(x: int, y: int) -> str:
    return "%d,%d" % (x, y)


def validate(
    facts: Dict[str, Any],
    pins: Any,
    ii: Optional[int] = None,
    require_complete: bool = True,
) -> Dict[str, Any]:
    ii = int(ii or facts.get("ii") or 1)
    xdim, ydim = int(facts["xdim"]), int(facts["ydim"])
    pe_all = {tuple(p) for p in facts["pe_all"]}
    pe_mem = {tuple(p) for p in facts["pe_mem"]}
    ops_alu = set((facts.get("ops") or {}).get("alu") or [])
    ops_mem = set((facts.get("ops") or {}).get("mem") or [])
    nodes = {n["idx"]: n for n in facts["nodes"]}
    node_bank = facts.get("node_bank") or {}
    pe_bank = {k: int(v) for k, v in (facts.get("pe_bank") or {}).items()}

    errors: List[str] = []
    warnings: List[str] = []
    placement: Dict[int, dict] = {}
    slot: Dict[tuple, int] = {}
    pinned: set = set()
    per_pe: Dict[tuple, int] = {}
    per_phase: Dict[int, int] = {}

    pins = _pins_list(pins)

    # -- R1..R5, one pin at a time -----------------------------------------
    for p in pins:
        if not isinstance(p, dict):
            errors.append("pin is not an object: %r" % (p,))
            continue
        try:
            idx = _as_int(p["node"])
            x = _as_int(p["x"])
            y = _as_int(p["y"])
            t = _as_int(p["t"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append("pin with missing/non-integer fields (%s): %r" % (exc, p))
            continue

        if idx not in nodes:
            errors.append("pin for unknown node %d" % idx)
            continue
        if idx in pinned:
            errors.append("node %d pinned twice" % idx)
            continue
        pinned.add(idx)
        n = nodes[idx]

        # R4: bounds
        if not (0 <= x < xdim) or not (0 <= y < ydim):
            errors.append(
                "node %d at (%d,%d): outside the %dx%d grid" % (idx, x, y, xdim, ydim)
            )
            continue
        if not (0 <= t < ii):
            errors.append(
                "node %d phase t=%d outside 0..%d (II=%d)" % (idx, t, ii - 1, ii)
            )
            continue
        if (x, y) not in pe_all:
            errors.append("node %d at (%d,%d): no PE there" % (idx, x, y))
            continue

        # R2: op support, memory ops only on memory PEs
        if ops_alu or ops_mem:
            if n["op"] not in ops_alu and n["op"] not in ops_mem:
                errors.append(
                    "node %d op %s is not supported by this architecture"
                    % (idx, n["op"])
                )
                continue
        is_mem_op = n["op"] in MEMORY_OPS
        if is_mem_op and (x, y) not in pe_mem:
            errors.append(
                "node %d op %s needs a memory PE, (%d,%d) is a plain PE "
                "(memory PEs: %s)"
                % (
                    idx,
                    n["op"],
                    x,
                    y,
                    ", ".join("(%d,%d)" % q for q in sorted(pe_mem)) or "none",
                )
            )
            continue

        # R5: a LOAD/STORE must reach the bank that holds its variable.  Only
        # checkable when the arch still has PE->bank wiring.
        if is_mem_op and pe_bank and idx in node_bank:
            want = node_bank[idx]
            got = pe_bank.get(_pe_key(x, y))
            if got is None:
                warnings.append(
                    "node %d (%s %s): bank wiring unknown for (%d,%d), R5 skipped"
                    % (idx, n["op"], n["bp"], x, y)
                )
            elif got != want:
                errors.append(
                    "node %d (%s %s) must use bank %d, but (%d,%d) is wired to bank %d"
                    % (idx, n["op"], n["bp"], want, x, y, got)
                )
                continue

        # R1: one node per (PE, phase)
        key = (x, y, t)
        if key in slot:
            errors.append(
                "PE (%d,%d) phase %d used by two nodes: %d and %d"
                % (x, y, t, slot[key], idx)
            )
            continue
        slot[key] = idx
        per_pe[(x, y)] = per_pe.get((x, y), 0) + 1
        per_phase[t] = per_phase.get(t, 0) + 1
        placement[idx] = {"node": idx, "x": x, "y": y, "t": t, "op": n["op"]}

    # -- constants that cannot be encoded ----------------------------------
    for n in facts["nodes"]:
        if n["const"] is not None and not (CONST_MIN <= int(n["const"]) <= CONST_MAX):
            errors.append(
                "node %d constant %d does not fit in 27 bit" % (n["idx"], int(n["const"]))
            )

    if require_complete:
        missing = [i for i in sorted(nodes) if i not in pinned]
        if missing:
            errors.append(
                "%d node(s) without a pin: %s"
                % (len(missing), missing[:15] + (["..."] if len(missing) > 15 else []))
            )

    # -- dependency lower bound (hard), R3/R6 (soft) -----------------------
    # Dependency: a consumer must see the producer's value, i.e. its phase is at
    # least ``lat`` cycles after the producer's phase on the cyclic schedule.
    # The PE distance is *not* part of that bound: HyCUBE forwards
    # combinationally within a cycle, so jump count only affects port pressure.
    for n in facts["nodes"]:
        if n["idx"] not in placement:
            continue
        a = placement[n["idx"]]
        lat_a = int(n.get("lat") or 1)
        for cidx, port, ni in n["children"]:
            if cidx not in placement:
                continue
            b = placement[cidx]
            d = (b["t"] - a["t"] - ni * ii) % ii
            hops = abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])
            if d < lat_a:
                errors.append(
                    "edge %d->%d (%s): child phase %d is %d cycle(s) after parent "
                    "phase %d, but %s needs %d cycle(s) of latency"
                    % (n["idx"], cidx, port or "?", b["t"], d, a["t"], n["op"], lat_a)
                )
            if hops > d:
                warnings.append(
                    "edge %d->%d (%s): phase difference %d is smaller than the "
                    "%d-hop distance; HyCUBE allows combinational forwarding so "
                    "this is not a hard constraint, but it raises the chance of "
                    "port conflicts" % (n["idx"], cidx, port or "?", d, hops)
                )

    for n in facts["nodes"]:
        if n["op"] not in ("SELECT", "CMERGE") or n["idx"] not in placement:
            continue
        phases = [
            (pidx, placement[pidx]["t"], ni)
            for (pidx, _port, ni) in n["parents"]
            if pidx in placement
        ]
        for i in range(len(phases)):
            for j in range(i + 1, len(phases)):
                if phases[i][1] == phases[j][1]:
                    warnings.append(
                        "node %d (%s): parents %d and %d both enter at phase %d; "
                        "this op must not receive two operands in the same cycle"
                        % (n["idx"], n["op"], phases[i][0], phases[j][0], phases[i][1])
                    )

    # -- capacity ----------------------------------------------------------
    n_pe = max(1, len(pe_all))
    if per_phase and max(per_phase.values()) > n_pe:
        errors.append(
            "phase %d holds %d pins but the grid only has %d PEs"
            % (max(per_phase, key=per_phase.get), max(per_phase.values()), n_pe)
        )
    stats = {
        "n_pins": len(pins),
        "n_nodes": len(nodes),
        "n_placed": len(placement),
        "slots_used": len(slot),
        "slots_total": n_pe * ii,
        "per_phase": {str(k): v for k, v in sorted(per_phase.items())},
        "per_pe_max": max(per_pe.values()) if per_pe else 0,
        "memory_pins": sum(1 for p in placement.values() if p["op"] in MEMORY_OPS),
    }

    for w in facts.get("arch_warnings") or []:
        if w not in warnings:
            warnings.append(w)

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "placement": [placement[k] for k in sorted(placement)],
        "stats": stats,
    }


def format_feedback(
    result: Dict[str, Any], max_errors: int = 12, max_warnings: int = 6
) -> str:
    """The text the placer receives after a rejected placement."""
    lines: List[str] = []
    errors = result.get("errors") or []
    if errors:
        lines.append("The placement was rejected by the local validator:")
        for e in errors[:max_errors]:
            lines.append("  - %s" % e)
        if len(errors) > max_errors:
            lines.append("  - ... and %d more error(s)" % (len(errors) - max_errors))
    warnings = [w for w in (result.get("warnings") or []) if "no PE->bank wiring" not in w]
    if warnings:
        lines.append("Also consider these risks:")
        for w in warnings[:max_warnings]:
            lines.append("  - %s" % w)
        if len(warnings) > max_warnings:
            lines.append("  - ... and %d more warning(s)" % (len(warnings) - max_warnings))
    lines.append(
        "Fix every error and answer again with the full json object "
        "(one pin per node, x/y/t integers)."
    )
    return "\n".join(lines)
