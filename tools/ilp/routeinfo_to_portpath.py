"""routeInfo.log -> --port-path file.

Self-test for the PORTPATH hook: replay the mapper's own route through the new
path and the emitted bitstream must be byte-identical to the baseline.  Any
mismatch means either resolvePort() picked the wrong Port object or the
absolute-latency reconstruction disagreed with what assignPath recorded.

routeInfo.log layout (one block per consumer)::

    node=5,mapped=PE_MEM_X0_Y2-T0
        parent:3
            CGRA_Ins.PE_MEM_X0_Y1-T2.FU0.DP0_T
            CGRA_Ins.PE_X1|_Y1|-T2.EAST_O
            ...
        parent:6
            CGRA_Ins.PE_X1|_Y2|-T3.FU0.DP0.I1

The chain runs from the producer's output port to the consumer's input port.
"""

import io
import re
import sys

HEAD_RE = re.compile(r"^node=(\d+),mapped=PE(?:_MEM)?_X(\d+)[|_]?_Y(\d+)[|_]?-T(\d+)")
PARENT_RE = re.compile(r"^\s*parent:(\d+)")
PORT_RE = re.compile(
    r"CGRA_Ins\.PE(?:_MEM)?_X(\d+)[|_]?_Y(\d+)[|_]?-T(\d+)\.([A-Za-z0-9_.]+)")


def parse(path):
    """-> [(src, dst, [(x, y, t, port), ...]), ...]"""
    edges = []
    dst = None
    src = None
    chain = []

    def flush():
        if dst is not None and src is not None and chain:
            edges.append((src, dst, list(chain)))

    for raw in io.open(path, encoding="utf-8", errors="replace"):
        line = raw.rstrip("\n")
        m = HEAD_RE.match(line)
        if m:
            flush()
            chain = []
            src = None
            dst = int(m.group(1))
            continue
        m = PARENT_RE.match(line)
        if m:
            flush()
            chain = []
            src = int(m.group(1))
            continue
        m = PORT_RE.search(line)
        if m:
            chain.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)))
    flush()
    return edges


def main():
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    edges = parse(args.log)
    if not edges:
        sys.exit("no edges parsed from %s" % args.log)

    out = io.open(args.out, "w", encoding="utf-8")
    out.write("# src dst nsteps x y t port ...\n")
    for src, dst, chain in edges:
        parts = [str(src), str(dst), str(len(chain))]
        for (x, y, t, port) in chain:
            parts += [str(x), str(y), str(t), port]
        out.write(" ".join(parts) + "\n")
    out.close()
    print("port-path: %d edge(s), %d step(s) -> %s"
          % (len(edges), sum(len(c) for _, _, c in edges), args.out))


if __name__ == "__main__":
    main()
