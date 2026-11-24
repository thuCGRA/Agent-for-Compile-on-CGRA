"""M1.5b：种子噪声有多大？——四个变体的差异是不是随机波动。

b1000 那张表里 array_add 的 cost 是 base 36 / sel 34 / pick 35 / both 37，
看着像"sel 更好"，但 SA 本身是随机的。如果换 3 个种子后这个排序就散了，
那这条差异就是噪声，不能写成结论。

这个脚本对 (kernel × variant × seed) 做全交叉，只报 cost / lat / G1，
最后按变体给 min/median/max，看区间是否重叠。

用法：
    python scripts/m15_seednoise.py --seeds 20136,7,99 --budget 1000
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

VARIANTS = (("base", 0, 0), ("sel", 1, 0), ("pick", 0, 1), ("both", 1, 1))


def one(kernel, ii, select, pick, budget, seed, root=None, do_sim=True):
    prob = build_problem(kernel,
                         os.path.join(mapper_mod.kernel_dir(kernel, root), "dfg.xml"),
                         os.path.join(mapper_mod.kernel_dir(kernel, root), "arch.json"),
                         ii)
    be = backends.make("sa")
    t0 = time.time()
    res = be.run(prob, ii, root=root, optimize=True, budget=budget,
                 select=select, pick=pick, hints=Hints(seed=seed))
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

    kernels = args.kernel or ["array_add", "trmm"]
    seeds = [int(s) for s in args.seeds.split(",")]
    rows = []
    for kernel in kernels:
        print("### %s (II=%d, budget=%d)" % (kernel, args.ii, args.budget))
        print("  variant seed   G1    II  lat  cost   耗时")
        per_variant = {}
        for name, sel, pick in VARIANTS:
            for seed in seeds:
                r = one(kernel, args.ii, sel, pick, args.budget, seed,
                        do_sim=not args.no_sim)
                r.update({"kernel": kernel, "variant": name, "seed": seed})
                rows.append(r)
                per_variant.setdefault(name, []).append(r)
                print("  %-7s %-6d %-5s %-3d %-4d %-6s %.1fs"
                      % (name, seed, "PASS" if r["g1"] else "fail", r["ii"],
                         r["lat"], r["cost"], r["elapsed"]))
        print("  -- 汇总（只统计 G1 通过的）--")
        for name, _, _ in VARIANTS:
            ok = [r for r in per_variant[name] if r["g1"] and r["cost"] is not None]
            if not ok:
                print("  %-7s 无通过样本" % name)
                continue
            costs = [r["cost"] for r in ok]
            print("  %-7s n=%d cost min=%.1f med=%.1f max=%.1f | lat %s | II %s"
                  % (name, len(ok), min(costs), statistics.median(costs), max(costs),
                     sorted({r["lat"] for r in ok}), sorted({r["ii"] for r in ok})))
        print()
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print("写入 %s" % args.json)


if __name__ == "__main__":
    main()
