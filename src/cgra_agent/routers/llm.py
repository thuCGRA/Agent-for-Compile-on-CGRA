"""LLMPolicy：与 RulePolicy 同接口的 LLM 布线策略（当前只有提示词 + 解析 + 离线回放）。

没有可用端点时**抛 NoEndpoint**，由上层决定回退到 RulePolicy —— 不做静默降级，
否则消融表里会出现"以为是 LLM 布的、其实是规则布的"这种脏数据。

离线回放：`CGRA_LLM_MOCK=<file.json>`，格式 {"calls": [{"routes": [...]}, ...]}，
按调用顺序取用，用来在没有网的情况下把链路测通。

真调用：`CGRA_LLM_BASE_URL` / `CGRA_LLM_API_KEY` / `CGRA_LLM_MODEL` 齐备时走
OpenAI 兼容的 `/chat/completions`。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from typing import Dict, List, Optional

from ..interfaces import MapReport, Placement, RouteEdge, Routes
from .base import Edge, select_edges
from .rule import paths_of


class NoEndpoint(RuntimeError):
    """没有配置 LLM 端点，也没有 mock：上层应退回 RulePolicy。"""


PROMPT = """你在为一个 4x4 的 CGRA 数组布线。每个 PE 用 (x,y) 表示，x 向右、y 向下。
已固定的放置（节点 -> 位置 -> 时间拍）：
{place}

DFG 边（源 -> 目的，slack = 目的拍 - 源拍，越大越有富余）：
{edges}

请只给下面这些边指定路由（跳数越少越好，相邻步只能走上下左右一格）：
{targets}

以 JSON 返回：{{"routes": [{{"from": 1, "to": 2, "path": [[x,y], ...]}}]}}
路径必须包含起点和终点，相邻两格必须是单步相邻。不要解释。
"""


class LLMPolicy:
    name = "llm"

    def __init__(self, mapper, seed: int = 1, max_rounds: int = 3,
                 mock: Optional[str] = None, verbose: bool = False):
        self.mapper = mapper
        self.seed = seed
        self.max_rounds = max_rounds
        self.verbose = verbose
        self.mock_path = mock or os.environ.get("CGRA_LLM_MOCK", "")
        self._mock: List[dict] = []
        if self.mock_path:
            with open(self.mock_path) as f:
                self._mock = json.load(f).get("calls", [])

    # ------------------------------------------------------------ 纯策略

    def routes(self, placement: Placement, facts: dict, arch: dict,
               ii: int, coverage: float, rule: str) -> Routes:
        lat = facts["lat"]
        edges = [tuple(e) for e in facts["edges"]]
        k = int(round(coverage * len(edges)))
        chosen = select_edges(edges, rule, k, lat, self.seed)
        proposed = self.ask(placement, facts, chosen)
        self.answered = sorted(proposed)
        self.unpathed = [e for e in chosen if e not in proposed]
        return Routes(edges=[RouteEdge(src=a, dst=b, path=p)
                             for (a, b), p in sorted(proposed.items())])

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
        rep.extra = {
            "policy": self.name, "rule": rule, "coverage": coverage,
            "seed": self.seed, "edges_total": len(facts["edges"]),
            "proposed": len(routes.edges),
            "unpathed": ["(%d,%d)" % e for e in self.unpathed],
            "accepted": rep.routes_applied, "rejected": rep.routes_rejected,
            "verify_rounds": len(steps),
            "rounds": [{"round": st.round, "ok": st.ok, "applied": st.applied,
                        "flex": ["(%d,%d)" % e for e in st.flex],
                        "suggest": ["(%d,%d)" % e for e in st.suggested],
                        "reason": st.reason} for st in steps],
            "wall_s": round(time.time() - t0, 3),
            "binary": rep.binary, "binary_sha256": rep.binary_sha256,
        }
        return rep

    # ------------------------------------------------------------ 模型

    def ask(self, placement: Placement, facts: dict,
            chosen: List[Edge]) -> Dict[Edge, List[List[int]]]:
        pins = (placement.to_dict()["pins"] if hasattr(placement, "to_dict")
                else placement["pins"])
        place = "\n".join("  node %d -> (%d,%d) @ %d" % (p["node"], p["x"], p["y"], p["t"])
                          for p in pins)
        lines = "\n".join("  %d -> %d   slack %+d" % (
            a, b, facts["lat"].get(b, 0) - facts["lat"].get(a, 0))
            for a, b in facts["edges"])
        targets = "\n".join("  %d -> %d" % e for e in chosen)
        prompt = PROMPT.format(place=place, edges=lines, targets=targets)
        return self.parse(self.call(prompt), set(chosen))

    def call(self, prompt: str) -> str:
        if self._mock:
            item = self._mock.pop(0) if self._mock else {}
            return json.dumps(item)
        base = os.environ.get("CGRA_LLM_BASE_URL", "")
        key = os.environ.get("CGRA_LLM_API_KEY", "")
        model = os.environ.get("CGRA_LLM_MODEL", "")
        if not (base and key and model):
            raise NoEndpoint(
                "LLMPolicy: set CGRA_LLM_BASE_URL/API_KEY/MODEL or CGRA_LLM_MOCK; "
                "callers should fall back to RulePolicy")
        body = json.dumps({"model": model, "temperature": 0,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=120) as r:
            doc = json.load(r)
        return doc["choices"][0]["message"]["content"]

    @staticmethod
    def parse(text: str, allowed: set) -> Dict[Edge, List[List[int]]]:
        """解析模型输出；越界/非法的路径直接丢弃，不猜、不修。"""
        s, e = text.find("{"), text.rfind("}")
        if s < 0 or e < 0:
            return {}
        try:
            doc = json.loads(text[s:e + 1])
        except ValueError:
            return {}
        out: Dict[Edge, List[List[int]]] = {}
        for r in doc.get("routes", []):
            try:
                edge = (int(r["from"]), int(r["to"]))
                path = [[int(c[0]), int(c[1])] for c in r["path"]]
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if edge not in allowed or len(path) < 2:
                continue
            if all(abs(path[i][0] - path[i + 1][0]) + abs(path[i][1] - path[i + 1][1]) == 1
                   for i in range(len(path) - 1)):
                out[edge] = path
        return out


def mock_from_bank(bank_path: str, out_path: str, edges: int = 99) -> None:
    """把 A* 参考路径包成一个 mock 回复，用来离线验证 LLMPolicy 的链路。"""
    with open(bank_path) as f:
        doc = json.load(f)
    routes = [{"from": r["from"], "to": r["to"], "path": r["path"]}
              for r in doc.get("routes", [])][:edges]
    with open(out_path, "w") as f:
        json.dump({"calls": [{"routes": routes}]}, f)
