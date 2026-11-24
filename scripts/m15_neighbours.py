"""M1.5：SA 的两个邻域算子值不值得改（四个变体，同一预算、同一种子）。

上游的两个邻域算子是纯随机的：
    selectDFGNodeToUnmap()  = sortedNodeList[uniform(0,n-1)]
    getRandomDPCandidate().front()  = 洗牌后取第一个
M0 已经测出"跑满 14900 次移动只换来 2 点 cost 改进"，瓶颈就在这两处。

四个变体：
    base  select=0 pick=0   上游（随机挑节点 + 洗牌取首个）
    sel   select=1 pick=0   按 blame 挑节点（同 PE 拥挤 + 已布线边绕行），前 4 名里随机
    pick  select=0 pick=1   按到已放置邻居的距离 + PE 占用数给候选排序
    both  select=1 pick=1   两个都改

判据只有仿真器（G1），质量看 core.check 的 cost（detour + hops + select_pairs）。
预算固定 1000 次移动，种子固定，这样四个变体比的是"每一步挑得准不准"。

用法（在 ~/acc-thu-agent 下）：
    python scripts/m15_neighbours.py                 # 3 kernel × 4 变体
    python scripts/m15_neighbours.py --budget 4000
    python scripts/m15_neighbours.py --kernel mac
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backends                                    # noqa: E402
from core import build_problem                     # noqa: E402
from gates import Attempt, gate_functional, gate_quality, gate_resource, gate_timing  # noqa: E402
from tools import mapper as mapper_mod             # noqa: E402

KERNELS = ("array_add", "mac", "trmm")
VARIANTS = (("base", 0, 0), ("sel", 1, 0), ("pick", 0, 1), ("both", 1, 1))


def run_one(kernel, ii, select, pick, budget, seed, root=None, do_sim=True):
    prob = build_problem(kernel,
                         os.path.join(mapper_mod.kernel_dir(kernel, root), "dfg.xml"),
                         os.path.join(mapper_mod.kernel_dir(kernel, root), "arch.json"),
                         ii)
    from backends.base import Hints
    be = backends.make("sa")
    t0 = time.time()
    res = be.run(prob, ii, root=root, optimize=True, budget=budget,
                 select=select, pick=pick, hints=Hints(seed=seed))
    wall = time.time() - t0

    att = Attempt(kernel=kernel, backend="sa", ii=res.ii or ii,
                  placement=res.placement, elapsed=round(wall, 3),
                  artifacts=res.artifacts)
    att.gates["G2"] = gate_resource(prob, res.placement, res.ii or ii)
    att.gates["G3"] = gate_timing(res.raw.get("maprun"), prob["n_nodes"])
    if do_sim:
        att.gates["G1"] = gate_functional(kernel, res.artifacts.get("binary"), root=root)
    else:
        from gates import GateResult
        att.gates["G1"] = GateResult(gate="G1", passed=False, reason="未跑仿真（--no-sim）")
    g4 = gate_quality(prob, res.placement, res.ii or ii)
    att.gates["G4"] = g4
    if g4.passed:
        att.cost = g4.detail["cost"]
        att.quality = g4.detail["quality"]
    att.lat = res.lat
    if res.error:
        att.artifacts["backend_error"] = res.error
    return att, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", action="append", default=None)
    ap.add_argument("--ii", type=int, default=4)
    ap.add_argument("--budget", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20136)
    ap.add_argument("--no-sim", action="store_true")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    kernels = args.kernel or list(KERNELS)
    rows = []
    print("kernel     variant II  G1    G2    lat  cost   moves  耗时")
    print("-" * 62)
    for kernel in kernels:
        for name, sel, pick in VARIANTS:
            att, res = run_one(kernel, args.ii, sel, pick, args.budget,
                               args.seed, do_sim=not args.no_sim)
            moves = res.raw.get("maprun") and getattr(res.raw["maprun"], "stdout", "")
            rows.append({"kernel": kernel, "variant": name, "select": sel, "pick": pick,
                         "ii": att.ii, "g1": att.gates["G1"].passed,
                         "g2": att.gates["G2"].passed, "lat": att.lat,
                         "cost": att.cost, "elapsed": att.elapsed,
                         "error": res.error})
            print("%-10s %-7s %-3d %-5s %-5s %-4d %-6.1f %-7d %.1fs"
                  % (kernel, name, att.ii,
                     "PASS" if att.gates["G1"].passed else "fail",
                     "PASS" if att.gates["G2"].passed else "fail",
                     att.lat, att.cost, args.budget, att.elapsed))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print("\n写入 %s" % args.json)


if __name__ == "__main__":
    main()
