"""Parse a DFG XML plus a bank-annotated arch JSON into one facts document.

Two quirks of this toolchain drive the implementation:

* the DFG writer emits attributes without a separating space
  (``<Node idx="11" ASAP="0" BB=""CONST="4094">``), so no conforming XML parser
  can read it -- a small regex scanner is the only tolerant option;
* ``update_mem_alloc.py`` injects ``SPM_B<i>_WRAPPER`` modules but does *not*
  add PE->bank wiring, so the arch may expose the variables of each bank while
  telling us nothing about which memory PE reaches which bank.  Facts records
  that honestly instead of inventing a mapping.

``facts.json`` is the only DFG view the placer gets, so everything here is
JSON serialisable and small enough to paste into a prompt.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence

NODE_RE = re.compile(r"<Node\b([^>]*)>(.*?)</Node>", re.S)
ATTR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([^\"]*)\"")
OP_RE = re.compile(r"<OP>\s*([^<]*?)\s*</OP>")
IN_RE = re.compile(r"<Input\s+idx=\"(\d+)\"")
OUT_RE = re.compile(r"<Output\b([^>]*)/>")
BP_RE = re.compile(r"<BasePointerName\b[^>]*>\s*([^<]*?)\s*</BasePointerName>")

# Only these mnemonics touch memory.  FU_MEM's op table also lists every ALU
# op (a memory PE can run an ADD), so membership in ops_mem is not a memory
# test on its own.
MEMORY_OPS = frozenset(
    ("LOAD", "LOADB", "LOADH", "LOADCL", "STORE", "STOREB", "STOREH")
)

CONST_MIN = -(1 << 26)
CONST_MAX = (1 << 26) - 1


class FactsError(RuntimeError):
    """The DFG or the architecture file cannot be turned into facts."""


def _attrs(blob: str) -> Dict[str, str]:
    return {k: v for k, v in ATTR_RE.findall(blob)}


def parse_dfg(path: str) -> List[Dict[str, Any]]:
    """Return one dict per node, sorted by node index."""
    text = open(path, "r", errors="replace").read()
    nodes: Dict[int, Dict[str, Any]] = {}
    for blob, body in NODE_RE.findall(text):
        a = _attrs(blob)
        idx = int(a.get("idx", "-1"))
        if idx < 0:
            raise FactsError("node without idx in %s" % path)
        const = None
        if "CONST" in a and a["CONST"] != "":
            try:
                const = int(a["CONST"])
            except ValueError:
                const = None
        m = OP_RE.search(body)
        op = (m.group(1) if m else "NOP").strip()
        m = BP_RE.search(body)
        bp = m.group(1).strip() if m else ""
        children = []
        for oblob in OUT_RE.findall(body):
            oa = _attrs(oblob)
            children.append(
                [int(oa.get("idx", "-1")), oa.get("type", ""), int(oa.get("nextiter", "0"))]
            )
        nodes[idx] = {
            "idx": idx,
            "op": op,
            "const": const,
            "asap": int(a.get("ASAP", "0")),
            "alap": int(a.get("ALAP", "0")),
            "bb": a.get("BB", "") or "",
            "bp": bp,
            "parents": [],
            "children": sorted(children),
        }
    if not nodes:
        raise FactsError("no <Node> found in %s" % path)
    # Parent links are rebuilt from the child side: <Input> carries no port.
    for n in nodes.values():
        for cidx, port, ni in n["children"]:
            if cidx in nodes:
                nodes[cidx]["parents"].append([n["idx"], port, ni])
    for n in nodes.values():
        n["parents"].sort()
    return [nodes[k] for k in sorted(nodes)]


def parse_arch(path: str) -> Dict[str, Any]:
    """Return grid, PE tables, op latencies and whatever bank wiring exists."""
    try:
        data = json.load(open(path, "r"))
    except (OSError, ValueError) as exc:
        raise FactsError("cannot read arch json %s: %s" % (path, exc))
    if "ARCH" not in data:
        raise FactsError("no ARCH key in %s" % path)
    arch = data["ARCH"]
    cgra = arch.get("CGRA")
    if not isinstance(cgra, dict):
        raise FactsError("no ARCH.CGRA in %s" % path)

    dims = {"X": 0, "Y": 0}
    mods: List[dict] = []
    subs = cgra.get("SUBMODS")
    if isinstance(subs, list):
        for s in subs:
            if isinstance(s, dict) and s.get("PATTERN") == "GRID":
                dims = s.get("DIMS", dims)
                mods = s.get("MODS", []) or []
                break
    elif isinstance(subs, dict):
        for _name, s in subs.items():
            if isinstance(s, dict) and s.get("PATTERN") == "GRID":
                dims = s.get("DIMS", dims)
                mods = s.get("MODS", []) or []
                break
    if not mods:
        raise FactsError("no GRID MODS in %s" % path)

    def ops_of(typename: str) -> Dict[str, int]:
        table = arch.get(typename) or {}
        out: Dict[str, int] = {}
        for op, lat in (table.get("OPS") or {}).items():
            try:
                out[str(op)] = int(lat)
            except (TypeError, ValueError):
                out[str(op)] = 1
        return out

    lat_alu = ops_of("FU")
    lat_mem = ops_of("FU_MEM")
    if not lat_alu and lat_mem:
        lat_alu = dict(lat_mem)
    if not lat_mem:
        lat_mem = dict(lat_alu)

    pe_mem = sorted(
        (int(m.get("X", 0)), int(m.get("Y", 0)))
        for m in mods
        if m.get("MOD") == "PE_MEM"
    )
    pe_alu = sorted(
        (int(m.get("X", 0)), int(m.get("Y", 0)))
        for m in mods
        if m.get("MOD") == "PE"
    )
    pe_all = sorted({(int(m.get("X", 0)), int(m.get("Y", 0))) for m in mods})

    # Variables per bank, straight from the wrapper modules.
    wrapper_bank: Dict[str, int] = {}
    var_bank: Dict[str, int] = {}
    for key, val in arch.items():
        if not (isinstance(key, str) and key.startswith("SPM_B") and key.endswith("_WRAPPER")):
            continue
        if not isinstance(val, dict):
            continue
        try:
            idx = int(key[len("SPM_B"):].split("_")[0])
        except ValueError:
            continue
        wrapper_bank[key + "_INS"] = idx
        wrapper_bank[key] = idx
        for var in (val.get("DATA_LAYOUT") or {}):
            var_bank[str(var)] = idx

    # PE->bank wiring is only inferable when the arch still has an explicit
    # <PE>.MEMPORT -> SPM wrapper connection table.
    pe_bank: Dict[tuple, int] = {}
    pos_of = {m["name"]: (int(m.get("X", 0)), int(m.get("Y", 0)))
              for m in mods if m.get("name")}
    conns = cgra.get("CONNECTIONS")
    if isinstance(conns, dict):
        for src, dsts in conns.items():
            if ".MEMPORT" not in str(src):
                continue
            pos = pos_of.get(str(src).split(".")[0])
            if pos is None:
                continue
            for d in (dsts if isinstance(dsts, list) else [dsts]):
                b = wrapper_bank.get(str(d).split(".")[0])
                if b is not None:
                    pe_bank[pos] = b
                    break

    bank_pes: Dict[int, List[tuple]] = {}
    for pos, b in pe_bank.items():
        bank_pes.setdefault(b, []).append(pos)
    for b in bank_pes:
        bank_pes[b].sort()

    return {
        "file": os.path.basename(path),
        "xdim": int(dims.get("X", 0)),
        "ydim": int(dims.get("Y", 0)),
        "pe_all": pe_all,
        "pe_mem": pe_mem,
        "pe_alu": pe_alu,
        "lat_alu": lat_alu,
        "lat_mem": lat_mem,
        "var_bank": var_bank,
        "pe_bank": {"%d,%d" % k: v for k, v in pe_bank.items()},
        "bank_pes": bank_pes,
        "wiring": "connections" if pe_bank else "none",
    }


def op_latency(arch: Dict[str, Any], op: str, is_mem_pe: bool) -> int:
    table = arch["lat_mem"] if is_mem_pe else arch["lat_alu"]
    if op in table:
        return int(table[op])
    return int(arch["lat_alu"].get(op, 1))


def res_min_ii(nodes: Sequence[dict], arch: Dict[str, Any]) -> int:
    """Resource bound: ceil(count(op) / #PE supporting op), over all ops."""
    counts: Dict[str, int] = {}
    for n in nodes:
        counts[n["op"]] = counts.get(n["op"], 0) + 1
    n_alu = max(1, len(arch["pe_all"]))
    n_mem = max(1, len(arch["pe_mem"]) or len(arch["pe_all"]))
    bound = 1
    for op, c in counts.items():
        npe = n_mem if op in MEMORY_OPS else n_alu
        bound = max(bound, -(-c // npe))
    return bound


def build_facts(dfg_path: str, arch_path: str, ii: Optional[int] = None) -> Dict[str, Any]:
    nodes = parse_dfg(dfg_path)
    arch = parse_arch(arch_path)
    nodes_by_idx = {n["idx"]: n for n in nodes}

    node_bank: Dict[int, int] = {}
    for n in nodes:
        if n["op"] in MEMORY_OPS and n["bp"] and n["bp"] in arch["var_bank"]:
            node_bank[n["idx"]] = arch["var_bank"][n["bp"]]

    edges = []
    degree = {n["idx"]: {"in": 0, "out": 0} for n in nodes}
    for n in nodes:
        for cidx, port, ni in n["children"]:
            if cidx not in nodes_by_idx:
                continue
            edges.append([n["idx"], cidx, port, ni])
            degree[n["idx"]]["out"] += 1
            degree[cidx]["in"] += 1

    bank_vars: Dict[int, List[str]] = {}
    for var, b in arch["var_bank"].items():
        bank_vars.setdefault(b, []).append(var)
    for b in bank_vars:
        bank_vars[b].sort()

    warnings: List[str] = []
    if arch["wiring"] == "none":
        warnings.append(
            "arch has no PE->bank wiring (update_mem_alloc.py adds SPM wrappers, "
            "not connections): R5 is advisory, the mapper/simulator is the judge"
        )
    if not arch["pe_mem"]:
        warnings.append("arch declares no PE_MEM: LOAD/STORE cannot be placed")
    for n in nodes:
        if n["op"] not in arch["lat_alu"] and n["op"] not in arch["lat_mem"]:
            warnings.append("node %d op %s is not in any PE op table" % (n["idx"], n["op"]))

    n_pe = len(arch["pe_all"]) or arch["xdim"] * arch["ydim"]
    ii_txt = int(ii) if ii else 0
    facts: Dict[str, Any] = {
        "dfg_file": os.path.basename(dfg_path),
        "arch_file": arch["file"],
        "xdim": arch["xdim"],
        "ydim": arch["ydim"],
        "ii": ii,
        "res_min_ii": res_min_ii(nodes, arch),
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "n_inputs": sum(len(n["parents"]) for n in nodes),
        "op_hist": {},
        "ops": {
            "alu": sorted(arch["lat_alu"]),
            "mem": sorted(arch["lat_mem"]),
            "memory_ops": sorted(MEMORY_OPS),
        },
        "pe_all": arch["pe_all"],
        "pe_alu": arch["pe_alu"],
        "pe_mem": arch["pe_mem"],
        "var_bank": arch["var_bank"],
        "bank_vars": bank_vars,
        "pe_bank": arch["pe_bank"],
        "bank_pes": arch["bank_pes"],
        "wiring": arch["wiring"],
        "node_bank": node_bank,
        "nodes": nodes,
        "edges": edges,
        "degree": {str(k): v for k, v in degree.items()},
        "phase_capacity": {
            "pes": n_pe,
            "per_phase": n_pe,
            "slots_total": n_pe * ii_txt if ii_txt else n_pe,
            "slots_mem": len(arch["pe_mem"]) * ii_txt if ii_txt else len(arch["pe_mem"]),
            "slots_alu": len(arch["pe_all"]) * ii_txt if ii_txt else len(arch["pe_all"]),
        },
        "arch_warnings": warnings,
    }
    for n in nodes:
        facts["op_hist"][n["op"]] = facts["op_hist"].get(n["op"], 0) + 1
    facts["min_ii"] = max(1, facts["res_min_ii"])
    return facts


def facts_for_ii(facts: Dict[str, Any], ii: int) -> Dict[str, Any]:
    """A shallow copy whose II-dependent fields follow a new II."""
    out = dict(facts)
    out["ii"] = int(ii)
    n_pe = len(facts["pe_all"])
    pc = dict(facts["phase_capacity"])
    pc.update(
        {
            "per_phase": n_pe,
            "slots_total": n_pe * ii,
            "slots_mem": len(facts["pe_mem"]) * ii,
            "slots_alu": n_pe * ii,
        }
    )
    out["phase_capacity"] = pc
    return out


def _fmt_pe(p) -> str:
    return "(%d,%d)" % (int(p[0]), int(p[1]))


def render_facts(facts: Dict[str, Any]) -> str:
    """The compact text block used by the placement prompt."""
    xdim, ydim = facts["xdim"], facts["ydim"]
    ii = facts["ii"]
    L: List[str] = []
    L.append(
        "ARCH %s  grid %dx%d (x=0..%d, y=0..%d)"
        % (facts["arch_file"], xdim, ydim, xdim - 1, ydim - 1)
    )
    L.append(
        "memory PEs (only these may run LOAD/STORE): %s"
        % (", ".join(_fmt_pe(p) for p in facts["pe_mem"]) or "none")
    )
    for b in sorted(facts.get("bank_vars") or {}):
        pes = facts.get("bank_pes", {}).get(b) or facts.get("bank_pes", {}).get(str(b))
        pes_txt = ", ".join(_fmt_pe(p) for p in pes) if pes else "?"
        L.append(
            "bank %d: memory PEs %s -> holds variables %s"
            % (b, pes_txt, ", ".join(facts["bank_vars"][b]) or "-")
        )
    L.append(
        "II = %s   ResMinII = %s   nodes = %d   edges = %d"
        % (ii, facts["res_min_ii"], facts["n_nodes"], facts["n_edges"])
    )
    pc = facts.get("phase_capacity") or {}
    L.append(
        "phase capacity: %s PE x %s phases = %s slots (memory slots %s)"
        % (pc.get("pes"), ii, pc.get("slots_total"), pc.get("slots_mem"))
    )
    L.append("alu ops: %s" % " ".join(facts["ops"]["alu"]))
    L.append("memory ops: %s" % " ".join(facts["ops"]["memory_ops"]))
    L.append(
        "op histogram: %s"
        % ", ".join("%s x%d" % (k, v) for k, v in sorted(facts["op_hist"].items()))
    )
    L.append("")
    L.append("NODES  (idx | op | const | bank | parents -> children)")
    for n in facts["nodes"]:
        par = " ".join(
            "%d%s%s" % (p[0], (":" + p[1]) if p[1] else "", ("@next" if p[2] else ""))
            for p in n["parents"]
        )
        ch = " ".join(
            "%d%s%s" % (c[0], (":" + c[1]) if c[1] else "", ("@next" if c[2] else ""))
            for c in n["children"]
        )
        extra = ""
        if n["const"] is not None:
            extra += " c=%d" % n["const"]
        if n["bp"]:
            extra += " bp=%s" % n["bp"]
        if n["idx"] in facts.get("node_bank", {}):
            b = facts["node_bank"][n["idx"]]
            pes = facts.get("bank_pes", {}).get(b) or facts.get("bank_pes", {}).get(str(b))
            hint = (" -> x,y one of %s" % ", ".join(_fmt_pe(p) for p in pes)) if pes else ""
            extra += " bank=%d%s" % (b, hint)
        L.append(
            "  %2d | %-7s%s | in: %s | out: %s"
            % (n["idx"], n["op"], extra, par or "-", ch or "-")
        )
    return "\n".join(L)
