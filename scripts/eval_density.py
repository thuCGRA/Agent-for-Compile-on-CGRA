"""A 臂 pin 密度消融：k ∈ {0, 25%, 50%, 100%} × 三种固定选点规则。

布局来源固定为贪心参考布局（与 LLM 无关），这样比较的是“pin 密度”本身。
k=0 即 mapper 的 `-m 0` 基线；k=100% 先 strict(1)，失败再 repair(3)；
中间档位一律 repair(3)，并记录 mapper 实际采纳的 pin 数（报告字段 pins_applied,
它是 mapper 内部各轮放置的累计成功次数，可能大于 pins_total）。
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from cgra_agent.pipeline import _ii_ladder, load_config
from cgra_agent.placers.greedy import GreedyPlacer
from cgra_agent.placers.pin_select import RULES, select as select_pins
from cgra_agent.tools.dfg import build_dfg
from cgra_agent.tools.facts import build_facts
from cgra_agent.tools.jobdir import JobDir
from cgra_agent.tools.mapper import run_mapper

DEFAULT_KERNELS = [
    "array_add", "gemm", "hpcg", "trmm", "array_cond",
    "mac", "mults1", "conv3", "conv2d", "gemm_pace",
]
RATIOS = [0.0, 0.25, 0.5, 1.0]


def run_one(repo, kernel, cfg, rule, ratio, out_root, timeout):
    job = JobDir(Path(out_root) / kernel / ("%s-%s" % (rule, ratio)))
    dfg = build_dfg(job, repo_root=repo, kernel=kernel, config=cfg, timeout=timeout)
    max_ii = int(cfg.get("max_ii") or 8)
    ladder = _ii_ladder(cfg, None, max_ii)
    rows = []
    for ii in ladder:
        facts = build_facts(dfg.dfg, dfg.arch, ii)
        if ratio <= 0.0:
            t0 = time.monotonic()
            out = run_mapper(job, repo_root=repo, dfg=dfg.dfg, arch=dfg.arch,
                             xdim=int(cfg["xdim"]), ydim=int(cfg["ydim"]), ii=ii,
                             pins=None, pins_mode=0, method=0, timeout=timeout)
            rows.append({
                "kernel": kernel, "rule": "-", "ratio": 0.0, "ii": ii,
                "mode": "baseline", "ok": out.report.ok, "stage": out.report.stage,
                "lat": out.report.lat, "pins_total": 0,
                "pin_attempts": 0, "secs": round(time.monotonic() - t0, 2),
                "error": (out.report.error or ""),
            })
            if out.report.ok:
                break
            continue

        placer = GreedyPlacer()
        try:
            placement = placer.place(facts, facts, ii, {})
        except Exception as exc:  # noqa: BLE001  greedy 在该 II 放不下，换下一个 II
            print("  greedy failed at II=%d: %s" % (ii, str(exc)[:80]), flush=True)
            continue
        keep = set(select_pins(facts, ratio, rule))
        pins = [{"node": p.node, "x": p.x, "y": p.y, "t": p.t} for p in placement.pins if p.node in keep]
        modes = [1] if ratio >= 1.0 else [3]
        if ratio >= 1.0:
            modes.append(3)
        done = False
        for mode in modes:
            pins_path = job.write_json("pins.json", {"pins": pins, "ii": ii})
            t0 = time.monotonic()
            try:
                out = run_mapper(job, repo_root=repo, dfg=dfg.dfg, arch=dfg.arch,
                                 xdim=int(cfg["xdim"]), ydim=int(cfg["ydim"]), ii=ii,
                                 pins=pins_path, pins_mode=mode, method=3, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                rows.append({
                    "kernel": kernel, "rule": rule, "ratio": ratio, "ii": ii,
                    "mode": mode, "ok": False, "stage": "exception", "lat": 0,
                    "pins_total": len(pins), "pin_attempts": 0,
                    "secs": round(time.monotonic() - t0, 2), "error": str(exc)[:120],
                })
                continue
            rows.append({
                "kernel": kernel, "rule": rule, "ratio": ratio, "ii": ii,
                "mode": mode, "ok": out.report.ok, "stage": out.report.stage,
                "lat": out.report.lat, "pins_total": out.report.pins_total,
                "pin_attempts": out.report.pins_applied,
                "secs": round(time.monotonic() - t0, 2),
                "error": (out.report.error or "")[:120],
            })
            if out.report.ok:
                done = True
                break
        if done:
            break
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernels", default=",".join(DEFAULT_KERNELS))
    ap.add_argument("--out", default="work/eval-density")
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
                    print("%-10s %-8s %4.0f%% FAILED: %s" % (kernel, rule, ratio * 100, str(exc)[:120]), flush=True)
                    continue
                all_rows.extend(rows)
                best = next((r for r in rows if r["ok"]), None)
                print(
                    "%-10s %-8s %4.0f%% -> %s"
                    % (kernel, rule, ratio * 100,
                       ("ok II=%d lat=%d %s pins=%d/%d %.1fs"
                        % (best["ii"], best["lat"], best["mode"], best["pin_attempts"],
                           best["pins_total"], time.monotonic() - started))
                       if best else "failed (%d attempts)" % len(rows)),
                    flush=True,
                )
    csv_path = os.path.join(out_root, "density.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()) if all_rows else [])
        writer.writeheader()
        writer.writerows(all_rows)
    with open(os.path.join(out_root, "density.json"), "w") as f:
        json.dump(all_rows, f, indent=2)
    print("\nwrote %s (%d rows)" % (csv_path, len(all_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
