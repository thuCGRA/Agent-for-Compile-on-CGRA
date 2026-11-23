"""Three-way comparison table: morpher's own solution vs. a placement program.

For every kernel this prints two rows:

    morpher   what the upstream mapper finds by itself. II comes from
              out/<kernel>/m0/result.txt; lat and the simulation verdict come
              from feeding that same placement back through the pinned mapper
              (so it is the ceiling measured on equal terms).
    program   the evolved program's (x, y), pinned, phase left to the mapper.

Both rows are scored with the same fitness, so the numbers are comparable.

Usage (inside ~/acc-thu, famou env):

    python evo/table.py                              # scores evo/init.py
    python evo/table.py --program famou_data/<exp>/programs/<prog>.py
    python evo/table.py --program a.py --kernel mac  # one kernel only

Requires out/<kernel>/m0/ from scripts/run_all.sh (the upstream solution) and
out/<kernel>/{dfg.xml,arch.json} for everything.
"""
from __future__ import print_function

import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evo import harness, verify_against_mapper as vam  # noqa: E402


def _cell(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "ok" if v else "no"
    if isinstance(v, float):
        return "%.3f" % v
    return str(v)


def morpher_ii(kernel):
    """The II the upstream mapper settled on, from out/<kernel>/m0/result.txt."""
    res = os.path.join(harness.root_dir(), "out", kernel, "m0", "result.txt")
    if not os.path.isfile(res):
        return None
    for line in reversed(io.open(res, errors="replace").read().splitlines()):
        parts = line.split()
        if len(parts) >= 5 and "method:" in line:
            try:
                return int(parts[-2])
            except ValueError:
                return None
    return None


def morpher_row(kernel, prob):
    """Upstream solution: its own II, then re-pinned so lat and sim are real."""
    row = {"ii": morpher_ii(kernel), "legal": None, "map_ok": None, "lat": None,
           "sim_ok": None, "matches": None, "mismatches": None, "score": 0.0}
    valid_ids = set(n["idx"] for n in prob["nodes"])
    place, sol_ii, _src = vam.find_solution(kernel, valid_ids)
    if not place:
        row["error"] = "no placement under out/%s/m* (run scripts/run_all.sh)" % kernel
        return row
    if row["ii"] is None:
        row["ii"] = sol_ii
    spatial = {i: (v[0], v[1]) for i, v in place.items()}
    verdict = harness.check_mod.check(prob, spatial, prob["ii"])
    row["legal"] = verdict["ok"]
    if not verdict["ok"]:
        row["error"] = "; ".join(verdict["errors"][:2])
        return row
    mv = harness.map_and_sim(prob, spatial, "tablem0")
    row.update({k: mv.get(k) for k in ("map_ok", "lat", "sim_ok",
                                       "matches", "mismatches")})
    row["error"] = mv.get("map_error")
    row["score"] = _score(mv)
    return row


def program_row(prob, got):
    row = {"legal": None, "map_ok": None, "lat": None, "sim_ok": None,
           "matches": None, "mismatches": None, "score": 0.0,
           "error": got.get("error")}
    if not got.get("ok"):
        return row
    verdict = harness.check_mod.check(prob, got["place"], prob["ii"])
    row["legal"] = verdict["ok"]
    if not verdict["ok"]:
        row["error"] = "; ".join(verdict["errors"][:2])
        return row
    mv = harness.map_and_sim(prob, got["place"], "tableprog")
    row.update({k: mv.get(k) for k in ("map_ok", "lat", "sim_ok",
                                       "matches", "mismatches")})
    row["error"] = mv.get("map_error")
    row["score"] = _score(mv)
    return row


def _score(mv):
    if not mv.get("map_ok"):
        return 0.0
    if not mv.get("sim_ok"):
        return harness.SCORE_MAP_ONLY
    return (harness.SCORE_MAP_ONLY +
            harness.SCORE_SIM_OK / (1.0 + float(mv.get("lat") or 0) / harness.LAT_SCALE))


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
    print("tight II per kernel: %s"
          % ", ".join("%s=%d" % (k, probs[k]["ii"]) for k in kernels))
    print("")

    head = ("kernel", "who", "II", "legal", "map", "lat", "sim",
            "matches/mismatch", "score")
    widths = (10, 8, 4, 7, 5, 5, 5, 17, 7)
    line = "  ".join(h.ljust(w) for h, w in zip(head, widths))
    print(line)
    print("-" * len(line))

    tot_m = 0.0
    tot_p = 0.0
    for k in kernels:
        m = morpher_row(k, probs[k])
        p = program_row(probs[k], results.get(k) or {})
        for who, r, tot in (("morpher", m, "m"), ("program", p, "p")):
            cells = (k, who, _cell(r.get("ii")), _cell(r.get("legal")),
                     _cell(r.get("map_ok")), _cell(r.get("lat")),
                     _cell(r.get("sim_ok")),
                     "%s/%s" % (_cell(r.get("matches")), _cell(r.get("mismatches"))),
                     _cell(r.get("score")))
            print("  ".join(c.ljust(w) for c, w in zip(cells, widths)))
            if r.get("error"):
                print("           %s" % str(r["error"])[:130])
            if tot == "m":
                tot_m += r.get("score") or 0.0
            else:
                tot_p += r.get("score") or 0.0

    n = float(len(kernels))
    print("")
    print("combined_score   morpher = %.6f   program = %.6f"
          % (tot_m / n, tot_p / n))
    print("")
    print("Both rows are scored the same way: placement -> real mapper -> real")
    print("simulator. 'morpher' is the upstream mapper's own solution, re-pinned,")
    print("so its lat is measured under the same rules and is the number to beat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
