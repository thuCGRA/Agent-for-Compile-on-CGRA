#!/usr/bin/env python
"""Map-level evaluation: arm A (pinned) vs method 0 (PathFinder, no pins).

No trace is needed: both paths stop after S5 ``map``.  The A arm is driven by
``GreedyPlacer`` here only because a live LLM endpoint is not part of the
evaluation; the placement is therefore a *reference*, not a model quality
claim -- see ARM.md.

    python scripts/eval_map.py --kernels array_add,gemm,fft,hpcg,trmm,array_cond

Writes work/eval/eval-<stamp>/summary.json and prints a markdown table.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

from cgra_agent.pipeline import Pipeline, _ii_ladder, load_config  # noqa: E402
from cgra_agent.tools.dfg import build_dfg  # noqa: E402
from cgra_agent.tools.facts import build_facts  # noqa: E402
from cgra_agent.tools.jobdir import JobDir, ToolError  # noqa: E402
from cgra_agent.tools.mapper import run_mapper  # noqa: E402


def baseline(repo: str, kernel: str, cfg: dict, out_root: str) -> dict:
    """First II in the ladder where method 0 (PathFinder, no pins) maps."""
    max_ii = int(cfg.get("max_ii") or 8)
    ladder = _ii_ladder(cfg, None, max_ii)
    dfg_job = JobDir(os.path.join(out_root, "dfg", kernel))
    t0 = time.monotonic()
    try:
        dfg = build_dfg(dfg_job, repo_root=repo, kernel=kernel, config=cfg, timeout=300)
    except ToolError as exc:
        return {"ok": False, "error": "dfg: %s" % exc}
    facts = build_facts(dfg.dfg, dfg.arch, ladder[0])
    for ii in ladder:
        job = JobDir(os.path.join(out_root, "map", kernel, "ii-%d" % ii))
        try:
            out = run_mapper(
                job, repo_root=repo, dfg=dfg.dfg, arch=dfg.arch,
                xdim=int(cfg.get("xdim") or 4), ydim=int(cfg.get("ydim") or 4),
                ii=ii, pins=None, pins_mode=0, method=0, timeout=600,
            )
        except ToolError as exc:
            return {"ok": False, "error": "mapper: %s" % exc}
        if out.report.ok:
            return {
                "ok": True,
                "ii": out.report.ii or ii,
                "lat": out.report.lat,
                "secs": round(time.monotonic() - t0, 2),
                "nodes": facts["n_nodes"],
                "edges": facts["n_edges"],
                "res_min_ii": facts["res_min_ii"],
            }
    return {"ok": False, "error": "no II in ladder %s mapped" % ladder,
            "secs": round(time.monotonic() - t0, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=REPO)
    ap.add_argument("--kernels", default="array_add,gemm,fft,hpcg,trmm,array_cond")
    ap.add_argument("--out-root", default="work/eval")
    ap.add_argument("--pins-mode", type=int, default=None, choices=[1, 2, 3])
    args = ap.parse_args()
    repo = os.path.abspath(args.repo_root)
    out_root = os.path.join(repo, args.out_root)

    rows = []
    for kernel in [k.strip() for k in args.kernels.split(",") if k.strip()]:
        cfg = load_config(repo, kernel)
        t0 = time.monotonic()
        try:
            out = Pipeline(
                repo_root=repo, kernel=kernel, placer="greedy",
                out_root=os.path.join(args.out_root, "runs"), verbose=False,
                pins_mode=args.pins_mode,
            ).run()
            run = out.run or {}
        except ToolError as exc:
            print("%-12s pipeline error: %s" % (kernel, exc))
            rows.append({"kernel": kernel, "arm": {"ok": False, "error": str(exc)}})
            continue
        res = run.get("result") or {}
        facts_meta = run.get("facts") or {}
        last_map = {}
        for att in reversed(run.get("attempts") or []):
            if att.get("map"):
                last_map = att["map"]
                break
        base = baseline(repo, kernel, cfg, out_root)
        row = {
            "kernel": kernel,
            "nodes": facts_meta.get("n_nodes"),
            "edges": facts_meta.get("n_edges"),
            "res_min_ii": facts_meta.get("res_min_ii"),
            "arm": {
                "ok": out.status in ("ok", "unverified"),
                "status": out.status,
                "ii": res.get("ii") or run.get("ii", {}).get("final"),
                "lat": res.get("lat"),
                "pins_applied": last_map.get("pins_applied"),
                "sim": res.get("sim") or "verified",
                "secs": round(time.monotonic() - t0, 2),
                "run_dir": out.run_dir,
            },
            "baseline": base,
        }
        rows.append(row)
        print("%-12s A: II=%s lat=%s %s | base: II=%s lat=%s" % (
            kernel, row["arm"]["ii"], row["arm"]["lat"], row["arm"]["status"],
            base.get("ii"), base.get("lat")))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(out_root, "eval-%s" % stamp)
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, "summary.json")
    with open(path, "w") as f:
        json.dump(rows, f, indent=2)
        f.write("\n")

    print("\n| kernel | nodes | edges | ResMII | A II | A lat | A status | base II | base lat |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        a, b = r.get("arm") or {}, r.get("baseline") or {}
        print("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["kernel"], r.get("nodes"), r.get("edges"), r.get("res_min_ii"),
            a.get("ii", "-"), a.get("lat", "-"), a.get("status", "-"),
            b.get("ii", "-"), b.get("lat", "-")))
    print("\nsummary: %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
