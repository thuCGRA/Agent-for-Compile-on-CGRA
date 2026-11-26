#!/usr/bin/env python3
"""Convert a Morpher DFG XML into the dot format CGRA-ME reads.

The two tools describe the same loop body but not the same way:

* Morpher's XML carries an explicit ``nextiter`` flag on each edge, which is how
  it marks loop-carried dependences; CGRA-ME infers recurrences from ``phi``
  nodes and the ``-i`` context count instead, so ``nextiter`` is dropped here.
  That is a real semantic difference and it is why CGRA-ME may find a different
  (usually looser) schedule on the same graph.
* Morpher names its opcodes after its own HyCUBE datapath (``LOADB``,
  ``OSTORE``, ``CMERGE``); CGRA-ME uses LLVM-ish names.  Ops with no CGRA-ME
  counterpart are reported rather than silently approximated.

usage: morpher_xml_to_dot.py <dfg.xml> [-o out.dot]
"""
import argparse
import re
import sys

NODE_RE = re.compile(r"<Node\b([^>]*)>(.*?)</Node>", re.S)
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
OP_RE = re.compile(r"<OP>([^<]*)</OP>")
BP_RE = re.compile(r"<BasePointerName[^>]*>([^<]*)</BasePointerName>")
OUT_RE = re.compile(r"<Output\b([^>]*)/?>")
IN_RE = re.compile(r"<Input\b([^>]*)/?>")

# Morpher opcode -> CGRA-ME opcode.  Keys are what the PartPred DFGs contain.
OPMAP = {
    "ADD": "add",
    "SUB": "sub",
    "MUL": "mul",
    "AND": "and",
    "OR": "or",
    "XOR": "xor",
    "LS": "shl",
    "RS": "lshr",
    "ARS": "ashr",
    "CMP": "icmp",
    "CLT": "icmp",
    "CGT": "icmp",
    "SELECT": "select",
    "CMERGE": "select",   # predicated merge; CGRA-ME phi is loop-carried
    "LOAD": "load",
    "LOADB": "load",
    "LOADH": "load",
    "OLOAD": "load",
    "OLOADB": "load",
    "OLOADH": "load",
    "STORE": "store",
    "STOREB": "store",
    "STOREH": "store",
    "OSTORE": "store",
    "OSTOREB": "store",
    "OSTOREH": "store",
    "MOVC": "const",
    "NOP": None,
}

# CGRA-ME wants to know which operand slot an edge feeds for these.
OPERAND = {"load": "addr"}


def parse(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    nodes, edges = {}, []
    for body_attrs, inner in NODE_RE.findall(src):
        a = dict(ATTR_RE.findall(body_attrs))
        idx = int(a["idx"])
        m = OP_RE.search(inner)
        op = m.group(1).strip() if m else ""
        const = a.get("CONST", "")
        bp = BP_RE.search(inner)
        nodes[idx] = {
            "op": op,
            "const": const,
            "bp": bp.group(1) if bp else "",
            "nextiter": {},
        }
        for ob in OUT_RE.findall(inner):
            o = dict(ATTR_RE.findall(ob))
            if "idx" not in o:
                continue
            dst = int(o["idx"])
            nodes[idx]["nextiter"][dst] = int(o.get("nextiter", "0") or 0)
            edges.append((idx, dst))
    return nodes, edges


def convert(nodes, edges, drop_recurrence=False):
    unsupported = {}
    lines = ["digraph G {"]
    for idx in sorted(nodes):
        n = nodes[idx]
        op = OPMAP.get(n["op"], "__missing__")
        if op == "__missing__":
            unsupported.setdefault(n["op"], []).append(idx)
            continue
        if op is None:
            continue
        name = "n%d" % idx
        if op == "const":
            val = n["const"] or "0"
            lines.append('    %s [opcode=const, constVal="%s"];' % (name, val))
        else:
            lines.append("    %s [opcode=%s];" % (name, op))
    for src_i, dst_i in edges:
        s, d = nodes.get(src_i), nodes.get(dst_i)
        if not s or not d:
            continue
        sop = OPMAP.get(s["op"], "__missing__")
        dop = OPMAP.get(d["op"], "__missing__")
        if sop in (None, "__missing__") or dop in (None, "__missing__"):
            continue
        if drop_recurrence and s["nextiter"].get(dst_i, 0) != 0:
            continue
        operand = OPERAND.get(dop, "any2input")
        lines.append("    n%d -> n%d [operand=%s];" % (src_i, dst_i, operand))
    lines.append("}")
    return "\n".join(lines) + "\n", unsupported


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dfg")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument(
        "--drop-recurrence",
        action="store_true",
        help="drop nextiter=1 edges instead of keeping them",
    )
    args = ap.parse_args(argv)
    nodes, edges = parse(args.dfg)
    text, unsupported = convert(nodes, edges, args.drop_recurrence)
    if unsupported:
        for op, ids in sorted(unsupported.items()):
            print(
                "unsupported op %s on %d node(s): %s" % (op, len(ids), ids[:8]),
                file=sys.stderr,
            )
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        kept = sum(1 for line in text.splitlines() if "->" in line)
        print(
            "wrote %s: %d node(s), %d edge(s), %d unsupported op kind(s)"
            % (args.out, len(nodes), kept, len(unsupported))
        )
    else:
        sys.stdout.write(text)
    return 1 if unsupported else 0


if __name__ == "__main__":
    sys.exit(main())
