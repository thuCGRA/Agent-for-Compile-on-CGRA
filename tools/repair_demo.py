"""臂 B 第二轮 item 1 的可复跑演示：冲突 → 释放 → 重跑 → 仿真通过。

用法（在仓库根目录）：
    python3 tools/repair_demo.py            # 跑整张表
    python3 tools/repair_demo.py --only a   # 只跑某一行

前置：toolchain 已 build（toolchain/out/cgra_xml_mapper、hycube_simulator），
fixture 取 work/armB/spike 里的 array_add。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cgra_agent.interfaces import Pin, Placement, RouteEdge, Routes  # noqa: E402
from cgra_agent.mapper import Mapper  # noqa: E402

SPIKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "work", "armB", "spike")
DFG = "array_add_PartPredDFG.xml"
MEM = "array_add_mem.json"
TRACE = "array_add_trace_0.txt"
ALLOC = "array_add_mem_alloc.txt"


def load_placement() -> Placement:
    rep = json.load(open(os.path.join(SPIKE, "base_report.json")))
    return Placement(source="reference",
                     pins=[Pin(node=p["node"], x=p["x"], y=p["y"], t=p["t"])
                           for p in rep["placement"]])


def load_clean_routes() -> Routes:
    doc = json.load(open(os.path.join(SPIKE, "clean_set.json")))
    return Routes(edges=[RouteEdge(src=r["from"], dst=r["to"],
                                   path=[list(c) for c in r["path"]])
                         for r in doc["routes"]])


def load_reference(edge):
    doc = json.load(open(os.path.join(SPIKE, "base_routes_full.json")))
    for r in doc["routes"]:
        if (r["from"], r["to"]) == edge:
            return RouteEdge(src=r["from"], dst=r["to"],
                             path=[list(c) for c in r["path"]])
    raise KeyError(edge)


def case(name, routes, mapper):
    res, steps = mapper.repair(SPIKE, DFG, MEM, TRACE, ALLOC,
                               load_placement(), routes, verbosity=True)
    sim = None
    if res.binary:
        sim = mapper.simulate(res.binary, SPIKE, TRACE, ALLOC)
    print("%-18s ok=%-5s applied=%-4s flex=%-2s sug=%-12s bin=%-14s sim=%s" % (
        name, res.report.ok, res.report.routes_applied, res.report.flex_rerouted,
        json.dumps(res.report.suggest_edges),
        (res.binary or "-").rsplit("_", 1)[-1],
        ("Mismatches::%d" % sim.mismatches) if sim and sim.ok else
        (sim.evidence[:60] if sim else "-")))
    return name, res, sim, steps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    m = Mapper()

    todo = [
        ("A_clean19", lambda: load_clean_routes()),
        ("B_plus_6_7", lambda: Routes(load_clean_routes().edges + [load_reference((6, 7))])),
        ("B_plus_4_5", lambda: Routes(load_clean_routes().edges + [load_reference((4, 5))])),
    ]
    rows = []
    for name, build in todo:
        if args.only and not name.startswith(args.only):
            continue
        rows.append(case(name, build(), m))

    bad = [r for r in rows if not (r[1].report.ok and r[2] and r[2].ok)]
    print("\n%d/%d cases green with Mismatches::0" % (len(rows) - len(bad), len(rows)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
