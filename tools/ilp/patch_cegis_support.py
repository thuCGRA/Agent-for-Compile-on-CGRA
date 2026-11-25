#!/usr/bin/env python3
"""Add a `forbidden` (nogood) parameter to `ilp_mapper.build_and_solve`.

This is what makes the CEGIS loop in `tools/ilp/cegis.py` sound: when the
placement solver returns a placement that the routing model proves unroutable,
the loop feeds that *exact* placement back as a clause

    NOT (node_0 in s_0 AND node_1 in s_1 AND ...)

which removes precisely the one point from the placement space and nothing
else.  Any relaxation of that (forbidding a subset of the assignments) would be
unsound -- those slots may be fine in a different combination.

Idempotent.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
P = os.path.join(ROOT, "tools", "ilp", "ilp_mapper.py")

SIG_OLD = '''def build_and_solve(nodes, arch, ii, max_hops, time_limit, objective,
                    route_cycles_max=1, timing="bypass"):'''
SIG_NEW = '''def build_and_solve(nodes, arch, ii, max_hops, time_limit, objective,
                    route_cycles_max=1, timing="bypass", forbidden=None):'''

CAP_OLD = '''    for s in slots:
        model.Add(sum(at[(n.idx, s)] for n in nodes.values()) <= 1)
'''
CAP_NEW = '''    for s in slots:
        model.Add(sum(at[(n.idx, s)] for n in nodes.values()) <= 1)

    # CEGIS: forbid placements that the routing model has already proven
    # unroutable.  Each entry is {node_idx: (x, y, t)} covering every node, and
    # the clause removes exactly that one point from the search space.
    for fb in forbidden or []:
        lits = []
        for n in nodes.values():
            if n.idx not in fb:
                continue
            x, y, t = fb[n.idx]
            if (x, y, t) in slot_index:
                lits.append(at[(n.idx, (x, y, t))].Not())
        if lits:
            model.AddBoolOr(lits)
'''


def main():
    src = open(P, encoding="utf-8").read()
    changed = False

    if "forbidden=None" in src:
        print("  skip (already applied): signature")
    else:
        if src.count(SIG_OLD) != 1:
            print("  !! signature anchor not unique (%d)" % src.count(SIG_OLD))
            sys.exit(1)
        src = src.replace(SIG_OLD, SIG_NEW, 1)
        print("  ok: signature")
        changed = True

    if "# CEGIS: forbid placements" in src:
        print("  skip (already applied): nogood clause")
    else:
        if src.count(CAP_OLD) != 1:
            print("  !! capacity anchor not unique (%d)" % src.count(CAP_OLD))
            sys.exit(1)
        src = src.replace(CAP_OLD, CAP_NEW, 1)
        print("  ok: nogood clause")
        changed = True

    if changed:
        open(P, "w", encoding="utf-8").write(src)
    print("done")


if __name__ == "__main__":
    main()
