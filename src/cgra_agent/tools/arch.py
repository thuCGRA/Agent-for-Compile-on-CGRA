"""Architecture variants for the HyCUBE ADL.

The vendor ``mem.json`` describes a parametric GRID: one SPM-sharing PE column
(``X=0, MOD=PE_MEM``) plus plain PEs, and the GRID pattern derives the wiring
from ``DIMS`` and ``MODS``.  Subsetting therefore only means: rewrite ``DIMS``,
keep the ``MODS`` that still fit, and drop the explicit ``CGRA.CONNECTIONS``
block (the parser asserts on its ``dest_mod`` entries once rows disappear).

Bank count is *not* changed here on purpose.  Each ``SPM_B<i>_WRAPPER`` is tied
to one PE_MEM row by the GRID pattern, so resizing the grid without re-wiring
banks silently produces an architecture where a bank has no PE or a PE has no
bank.  When the grid is too small for the bank count we refuse rather than emit
something the mapper will mis-read.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict


def make_arch(src_path: str, xdim: int, ydim: int, out_path: str) -> Dict[str, Any]:
    """Write an ``xdim`` x ``ydim`` subset of *src_path* to *out_path*."""
    if xdim < 1 or ydim < 1:
        raise ValueError("xdim/ydim must be positive, got %dx%d" % (xdim, ydim))
    d = json.load(open(src_path))
    arch = d["ARCH"]
    cgra = arch["CGRA"]
    grid = cgra["SUBMODS"][0]
    if grid.get("PATTERN") != "GRID":
        raise ValueError("expected a GRID pattern, got %r" % grid.get("PATTERN"))

    mods = [m for m in grid["MODS"] if m["X"] < xdim and m["Y"] < ydim]
    if not mods:
        raise ValueError("no MODS survive %dx%d" % (xdim, ydim))

    banks = sorted(n for n in arch if n.startswith("SPM_B") and n.endswith("_WRAPPER"))
    rows = sorted({m["Y"] for m in mods if m["MOD"] == "PE_MEM"})
    if banks and len(rows) < len(banks):
        raise ValueError(
            "%d banks need %d PE_MEM rows, %dx%d only keeps %d"
            % (len(banks), len(banks), xdim, ydim, len(rows)))

    grid["DIMS"] = {"X": int(xdim), "Y": int(ydim)}
    grid["MODS"] = mods
    cgra.pop("CONNECTIONS", None)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    json.dump(d, open(out_path, "w"), indent=1)
    return {"path": out_path, "xdim": xdim, "ydim": ydim,
            "mods": len(mods), "pe_mem_rows": rows, "banks": len(banks)}


def assert_consistent(arch_path: str) -> None:
    """Fail loudly when a variant has a bank the grid cannot host."""
    d = json.load(open(arch_path))
    arch = d["ARCH"]
    grid = arch["CGRA"]["SUBMODS"][0]
    dims, mods = grid["DIMS"], grid["MODS"]
    banks = [n for n in arch if n.startswith("SPM_B") and n.endswith("_WRAPPER")]
    rows = sorted({m["Y"] for m in mods if m["MOD"] == "PE_MEM"})
    if len(rows) < len(banks):
        raise AssertionError("%s: %d banks vs %d PE_MEM rows"
                             % (arch_path, len(banks), len(rows)))
    if any(m["X"] >= dims["X"] or m["Y"] >= dims["Y"] for m in mods):
        raise AssertionError("%s: MODS outside %r" % (arch_path, dims))
