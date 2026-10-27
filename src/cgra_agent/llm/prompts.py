"""Prompt for the placement agent.

Everything the model needs is in one user message: the facts block, the
output contract, the rules and (on a retry) the previous answer plus concrete
feedback.  Keeping the system message stable also keeps prompt caching useful.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..tools.facts import render_facts

SYSTEM = """You are a CGRA place-and-route expert. You place a dataflow graph on \
a coarse-grained reconfigurable array (CGRA) for the Morpher compiler.

The hardware is a 2D mesh of PEs. Every PE holds one datapath and executes one \
operation per cycle. Time is modulo the initiation interval: a PE at phase t runs \
its operation in cycles t, t+II, t+2*II, ... A placement is a triple (node, x, y, t) \
with t in [0, II).

Hard rules that make a placement illegal:
 R1 every node gets exactly one pin, and no (PE, phase) pair may hold two nodes;
 R2 a node's op must be supported by the target PE; LOAD/STORE only on memory PEs;
 R3 a value produced by a parent must be consumable by the child: on the cyclic \
schedule, (t_child - t_parent - nextiter*II) mod II must be at least the producer's \
latency (1 cycle for ALU ops, 2 for LOAD/STORE). The PE distance is NOT part of this \
bound;
 R4 x in [0, xdim), y in [0, ydim), t in [0, II);
 R5 a LOAD/STORE should sit on a memory PE whose bank holds the variable it reaches; \
the facts list which variables belong to which bank;
 R6 SELECT must not receive two operands in the same cycle: keep the phases of its \
two parents different.

Crossing a PE boundary does not consume a cycle: HyCUBE forwards combinationally within \
a cycle, so jump count is not a scheduling constraint; the PathFinder router inserts \
detours afterwards. What does hurt is port pressure: long detours, many edges entering \
one PE in the same cycle, and edges with zero slack. Give each dependency enough slack \
(t_child - t_parent - nextiter*II), keep chains mostly along a row or column, and prefer \
short distances between a node and its children.

Answer with one json object and nothing else."""

SCHEMA = """{
  "pins": [ {"node": <int>, "x": <int>, "y": <int>, "t": <int>}, ... ],
  "reasoning": "<one or two sentences>",
  "ii": <int>
}"""


def build_messages(
    facts: Dict[str, Any],
    ii: int,
    feedback: Optional[str] = None,
    previous: Optional[Any] = None,
) -> list:
    xmax = int(facts["xdim"]) - 1
    ymax = int(facts["ydim"]) - 1
    tmax = max(0, int(ii) - 1)
    mempes = ", ".join("(%d,%d)" % (p[0], p[1]) for p in facts["pe_mem"]) or "none"
    banks = []
    for b in sorted(facts.get("bank_vars") or {}):
        pes = facts.get("bank_pes", {}).get(b) or facts.get("bank_pes", {}).get(str(b))
        pes_txt = " ".join("(%d,%d)" % (p[0], p[1]) for p in pes) if pes else "?"
        banks.append(
            "bank %d: PEs %s for %s"
            % (b, pes_txt, ",".join(facts["bank_vars"][b]) or "-")
        )
    nnodes = int(facts["n_nodes"])
    budget = max(tmax, 1)
    minpes = (nnodes + max(budget, 1)) // max(budget + 1, 1)

    rules = """Rules
 - Return exactly one pin for every node listed under NODES, and no extra pins.
 - x in [0, %(xmax)d], y in [0, %(ymax)d], t in [0, %(tmax)d].
 - Memory operations (LOAD/LOADB/LOADH/LOADCL/STORE/STOREB/STOREH) must be placed on a \
memory PE: %(mempes)s. %(banks)s
 - Two nodes must not share the same (x, y, t).
 - For an edge parent -> child: (t_child - t_parent - nextiter*II) mod II >= the parent's \
latency (1 for ALU ops, 2 for LOAD/STORE). Jump distance is not part of the legality \
check.
 - For SELECT give its two parents different phases: the hardware must not see both \
operands in one cycle.
 - Locality decides routability, not legality: keep hops = |dx| + |dy| small (a good \
target is <= II-1 = %(budget)d), give dependencies slack, and avoid many edges entering \
the same PE in the same phase.
 - Start each chain at a memory PE (x=0) and march RIGHTWARDS, one column per phase \
step: (0,y,t) feeds (1,y,t+1), which feeds (2,y,t+2), which feeds (3,y,t+3).
 - %(nnodes)d nodes need at least %(minpes)d PEs; do not pile the whole graph onto one \
column.
 - When a (PE, phase) slot is taken, shift that node by one phase on the same PE or move \
it one column, rather than jumping to a far PE.""" % {
        "xmax": xmax,
        "ymax": ymax,
        "tmax": tmax,
        "mempes": mempes,
        "banks": ("\n   " + "\n   ".join(banks)) if banks else "",
        "budget": budget,
        "nnodes": nnodes,
        "minpes": minpes,
    }

    parts = [
        "## Hardware and DFG",
        render_facts(facts),
        "",
        "## Output contract",
        SCHEMA,
        "",
        rules,
    ]
    if previous is not None:
        parts += ["", "## Your previous answer", json.dumps(previous, sort_keys=True), ""]
    if feedback:
        parts += [
            "",
            "## Feedback on the previous attempt",
            feedback,
            "",
            "Improve the placement accordingly and answer again with the full json object.",
        ]
    else:
        parts += ["", "Now produce the placement json."]
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]
