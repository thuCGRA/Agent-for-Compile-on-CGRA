"""M1.5b：把 SA 的代价函数换成线长之后，退火到底有没有用。

上游 getCost() = unmapped*100 + congestion*10 + conflict*10 + port_usage，
里面没有延迟也没有绕行。M1.5 证明热启动可行之后它就再没有下降空间，
三种子跑出完全一样的解（array_add 36/36/36、trmm 18/18/18）。

patch 0007 加 --sa_cost_mode：mode 1 时把 port_usage 换成「已布线边总跳数」。
这个脚本比较 mode 0 与 mode 1 在同一预算、同一种子下的结果：
    cost  = 我们的目标（core.check: detour + hops + select_pairs）
    wire  = SA 自己的目标（总跳数，从 routeInfo 重建）
    lat   = 关键路径延迟
如果 mode 1 的 cost 系统性低于 mode 0 且 G1 仍通过，说明"把目标喂进去"这条路成立。

用法：
    python scripts/m15b_costmode.py --seeds 20136,7,99 --budget 1000
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backends                                    # noqa: E402
from backends.base import Hints                    # noqa: E402
from core import build_problem                     # noqa: E402
from gates import gate_functional, gate_quality, gate_resource  # noqa: E402
from tools import mapper as mapper_mod             # noqa: E402


def wire_of(placement):
    """粗算：每个已放置节点到其父节点的曼哈顿距离之和（与 core.check 的 hops 同量级）。"""
    if not placement:
        return None
    total = 0
    for idx, p in placement.items():
        for par in p.get("parents", []) or []:
            q = placement.get(par)
            if q:
                total += abs(p["x"] - q["x"]) + abs(p["y"] - q["y"])
    return total


def one(kernel, ii, cost_mode, budget, seed, root=None, do_sim=True):
    prob = build_problem(kernel,
                         os.path.join(mapper_mod.kernel_dir(kernel, root), "dfg.xml"),
                         os.path.join(mapper_mod.kernel_dir(kernel, root), "arch.json"),
                         ii)
    be = backends.make("sa")
    t0 = time.time()
    res = be.run(prob, ii, root=root, optimize=True, budget=budget, cost_mode=cost_mode,
                 hints=Hints(seed=seed))
    wall = time.time() - t0
    g1 = gate_functional(kernel, res.artifacts.get("binary"), root=root) if do_sim else None
    g2 = gate_resource(prob, res.placement, res.ii or ii)
    g4 = gate_quality(prob, res.placement, res.ii or ii)
    return {"ii": res.ii or ii, "g1": bool(g1 and g1.passed), "g2": bool(g2.passed),
            "lat": res.lat, "cost": g4.detail.get("cost") if g4.passed else None,
            "elapsed": round(wall, 1), "error": res.error}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", action="append", default=None)
    ap.add_argument("--ii", type=int, default=4)
    ap.add_argument("--budget", type=int, default=1000)
    ap.add_argument("--seeds", default="20136,7,99")
    ap.add_argument("--no-sim", action="store_true")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    kernels = args.kernel or ["array_add", "mac", "trmm"]
    seeds = [int(s) for s in args.seeds.split(",")]
    rows = []
    for kernel in kernels:
        print("### %s (II=%d, budget=%d)" % (kernel, args.ii, args.budget))
        print("  mode seed   G1    II  lat  cost   耗时")
        buckets = {0: [], 1: []}
        for mode in (0, 1):
            for seed in seeds:
                r = one(kernel, args.ii, mode, args.budget, seed, do_sim=not args.no_sim)
                r.update({"kernel": kernel, "mode": mode, "seed": seed})
                rows.append(r)
                buckets[mode].append(r)
                print("  %-4d %-6d %-5s %-3d %-4d %-6s %.1fs"
                      % (mode, seed, "PASS" if r["g1"] else "fail", r["ii"],
                         r["lat"], r["cost"], r["elapsed"]))
        for mode in (0, 1):
            ok = [r for r in buckets[mode] if r["g1"] and r["cost"] is not None]
            if not ok:
                print("  mode %d: 无通过样本 (%d/%d fail)" %
                      (mode, sum(1 for r in buckets[mode] if not r["g1"]), len(buckets[mode])))
                continue
            cs = [r["cost"] for r in ok]
            print("  mode %d: n=%d cost min=%.1f med=%.1f max=%.1f | lat %s | II %s"
                  % (mode, len(ok), min(cs), statistics.median(cs), max(cs),
                     sorted({r["lat"] for r in ok}), sorted({r["ii"] for r in ok})))
        print()
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print("写入 %s" % args.json)


if __name__ == "__main__":
    main()
