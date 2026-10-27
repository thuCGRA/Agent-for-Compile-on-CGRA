"""A 臂 II=3 主实验：pin 能否把人给的结构提示塞进 PathFinder，让它从 II=4 的死局里出来。

对每个 kernel 强制 ``ii_start=3``（比基线低一档），跑
k ∈ {0, 25%, 50%} × {degree, critical}。k=0 是 mapper 自己的 ``-m 0`` 基线；
k>0 用贪心参考布局 + 固定选点规则，pin 模式 repair(3)。
指标是 **II=3 是否可映射**（成功率），lat 与 mapper 耗时作为次要项记录。

贪心布局在 II=3 放不下时记为 ``placement_not_found``：连参考布局都给不出合法相位分配，
pin 无从谈起——这本身是负结果的一半。
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from cgra_agent.pipeline import load_config
from cgra_agent.placers.greedy import GreedyPlacer
from cgra_agent.placers.pin_select import select as select_pins
from cgra_agent.tools.dfg import build_dfg
from cgra_agent.tools.facts import build_facts
from cgra_agent.tools.jobdir import JobDir
from cgra_agent.tools.mapper import run_mapper

DEFAULT_KERNELS = [
    "array_add", "gemm", "hpcg", "trmm", "array_cond",
    "mac", "mults1", "conv3", "conv2d", "gemm_pace",
]
RULES = ["degree", "critical"]
RATIOS = [0.0, 0.25, 0.5]
II = 3


def _row(kernel, rule, ratio, mode, ok, ii, stage, lat, total, attempts, secs, error):
    return {
        "kernel": kernel, "rule": rule, "ratio": ratio, "req_ii": II,
        "mode": mode, "ok": bool(ok), "ii": int(ii), "stage": stage, "lat": int(lat or 0),
        "pins_total": int(total), "pin_attempts": int(attempts),
        "secs": round(float(secs), 2), "error": (error or "")[:120],
    }


def run_one(repo, kernel, cfg, rule, ratio, out_root, timeout):
    job = JobDir(Path(out_root) / kernel / ("%s-%s" % (rule, ratio)))
    dfg = build_dfg(job, repo_root=repo, kernel=kernel, config=cfg, timeout=timeout)
    facts = build_facts(dfg.dfg, dfg.arch, II)

    if ratio <= 0.0:
        t0 = time.monotonic()
        out = run_mapper(job, repo_root=repo, dfg=dfg.dfg, arch=dfg.arch,
                         xdim=int(cfg["xdim"]), ydim=int(cfg["ydim"]), ii=II,
                         pins=None, pins_mode=0, method=0, timeout=timeout)
        return [_row(kernel, "-", 0.0, "baseline", out.report.ok, out.report.ii, out.report.stage,
                     out.report.lat, 0, 0, time.monotonic() - t0, out.report.error)]

    t0 = time.monotonic()
    try:
        placement = GreedyPlacer(seed=0).place(facts, facts, II, {})
    except Exception as exc:  # noqa: BLE001 参考布局在 II=3 不可行
        return [_row(kernel, rule, ratio, "placement_not_found", False, II,
                     "placement_not_found", 0, 0, 0,
                     time.monotonic() - t0, str(exc))]

    keep = set(select_pins(facts, ratio, rule))
    pins = [{"node": p.node, "x": p.x, "y": p.y, "t": p.t}
            for p in placement.pins if p.node in keep]
    if not pins:
        return [_row(kernel, rule, ratio, "empty_pins", False, II, "empty_pins", 0, 0, 0,
                     time.monotonic() - t0, "selection produced no pins")]
    pins_path = job.write_json("pins.json", {"pins": pins, "ii": II})
    t1 = time.monotonic()
    out = run_mapper(job, repo_root=repo, dfg=dfg.dfg, arch=dfg.arch,
                     xdim=int(cfg["xdim"]), ydim=int(cfg["ydim"]), ii=II,
                     pins=pins_path, pins_mode=3, method=3, timeout=timeout)
    return [_row(kernel, rule, ratio, "repair", out.report.ok, out.report.ii, out.report.stage,
                 out.report.lat, out.report.pins_total, out.report.pins_applied,
                 time.monotonic() - t1, out.report.error)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernels", default=",".join(DEFAULT_KERNELS))
    ap.add_argument("--out", default="work/eval-ii")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--repo-root", default="/home/steve/cac-armA")
    args = ap.parse_args()

    repo = args.repo_root
    out_root = os.path.join(repo, args.out)
    os.makedirs(out_root, exist_ok=True)
    all_rows = []
    for kernel in [k.strip() for k in args.kernels.split(",") if k.strip()]:
        cfg = load_config(repo, kernel)
        for rule in RULES:
            for ratio in RATIOS:
                started = time.monotonic()
                try:
                    rows = run_one(repo, kernel, cfg, rule, ratio, out_root, args.timeout)
                except Exception as exc:  # noqa: BLE001
                    print("%-10s %-8s %4.0f%% ERROR: %s"
                          % (kernel, rule, ratio * 100, str(exc)[:120]), flush=True)
                    continue
                all_rows.extend(rows)
                r = rows[0]
                print("  %-10s %-8s %4.0f%% -> %-20s II=%d lat=%s %.1fs"
                      % (kernel, rule, ratio * 100,
                         ("ok" if r["ok"] else r["stage"]), r["ii"],
                         r["lat"] or "-", time.monotonic() - started), flush=True)
    csv_path = os.path.join(out_root, "ii3.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()) if all_rows else [])
        writer.writeheader()
        writer.writerows(all_rows)
    with open(os.path.join(out_root, "ii3.json"), "w") as f:
        json.dump(all_rows, f, indent=2)
    print("\nwrote %s (%d rows)" % (csv_path, len(all_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
