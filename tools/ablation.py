"""覆盖率消融：固定布局，只让"由策略接管的边数"变化（k = 0/25/50/100%）。

对照 `critical` vs `random` 两种选边规则：如果 critical 明显好于 random，说明
"选哪条边"本身有信息；如果两者一样，收益只跟覆盖率有关，跟选边能力无关。

用法（仓库根目录）：
    python3 tools/ablation.py                  # 全矩阵
    python3 tools/ablation.py --ks 0,100       # 只跑两端
    python3 tools/ablation.py --no-sim         # 不跑仿真
    python3 tools/ablation.py --path-mode shortest

产物：work/armB/ablation/ablation.csv 与 ablation.md
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cgra_agent.interfaces import Pin, Placement   # noqa: E402
from cgra_agent.mapper import Mapper              # noqa: E402
from cgra_agent.routers import RulePolicy, load_bank  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SPIKE = os.path.join(ROOT, "work", "armB", "spike")
OUT = os.path.join(ROOT, "work", "armB", "ablation")
DFG = "array_add_PartPredDFG.xml"
MEM = "array_add_mem.json"
TRACE = "array_add_trace_0.txt"
ALLOC = "array_add_mem_alloc.txt"
ARCH = {"x": 4, "y": 4, "ii": 4, "name": "HyCUBE_4REG"}


def parse_dfg(path: str):
    """极简 PartPredDFG 解析：<Input idx> -> 所在 <Node idx>。"""
    text = open(path).read()
    edges, seen = [], set()
    for blk in re.findall(r"<Node\b[^>]*>.*?</Node>", text, re.S):
        m = re.search(r'idx="(\d+)"', blk)
        if not m:
            continue
        dst = int(m.group(1))
        for i in re.findall(r'<Input idx="(\d+)"', blk):
            e = (int(i), dst)
            if e not in seen:
                seen.add(e)
                edges.append(e)
    return edges


def load_placement() -> Placement:
    rep = json.load(open(os.path.join(SPIKE, "base_report.json")))
    return Placement(source="fixed",
                     pins=[Pin(node=p["node"], x=p["x"], y=p["y"], t=p["t"])
                           for p in rep["placement"]])


def facts_from(edges):
    rep = json.load(open(os.path.join(SPIKE, "base_report.json")))
    lat = {p["node"]: p["lat"] for p in rep["placement"]}
    pos = {p["node"]: [p["x"], p["y"]] for p in rep["placement"]}
    return {
        "paths": {"workdir": SPIKE, "dfg": DFG, "arch_json": MEM,
                  "mem_alloc": ALLOC, "trace": TRACE},
        "edges": [list(e) for e in edges],
        "lat": lat, "pos": pos, "arch": ARCH,
    }


def run_one(mapper, facts, placement, coverage, rule, seed, sim, path_mode):
    policy = RulePolicy(mapper, path_mode=path_mode, seed=seed, max_rounds=3,
                        bank=load_bank(os.path.join(SPIKE, "base_routes_full.json"))
                        if path_mode == "bank" else None)
    budget = {"coverage": coverage, "rule": rule, "max_rounds": 3, "timeout_s": 300}
    t0 = time.time()
    rep = policy.route(placement, facts, ARCH, ARCH["ii"], budget)
    rounds = rep.extra.get("rounds", [])
    r0 = rounds[0] if rounds else {}
    row = {
        "k_pct": int(round(coverage * 100)),
        "rule": rule,
        "seed": seed if rule == "random" else 0,
        "path_mode": path_mode,
        "ok": rep.ok,
        "ii": rep.ii,
        "lat_verify": rep.lat,
        "lat_max_pin": max(facts["lat"].values()) if facts["lat"] else 0,
        "conf_round0": len(r0.get("suggest", [])),
        "conf_final": len(rep.conflicts),
        "conflict_types": ",".join(sorted({c["type"] for c in rep.conflicts})),
        "routes_proposed": rep.extra["proposed"],
        "routes_accepted": rep.extra["accepted"],
        "routes_rejected": rep.extra["rejected"],
        "edges_total": rep.extra["edges_total"],
        "verify_rounds": rep.extra["verify_rounds"],
        "wall_s": rep.extra["wall_s"],
        "failure_class": rep.failure_class,
        "mismatches": "",
        "sim": "skipped",
        "bin_sha8": (rep.binary_sha256 or "")[:8],
        "reason_round0": (r0.get("reason") or "")[:140],
        "released_round0": ",".join(r0.get("flex", [])),
    }
    if sim and rep.binary:
        s = mapper.simulate(rep.binary, SPIKE, TRACE, ALLOC)
        row["sim"] = "ok" if s.ok else "fail"
        row["mismatches"] = s.mismatches
        row["failure_class"] = ("semantic" if s.mismatches else
                                "runtime" if s.failure_class == "runtime" else
                                row["failure_class"])
    elif sim:
        row["sim"] = "no-binary"
    return row, rep, round(time.time() - t0, 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ks", default="0,25,50,100")
    ap.add_argument("--rules", default="critical,random")
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--path-mode", default="bank", choices=["bank", "shortest"])
    ap.add_argument("--no-sim", action="store_true")
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    mapper = Mapper(home=os.path.abspath(ROOT))
    edges = parse_dfg(os.path.join(SPIKE, DFG))
    facts = facts_from(edges)
    placement = load_placement()
    print("edges=%d pinned=%d path_mode=%s" % (len(edges), len(placement.pins),
                                               a.path_mode))

    rows = []
    for ks in a.ks.split(","):
        cov = float(ks) / 100.0
        for rule in a.rules.split(","):
            seeds = [int(s) for s in a.seeds.split(",")] if rule == "random" else [0]
            for seed in seeds:
                row, rep, total = run_one(mapper, facts, placement, cov, rule,
                                          seed, not a.no_sim, a.path_mode)
                rows.append(row)
                print("k=%-3d %-8s s=%-2d ok=%-5s II=%d lat=%d c0=%-2d acc=%2d/%-2d "
                      "r=%d %5.2fs %-8s %-8s %s" % (
                          row["k_pct"], rule, seed, row["ok"], row["ii"],
                          row["lat_verify"], row["conf_round0"],
                          row["routes_accepted"], row["routes_proposed"],
                          row["verify_rounds"], total, row["sim"],
                          row["bin_sha8"], row["failure_class"]))

    with open(os.path.join(OUT, "ablation.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    md = ["# 覆盖率消融（array_add，布局固定，path_mode=%s）" % a.path_mode, "",
          "| k% | 选边 | seed | ok | II | lat | 首轮冲突 | 接受/提出 | 首轮释放 | 轮数 | 耗时 | bin | 仿真 | failure_class |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append("| %d | %s | %s | %s | %d | %d | %d | %d/%d | %s | %d | %.2fs | %s | %s | %s |" % (
            r["k_pct"], r["rule"], r["seed"] or "-", r["ok"], r["ii"],
            r["lat_verify"], r["conf_round0"], r["routes_accepted"],
            r["routes_proposed"], r["released_round0"] or "-",
            r["verify_rounds"], r["wall_s"], r["bin_sha8"], r["sim"],
            r["failure_class"] or "-"))
    open(os.path.join(OUT, "ablation.md"), "w").write("\n".join(md) + "\n")
    print("wrote", os.path.join(OUT, "ablation.csv"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
