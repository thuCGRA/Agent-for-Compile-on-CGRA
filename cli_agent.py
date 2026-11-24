#!/usr/bin/env python
"""agent 的入口。

    python cli_agent.py run --kernel mac --policy scripted --budget 4
    python cli_agent.py run --kernel array_add --policy llm --budget 5
    python cli_agent.py recall --kernel mac

注意：这里只做"调度"这一件事。真正的映射由 mapper 做，判据由仿真器给。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.graph import run_agent            # noqa: E402
from agents.policy import LLMPolicy, ScriptedPolicy  # noqa: E402
from experience.store import Store            # noqa: E402


def _print_run(res: dict) -> None:
    print("kernel=%(kernel)s ii=%(ii)d budget=%(budget)d used=%(used)d ok=%(ok)s"
          % res)
    print("%-4s %-11s %-4s %-6s %-5s %-6s %-8s %-6s %s"
          % ("#", "backend", "II", "G1", "G2", "lat", "cost", "耗时", "params"))
    print("-" * 96)
    for i, h in enumerate(res["history"], 1):
        print("%-4d %-11s %-4d %-6s %-5s %-6s %-8s %-6.1f %s"
              % (i, h["backend"], h["ii"],
                 "PASS" if h["gates"].get("G1", {}).get("passed") else "fail",
                 "PASS" if h["gates"].get("G2", {}).get("passed") else "fail",
                 h["lat"], h["cost"], h["elapsed"],
                 json.dumps(h["params"], ensure_ascii=False)))
        err = h.get("error") or ""
        reason = (h["gates"].get("G1", {}).get("reason") or "")[:110]
        if err or reason:
            print("       └ %s %s" % (err, reason))
    print("-" * 96)
    if res["best"]:
        b = res["best"]
        print("最好解: %s %s cost=%s lat=%s"
              % (b["backend"], json.dumps(b["params"], ensure_ascii=False),
                 b["cost"], b["lat"]))
    else:
        print("没有合法解。停止原因: %s" % (res["stop_reason"] or "预算用完"))
    if res.get("policy_failures"):
        print("策略装置失败 %d 次: %s" % (len(res["policy_failures"]),
                                        json.dumps(res["policy_failures"][:2],
                                                   ensure_ascii=False)[:300]))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="跑一个 kernel")
    r.add_argument("--kernel", required=True)
    r.add_argument("--ii", type=int, default=4)
    r.add_argument("--budget", type=int, default=4, help="最多几次 mapper 调用")
    r.add_argument("--policy", default="scripted", choices=["scripted", "llm"])
    r.add_argument("--no-sim", action="store_true")
    r.add_argument("--no-store", action="store_true")
    r.add_argument("--no-thinking", action="store_true",
                   help="LLM: 关掉推理通道（会快很多，但选得更差）")
    r.add_argument("--json", default=None)

    q = sub.add_parser("recall", help="查经验库")
    q.add_argument("--kernel", required=True)
    q.add_argument("--ii", type=int, default=4)

    args = ap.parse_args()
    if args.cmd == "recall":
        from tools import mapper as mapper_mod
        from agents.engine import load_problem
        st = Store()
        prob = load_problem(args.kernel, args.ii)
        rows = st.recall(args.kernel, prob["n_nodes"], args.ii)
        print(json.dumps({"stats": st.stats(), "rows": rows},
                         ensure_ascii=False, indent=1))
        return

    store = None if args.no_store else Store()
    if args.policy == "llm":
        policy = LLMPolicy(no_thinking=args.no_thinking)
    else:
        policy = ScriptedPolicy()
    res = run_agent(args.kernel, ii=args.ii, budget=args.budget, policy=policy,
                    store=store, do_sim=not args.no_sim)
    _print_run(res)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("\n写入 %s" % args.json)


if __name__ == "__main__":
    main()
