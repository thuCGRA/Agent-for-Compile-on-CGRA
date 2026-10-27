"""RulePolicy：确定性布线策略（消融的对照组）。

两步走，两步都能单独调：
  routes(...)  只产出 Routes —— 纯策略，不碰 mapper，便于单测与对照；
  route(...)   = routes(...) + mapper verify + 冲突释放（flex）循环 + 落 bitstream。

path_mode：
  "bank"     用 A* 自己布的路径（默认）。消融要回答"接管布线的决策权值多少"，
             路径质量保持不变，critical / random 的差别才干净。
  "shortest" 确定性最短路 + 软端口避让（外行规则的真实水平）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from ..interfaces import MapReport, Placement, RouteEdge, Routes
from .base import Edge, select_edges, shortest_path


def paths_of(facts: dict) -> dict:
    """facts["paths"] 归一化：A 臂定稿用 arch_json/mem_alloc，孤立调用用 mem/trace/alloc。

    两种都接受，避免为了一个键名去改别人的契约。
    """
    p = facts.get("paths", {})
    mem = p.get("mem") or p.get("mem_json") or p.get("arch_json") or ""
    alloc = p.get("alloc") or p.get("mem_alloc") or ""
    return {"workdir": p.get("workdir", "."), "dfg": p.get("dfg", ""),
            "mem": mem, "trace": p.get("trace", ""), "alloc": alloc}


class RulePolicy:
    name = "rule"

    def __init__(self, mapper, path_mode: str = "bank",
                 bank: Optional[Dict[Edge, List[List[int]]]] = None,
                 seed: int = 1, max_rounds: int = 3, verbose: bool = False):
        self.mapper = mapper
        self.path_mode = path_mode
        self.bank = bank or {}
        self.seed = seed
        self.max_rounds = max_rounds
        self.verbose = verbose

    # ------------------------------------------------------------ 纯策略

    def routes(self, placement: Placement, facts: dict, arch: dict,
               ii: int, coverage: float, rule: str) -> Routes:
        lat = facts["lat"]
        edges = [tuple(e) for e in facts["edges"]]
        k = int(round(coverage * len(edges)))
        chosen = select_edges(edges, rule, k, lat, self.seed)

        routes = Routes()
        self.unpathed: List[Edge] = []
        avoid: Dict[Tuple[int, int, int, int], int] = {}
        for e in chosen:
            path = self.bank.get(e) if self.path_mode == "bank" else None
            if path is None and self.path_mode == "shortest":
                pos = facts["pos"]
                a, b = pos.get(e[0]), pos.get(e[1])
                agent = facts.get("arch", {}).get("x", 4), facts.get("arch", {}).get("y", 4)
                if a and b:
                    path = shortest_path(tuple(a), tuple(b), agent[0], agent[1], avoid)
                    if path:
                        for i in range(len(path) - 1):
                            avoid[(path[i][0], path[i][1],
                                   path[i + 1][0], path[i + 1][1])] = 1
            if path is None:
                self.unpathed.append(e)
                continue
            routes.edges.append(RouteEdge(src=e[0], dst=e[1],
                                          path=[list(c) for c in path]))
        return routes

    # ------------------------------------------------------------ 完整调用

    def route(self, placement: Placement, facts: dict, arch: dict,
              ii: int, budget: dict) -> MapReport:
        t0 = time.time()
        coverage = float(budget.get("coverage", 1.0))
        rule = budget.get("rule", "critical")
        routes = self.routes(placement, facts, arch, ii, coverage, rule)

        p = paths_of(facts)
        res, steps = self.mapper.repair(
            p["workdir"], p["dfg"], p["mem"], p["trace"], p["alloc"],
            placement, routes,
            max_rounds=budget.get("max_rounds", self.max_rounds),
            verbosity=self.verbose)

        rep = res.report
        rep.failure_class = "" if rep.ok else "static"
        rep.binary = res.binary or ""
        rep.binary_sha256 = ""
        if rep.binary and os.path.exists(rep.binary):
            rep.binary_sha256 = hashlib.sha256(
                open(rep.binary, "rb").read()).hexdigest()
        rep.routes_rejected = max(0, len(routes.edges) - rep.routes_applied)

        reasons: List[str] = []
        for c in rep.conflicts:
            e = c.get("suggest", {}).get("edge")
            if e:
                reasons.append("(%d,%d) %s: %s" % (e[0], e[1], c["type"], c["msg"][:120]))
        rep.extra = {
            "policy": self.name, "rule": rule, "coverage": coverage,
            "seed": self.seed, "path_mode": self.path_mode,
            "edges_total": len(facts["edges"]),
            "proposed": len(routes.edges),
            "unpathed": ["(%d,%d)" % e for e in getattr(self, "unpathed", [])],
            "accepted": rep.routes_applied,
            "rejected": rep.routes_rejected,
            "reject_reasons": reasons[:10],
            "verify_rounds": len(steps),
            "rounds": [{"round": st.round, "ok": st.ok, "applied": st.applied,
                        "flex": ["(%d,%d)" % e for e in st.flex],
                        "suggest": ["(%d,%d)" % e for e in st.suggested],
                        "reason": st.reason} for st in steps],
            "wall_s": round(time.time() - t0, 3),
            "binary": rep.binary,
            "binary_sha256": rep.binary_sha256,
        }
        return rep


def load_bank(path: str) -> Dict[Edge, List[List[int]]]:
    """读取 A* 参考路径库（base_routes_full.json 或 routeInfo 转储）。"""
    with open(path) as f:
        doc = json.load(f)
    out: Dict[Edge, List[List[int]]] = {}
    for r in doc.get("routes", []):
        out[(int(r["from"]), int(r["to"]))] = [list(c) for c in r["path"]]
    return out
