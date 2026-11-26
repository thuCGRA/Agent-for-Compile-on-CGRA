"""Validate the generated MRRG against the mapper's own route dumps.

``*.routeInfo.log`` is written by ``PathFinderMapper::Route()`` and is the only
artefact that records the *full port chain* of a mapped edge.  If every
consecutive pair inside a ``parent:`` group is an arc of the MRRG we generate,
then the model matches the machine; if not, the model is wrong and no amount of
CP-SAT will help.

usage: validate_mrrg.py <arch.json> <routeInfo.log> [--ii N] [-v]
"""

import argparse
import re
import sys

import mrrg

PE_RE = re.compile(
    r"CGRA_Ins\.PE(?:_MEM)?_X(\d+)[|_]?_Y(\d+)[|_]?-T(\d+)\.([A-Za-z0-9_.]+)"
)
HEAD_RE = re.compile(r"^node=(\d+),mapped=PE(?:_MEM)?_X(\d+)[|_]?_Y(\d+)[|_]?-T(\d+)")


def parse_routeinfo(path):
    """Return ``[(consumer_node, [[(x, y, t, port), ...], ...]), ...]``.

    Each ``parent:N`` group is one chain, already ordered from the producer's
    output port to the consumer's sink port.
    """
    out = []
    cur_node = None
    chain = None
    with open(path) as fh:
        for line in fh:
            m = HEAD_RE.match(line)
            if m:
                if cur_node is not None and chain:
                    out.append((cur_node, chain))
                cur_node = int(m.group(1))
                chain = None
                continue
            if line.strip().startswith("parent:"):
                if chain:
                    out.append((cur_node, chain))
                chain = []
                continue
            p = PE_RE.search(line)
            if p and chain is not None:
                chain.append(
                    (int(p.group(1)), int(p.group(2)), int(p.group(3)), p.group(4))
                )
    if cur_node is not None and chain:
        out.append((cur_node, chain))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arch")
    ap.add_argument("routeinfo")
    ap.add_argument("--ii", type=int, default=4)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    nodes, arcs, meta = mrrg.build(args.arch, args.ii)
    print("MRRG:", mrrg.describe(nodes, arcs))

    chains = parse_routeinfo(args.routeinfo)
    total = 0
    missing = []
    for consumer, chain in chains:
        for a, b in zip(chain, chain[1:]):
            total += 1
            ok = False
            for bump in (0, args.ii):
                u = (a[0], a[1], (a[2] + bump) % args.ii, a[3])
                v = (b[0], b[1], (b[2] + bump) % args.ii, b[3])
                if (u, v) in arcs:
                    ok = True
                    break
            if not ok:
                missing.append((consumer, a, b))

    print("chains: %d, transitions: %d, missing: %d" % (len(chains), total, len(missing)))
    show = len(missing) if args.verbose else min(10, len(missing))
    for consumer, a, b in missing[:show]:
        print("  MISSING (consumer %s): %s -> %s" % (consumer, a, b))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
