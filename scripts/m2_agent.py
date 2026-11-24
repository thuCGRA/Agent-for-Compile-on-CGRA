#!/usr/bin/env python
"""M2 对照实验：同一实例、同一判据、同一预算口径，唯一差别是谁决定下一步。

四条臂：
  pathfinder  一次 mapper 调用，不做任何决策（下界参照，也是"最省"的一条）
  sa          一次 mapper 调用，跑满参（budget=1000，M1.5 证明这是 SA 的最好点）
  scripted    ScriptedPolicy 驱动的 agent（固定阶梯，确定性，可复现）
  llm         LLMPolicy 驱动的 agent（真端点）

判据：G1 仿真通过才算"合法"。cost 只在合法时才有意义。

用法：
    python scripts/m2_agent.py                       # 3 kernel × 4 臂
    python scripts/m2_agent.py --arms scripted --kernel mac
    python scripts/m2_agent.py --json work/m2.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agents.engine import run_attempt, summarize        # noqa: E402
from agents.graph import run_agent                     # noqa: E402
from agents.policy import LLMPolicy, ScriptedPolicy     # noqa: E402
from experience.store import Store                      # noqa: E402

DEFAULT_KERNELS = ("array_add", "mac", "trmm")
ARMS = ("pathfinder", "sa", "scripted", "llm")


def one_baseline(kernel: str, ii: int, arm: str, root, do_sim: bool) -> dict:
    t0 = time.time()
    if arm == "pathfinder":
        att = run_attempt(kernel, ii, "pathfinder", root=root, do_sim=do_sim)
    elif arm == "sa":
        att = run_attempt(kernel, ii, "sa", params={"optimize": True, "budget": 1000},
                          root=root, do_sim=do_sim)
    else:
        raise ValueError(arm)
    s = summarize(att)
    s["attempts"] = 1
    s["wall"] = round(time.time() - t0, 1)
    s["best_cost"] = att.cost if att.ok else None
    return s


def one_agent(kernel: str, ii: int, arm: str, budget: int, root, do_sim: bool,
              store) -> dict:
    t0 = time.time()
    policy = LLMPolicy(store=store) if arm == "llm" else ScriptedPolicy()
    res = run_agent(kernel, ii=ii, budget=budget, policy=policy, store=store,
                    root=root, do_sim=do_sim)
    b = res["best"] or {}
    return {"kernel": kernel, "backend": arm, "ii": b.get("ii", ii),
            "ok": bool(res["ok"]), "cost": b.get("cost"),
            "lat": b.get("lat"), "elapsed": b.get("elapsed", 0.0),
            "attempts": res["used"], "wall": round(time.time() - t0, 1),
            "best_cost": b.get("cost"),
            "stop": res["stop_reason"],
            "failures": len(res.get("policy_failures") or [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernels", default=",".join(DEFAULT_KERNELS))
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--ii", type=int, default=4)
    ap.add_argument("--budget", type=int, default=4, help="agent 的 mapper 调用上限")
    ap.add_argument("--no-sim", action="store_true")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    kernels = [k.strip() for k in args.kernels.split(",") if k.strip()]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    do_sim = not args.no_sim
    store = None if args.no_store else Store()

    rows = []
    for k in kernels:
        for arm in arms:
            print("[run] %-10s %-11s ..." % (k, arm), flush=True)
            try:
                if arm in ("pathfinder", "sa"):
                    r = one_baseline(k, args.ii, arm, None, do_sim)
                else:
                    r = one_agent(k, args.ii, arm, args.budget, None, do_sim, store)
            except Exception as e:                     # 一条臂炸掉不影响整张表
                r = {"kernel": k, "backend": arm, "ok": False, "cost": None,
                     "lat": 0, "elapsed": 0.0, "attempts": 0, "wall": 0.0,
                     "best_cost": None, "error": "%s: %s" % (type(e).__name__, e)}
            r["kernel"] = k
            r["arm"] = arm
            rows.append(r)
            print("      -> ok=%-5s cost=%-7s attempts=%s wall=%ss %s"
                  % (r.get("ok"), r.get("cost"), r.get("attempts"),
                     r.get("wall"), r.get("error", "")))

    print()
    print("%-11s %-11s %-6s %-6s %-7s %-6s %-8s %-7s" %
          ("kernel", "arm", "ok", "II", "cost", "lat", "attempts", "wall"))
    print("-" * 76)
    for r in rows:
        print("%-11s %-11s %-6s %-6s %-7s %-6s %-8s %-7s" %
              (r["kernel"], r["arm"], "PASS" if r.get("ok") else "fail",
               r.get("ii", ""), r.get("cost"), r.get("lat", ""),
               r.get("attempts", ""), r.get("wall", "")))
    print("-" * 76)
    for arm in arms:
        sub = [r for r in rows if r["arm"] == arm]
        n_ok = sum(1 for r in sub if r.get("ok"))
        print("%-11s 合法 %d/%d" % (arm, n_ok, len(sub)))

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"ii": args.ii, "budget": args.budget, "rows": rows},
                      f, ensure_ascii=False, indent=1)
        print("\n写入 %s" % args.json)


if __name__ == "__main__":
    main()
