"""Same-II success-rate table from the pin-density run (no new mapper calls).

Rows come from ``work/eval-density/density.json``; all numbers are "as executed":
the mapper is asked for II=4 but floors it at RecMin II, so ``target_ii`` is the
II the mapper actually used (4 for most kernels, 9 for gemm_pace -- read from
the ``Initial target II`` line in each ``mapper.log``).

A 100% cell runs strict first (first call) and repair after (second call); the
``first_call_ok`` column is therefore the strict result, not "eventually ok".

Outputs ``same_ii.csv`` (one row per kernel×rule×k) and prints a matrix.
"""

from __future__ import annotations

import json
import os
import sys

KERNELS = ["array_add", "gemm", "hpcg", "trmm", "array_cond", "mac", "mults1", "conv3", "conv2d", "gemm_pace"]
RULES = ["degree", "critical", "bank"]
KS = [0.25, 0.5, 1.0]
# Read from `grep "Initial target II" work/eval-density/<k>/degree-0.0/mapper.log`.
TARGET_II = {k: 4 for k in KERNELS}
TARGET_II["gemm_pace"] = 9
REQUEST_II = 4


def load(path: str) -> list:
    with open(path, "r") as f:
        return json.load(f)


def rows_for(rows: list, kernel: str, rule: str, ratio: float) -> list:
    return [
        r for r in rows
        if r["kernel"] == kernel and r["rule"] == rule and float(r["ratio"]) == ratio
        and int(r["ii"]) == REQUEST_II
    ]


def summarize(rows: list) -> dict:
    """One cell: mappability, lat, pins, cost, failure stage."""
    if not rows:
        return {"calls": 0, "strict_ok": None, "repair_ok": None,
                "first_ok": False, "lat": 0, "pins_applied": 0, "pins_total": 0,
                "secs": 0.0, "stage": "", "error": "no placement attempt at II=%d" % REQUEST_II}
    first = rows[0]
    strict = [r for r in rows if r.get("mode") == 1]
    repair = [r for r in rows if r.get("mode") == 3]
    ok_rows = [r for r in rows if r["ok"]]
    best = ok_rows[0] if ok_rows else None
    fail = next((r for r in rows if not r["ok"]), None)
    return {
        "calls": len(rows),
        "strict_ok": (bool(strict and strict[0]["ok"]) if strict else None),
        "repair_ok": (bool(repair and repair[0]["ok"]) if repair else None),
        "first_ok": bool(first["ok"]),
        "lat": int(best["lat"]) if best else 0,
        "pins_applied": int((best or first).get("pins_applied")
                            or (best or first).get("pin_attempts") or 0),
        "pins_total": int((best or first).get("pins_total") or 0),
        "secs": round(sum(float(r.get("secs") or 0.0) for r in rows), 2),
        "stage": str((fail or {}).get("stage") or ""),
        "error": str((fail or {}).get("error") or "")[:200],
    }


def cell_str(c: dict, with_lat: bool = False) -> str:
    if c["calls"] == 0:
        return "—"
    ok = c["repair_ok"] if c["repair_ok"] is not None else c["first_ok"]
    s = "%s/%d" % ("ok" if ok else "FAIL", c["calls"])
    if with_lat and ok:
        s += " (lat %d)" % c["lat"]
    return s


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(here, "work", "eval-density", "density.json")
    out_csv = os.path.join(here, "work", "eval-density", "same_ii.csv")
    rows = load(src)

    cells = {}
    for kernel in KERNELS:
        cells[(kernel, "-", 0.0)] = summarize(rows_for(rows, kernel, "-", 0.0))
        for rule in RULES:
            for k in KS:
                cells[(kernel, rule, k)] = summarize(rows_for(rows, kernel, rule, k))

    header = ["kernel", "rule", "k", "target_ii", "calls", "strict_ok", "repair_ok",
              "first_call_ok", "lat", "pins_applied", "pins_total",
              "mapper_time_s", "failure_stage"]
    with open(out_csv, "w") as f:
        f.write(",".join(header) + "\n")
        for kernel in KERNELS:
            for rule, k in [("-", 0.0)] + [(r, x) for r in RULES for x in KS]:
                c = cells[(kernel, rule, k)]
                f.write(",".join(str(x) for x in [
                    kernel, rule, k, TARGET_II[kernel], c["calls"],
                    {True: "yes", False: "no", None: "-"}[c["strict_ok"]],
                    {True: "yes", False: "no", None: "-"}[c["repair_ok"]],
                    "yes" if c["first_ok"] else "no",
                    c["lat"] or "-", c["pins_applied"], c["pins_total"],
                    "%.2f" % c["secs"], c["stage"] or "-",
                ]) + "\n")

    # Compact matrix: rows kernels, 9 columns (rule×k), plus baseline.
    print("baseline:")
    for kernel in KERNELS:
        c = cells[(kernel, "-", 0.0)]
        print("  %-10s II=%d %s lat=%s pins=%d/%d %.2fs" % (
            kernel, TARGET_II[kernel], cell_str(c), c["lat"], c["pins_applied"],
            c["pins_total"], c["secs"]))
    print("\nmatrix (ok/calls, first-call ok in parens when strict failed):")
    head = ["kernel"] + ["%s-%.0f%%" % (r, k * 100) for r in RULES for k in KS]
    print(",".join(head))
    for kernel in KERNELS:
        line = [kernel]
        for rule in RULES:
            for k in KS:
                c = cells[(kernel, rule, k)]
                s = cell_str(c)
                if c["calls"] and not c["first_ok"]:
                    s += " (first FAIL)"
                line.append(s)
        print(",".join(line))

    print("\n# per-rule curve")
    for rule in RULES:
        parts = []
        for k in KS:
            att = [cells[(kernel, rule, k)] for kernel in KERNELS if cells[(kernel, rule, k)]["calls"]]
            ok = sum(1 for c in att if (c["repair_ok"] if c["repair_ok"] is not None else c["first_ok"]))
            first = sum(1 for c in att if c["first_ok"])
            parts.append("%.0f%%: %d/%d ok (%d first)" % (k * 100, ok, len(att), first))
        print("%s: %s" % (rule, " | ".join(parts)))
    print("\nwrote %s" % out_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
