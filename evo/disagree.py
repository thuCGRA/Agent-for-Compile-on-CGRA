"""Compare our static verdict against the real mapper and the real simulator.

The point of this script is the *disagreement*, not the score. Per kernel it
prints three independent verdicts side by side:

    check.py    our static model: does it satisfy the spatial hard constraints?
    mapper      morpher's PinnedMapper: can the real mapper schedule it?
    simulator   hycube_simulator: Matches / Mismatches

`check` saying yes while `mapper` says no means our static model is missing a
real constraint; `mapper` saying yes while the simulator reports mismatches
means the mapper's own solution is functionally wrong (which happens -- see
docs in evo/README.md).

A fourth verdict used to live here: `router.py`, a time-expanded-graph router of
ours. It was removed because it allowed "wait one cycle in the PE register"
without consuming a link, which made it call 8 of 8 placements routable while
the real mapper accepted 3 of 8. It is in git history if the question ever comes
back; it is not a usable gate.

Usage (inside ~/acc-thu, famou env):

    python evo/disagree.py                     # evo/init.py
    python evo/disagree.py --program <prog>.py
    python evo/disagree.py --kernel mac --kernel trmm
"""
from __future__ import print_function

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evo import check as check_mod  # noqa: E402
from evo import harness  # noqa: E402


def _cell(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "ok" if v else "NO"
    return str(v)


def row(prob, got):
    """Run the three verdicts on one kernel's placement."""
    out = {"check": None, "mapper": None, "sim": None, "lat": None,
           "matches": None, "mismatches": None, "flag": ""}
    if not got or not got.get("ok"):
        out["flag"] = "no placement"
        return out

    place = got["place"]
    verdict = check_mod.check(prob, place, prob["ii"])
    out["check"] = verdict["ok"]
    if not verdict["ok"]:
        out["flag"] = "; ".join(verdict["errors"][:2])
        return out

    mv = harness.map_and_sim(prob, place, "disagree")
    out["mapper"] = mv.get("map_ok")
    out["lat"] = mv.get("lat")
    out["sim"] = mv.get("sim_ok")
    out["matches"] = mv.get("matches")
    out["mismatches"] = mv.get("mismatches")

    if out["mapper"] and out["sim"] is False:
        out["flag"] = "mapped but functionally wrong"
    elif out["mapper"] and out["sim"] is None:
        out["flag"] = "mapper ok, sim not measured"
    elif not out["mapper"]:
        out["flag"] = "static ok, mapper says no"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "init.py"))
    ap.add_argument("--kernel", action="append", default=None)
    args = ap.parse_args(argv)

    kernels = tuple(args.kernel) if args.kernel else harness.KERNELS
    probs = harness.load_problems(kernels=kernels)
    results = harness.run_program(args.program, probs)

    print("program: %s" % args.program)
    print("")
    head = ("kernel", "check", "mapper", "lat", "sim", "matches/mismatch", "note")
    widths = (10, 6, 7, 5, 5, 17, 30)
    line = "  ".join(h.ljust(w) for h, w in zip(head, widths))
    print(line)
    print("-" * len(line))

    flags = 0
    for k in kernels:
        r = row(probs[k], results.get(k) or {})
        cells = (k, _cell(r["check"]), _cell(r["mapper"]), _cell(r["lat"]),
                 _cell(r["sim"]),
                 "%s/%s" % (_cell(r["matches"]), _cell(r["mismatches"])),
                 r["flag"])
        print("  ".join(c.ljust(w) for c, w in zip(cells, widths)))
        if r["flag"] and r["flag"] != "mapper ok, sim not measured":
            flags += 1

    print("")
    print("%d disagreement(s). 'static ok, mapper says no' means check.py is" % flags)
    print("missing a real constraint; 'mapped but functionally wrong' means the")
    print("mapper accepted a placement the hardware cannot execute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
