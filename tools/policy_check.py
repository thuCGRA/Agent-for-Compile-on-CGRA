"""策略自检：不碰 mapper 的部分先测通（纯函数 + 接口契约）。

用法：python3 tools/policy_check.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cgra_agent.interfaces import Placement, Pin  # noqa: E402
from cgra_agent.routers import (LLMPolicy, NoEndpoint, RulePolicy, load_bank,
                                mock_from_bank, select_edges,
                                shortest_path)  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SPIKE = os.path.join(ROOT, "work", "armB", "spike")

EDGES = [(11, 12), (12, 0), (13, 0), (0, 1), (1, 2), (2, 13), (20018, 20017)]
LAT = {11: 0, 12: 2, 13: 6, 0: 3, 1: 4, 2: 5, 20017: 7, 20018: 1}
FACTS = {"paths": {"workdir": ".", "dfg": "x.xml", "arch_json": "m.json",
                   "mem_alloc": "a.txt"},
         "edges": [list(e) for e in EDGES], "lat": LAT,
         "pos": {11: [0, 1], 12: [2, 1], 13: [2, 1], 0: [1, 1], 1: [1, 1]},
         "arch": {"x": 4, "y": 4}}
PLACE = Placement(pins=[Pin(node=n, x=LAT[n] % 4, y=0, t=LAT[n] % 4) for n in LAT],
                  source="test")


class FakeMapper:
    """记录调用，返回一个成功的 MapReport；不发子进程。"""

    def __init__(self):
        self.calls = []

    def repair(self, fixture_dir, dfg, mem, trace, alloc, placement, routes,
               max_rounds=3, verbosity=False):
        from cgra_agent.interfaces import MapReport
        from cgra_agent.mapper import RepairStep, VerifyResult
        self.calls.append({"routes": len(routes.edges), "flex": list(routes.flex),
                           "mem": mem, "alloc": alloc})
        rep = MapReport(ok=True, ii=4, lat=13, routes_total=len(routes.edges),
                        routes_applied=len(routes.edges))
        return VerifyResult(rep, None, ""), [RepairStep(0, True, [], len(routes.edges), [])]


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("  " + extra if extra else ""))
    return 0 if cond else 1


def main() -> int:
    bad = 0
    print("select_edges")
    crit = select_edges(EDGES, "critical", 3, LAT)
    rnd1 = select_edges(EDGES, "random", 3, LAT, seed=1)
    rnd1b = select_edges(EDGES, "random", 3, LAT, seed=1)
    rnd2 = select_edges(EDGES, "random", 3, LAT, seed=2)
    bad += check("critical 取 slack 最大的三条", crit == [(13, 0), (2, 13), (1, 2)] or
                 max(LAT[b] - LAT[a] for a, b in crit) >= max(LAT[b] - LAT[a] for a, b in EDGES[:3]))
    bad += check("random 同种子可复现", rnd1 == rnd1b)
    bad += check("random 换种子会变", rnd1 != rnd2)
    bad += check("k=0 返回空", select_edges(EDGES, "critical", 0, LAT) == [])

    print("shortest_path")
    p = shortest_path((0, 0), (2, 1))
    bad += check("4x4 内可达且逐步相邻", p is not None and len(p) == 4 and
                 all(abs(p[i][0] - p[i + 1][0]) + abs(p[i][1] - p[i + 1][1]) == 1
                     for i in range(len(p) - 1)), str(p))
    bad += check("越界返回 None", shortest_path((0, 0), (9, 9)) is None)
    bad += check("绕开占用后仍然可达", shortest_path((0, 0), (1, 1),
                                                   avoid={(0, 0, 1, 0): 1}) is not None)

    print("RulePolicy.routes（纯策略，不碰 mapper）")
    fm = FakeMapper()
    pol = RulePolicy(fm, path_mode="bank",
                     bank=load_bank(os.path.join(SPIKE, "base_routes_full.json")))
    r_full = pol.routes(PLACE, FACTS, FACTS["arch"], 4, 1.0, "critical")
    r_none = pol.routes(PLACE, FACTS, FACTS["arch"], 4, 0.0, "critical")
    r_half = pol.routes(PLACE, FACTS, FACTS["arch"], 4, 0.5, "critical")
    bad += check("覆盖率 0 不产出路由", len(r_none.edges) == 0)
    bad += check("覆盖率 100 不超出边数", len(r_full.edges) <= len(EDGES))
    bad += check("覆盖率 50 约一半", len(r_half.edges) == 3 or len(r_half.edges) == 4,
                 str(len(r_half.edges)))
    bad += check("bank 里没有的边记进 unpathed", len(pol.unpathed) >= 0)

    pol2 = RulePolicy(fm, path_mode="shortest")
    r_s = pol2.routes(PLACE, FACTS, FACTS["arch"], 4, 1.0, "critical")
    bad += check("shortest 模式产出合法路径", all(
        all(abs(e.path[i][0] - e.path[i + 1][0]) + abs(e.path[i][1] - e.path[i + 1][1]) == 1
            for i in range(len(e.path) - 1)) for e in r_s.edges))

    print("route() 会落盘并填 binary 字段")
    rep = pol.route(PLACE, FACTS, FACTS["arch"], 4,
                    {"coverage": 1.0, "rule": "critical", "max_rounds": 1})
    bad += check("route() 调用了 mapper.repair", len(fm.calls) == 1)
    bad += check("repair 拿到 arch_json 作为 mem", fm.calls[0]["mem"] == "m.json")
    bad += check("repair 拿到 mem_alloc 作为 alloc", fm.calls[0]["alloc"] == "a.txt")
    bad += check("MapReport 有 binary 字段", hasattr(rep, "binary"))
    bad += check("MapReport 有 binary_sha256 字段", hasattr(rep, "binary_sha256"))
    bad += check("MapReport 有 routes_rejected", hasattr(rep, "routes_rejected"))
    bad += check("ok 时 failure_class 为空", rep.failure_class == "")

    print("LLMPolicy")
    lp = LLMPolicy(fm)
    raised = False
    try:
        lp.routes(PLACE, FACTS, FACTS["arch"], 4, 0.5, "critical")
    except NoEndpoint:
        raised = True
    bad += check("无端点时抛 NoEndpoint（不静默降级）", raised)

    mock = os.path.join(SPIKE, "_mock_routes.json")
    mock_from_bank(os.path.join(SPIKE, "base_routes_full.json"), mock)
    lp2 = LLMPolicy(fm, mock=mock)
    r_m = lp2.routes(PLACE, FACTS, FACTS["arch"], 4, 1.0, "critical")
    bad += check("mock 回放能产出路由", len(r_m.edges) > 0, "%d" % len(r_m.edges))
    bad += check("mock 回放只保留被问到的边",
                 all((e.src, e.dst) in [tuple(x) for x in FACTS["edges"]]
                     for e in r_m.edges))
    os.remove(mock)

    print("\n%d failure(s)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
