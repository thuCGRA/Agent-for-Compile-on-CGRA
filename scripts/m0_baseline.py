"""M0 基线表：把两个后端 × 三个 kernel × II 4/3 跑一遍，打印成表。

用途：
  1. 确认 core/tools/backends/gates 四层真的串起来了；
  2. 给出 agent 后面要打的那条基线（后面所有"agent 更快/更好"都要相对它说）。

用法（在 ~/acc-thu-agent 下）：
    python scripts/m0_baseline.py                # 默认 pathfinder + sa
    python scripts/m0_baseline.py --backend sa   # 只跑一个
    python scripts/m0_baseline.py --ii 4         # 只跑 II=4
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backends                                    # noqa: E402
from core import build_problem, score_placement    # noqa: E402
from gates import (Attempt, gate_functional, gate_quality,   # noqa: E402
                   gate_resource, gate_timing)
from tools import mapper as mapper_mod             # noqa: E402

KERNELS = ("array_add", "mac", "trmm")


def run_one(kernel, backend, ii, root=None, force_anneal=False, do_sim=True,
            optimize=False, budget=0):
    prob = build_problem(kernel,
                         os.path.join(mapper_mod.kernel_dir(kernel, root), "dfg.xml"),
                         os.path.join(mapper_mod.kernel_dir(kernel, root), "arch.json"),
                         ii)
    be = backends.make(backend)
    kw = {}
    if backend == "sa":
        kw["force_anneal"] = force_anneal
        kw["optimize"] = optimize
        kw["budget"] = budget
    t0 = time.time()
    res = be.run(prob, ii, root=root, **kw)
    wall = time.time() - t0

    att = Attempt(kernel=kernel, backend=backend, ii=res.ii or ii,
                  placement=res.placement, elapsed=round(wall, 3),
                  artifacts=res.artifacts)
    att.gates["G2"] = gate_resource(prob, res.placement, res.ii or ii)
    att.gates["G3"] = gate_timing(res.raw.get("maprun"), prob["n_nodes"])
    if do_sim:
        att.gates["G1"] = gate_functional(kernel, res.artifacts.get("binary"), root=root)
    else:
        att.gates["G1"] = _skip("G1", "未跑仿真（--no-sim）")
    g4 = gate_quality(prob, res.placement, res.ii or ii)
    att.gates["G4"] = g4
    if g4.passed:
        att.cost = g4.detail["cost"]
        att.quality = g4.detail["quality"]
    att.lat = res.lat
    if res.error:
        att.artifacts["backend_error"] = res.error
    return att


def _skip(name, why):
    from gates import GateResult
    return GateResult(gate=name, passed=False, reason=why)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", action="append", default=None)
    ap.add_argument("--kernel", action="append", default=None)
    ap.add_argument("--ii", action="append", type=int, default=None)
    ap.add_argument("--no-sim", action="store_true")
    ap.add_argument("--force-anneal", action="store_true",
                    help="SA: 热启动已可行也进退火循环（仍在上游语义下，可行即停）")
    ap.add_argument("--optimize", action="store_true",
                    help="SA: 可行之后继续退火，最后回滚到最便宜的可行快照")
    ap.add_argument("--budget", type=int, default=0,
                    help="SA: 移动次数上限，0=不限")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    backends_ = args.backend or ["pathfinder", "sa"]
    kernels = args.kernel or list(KERNELS)
    iis = args.ii or [4, 3]

    rows = []
    print("%-10s %-10s %-4s %-6s %-6s %-7s %-6s %-8s %s"
          % ("kernel", "backend", "II", "G1", "G2", "G3", "lat", "cost", "耗时"))
    print("-" * 78)
    for kernel in kernels:
        for backend in backends_:
            for ii in iis:
                att = run_one(kernel, backend, ii,
                              force_anneal=args.force_anneal,
                              do_sim=not args.no_sim,
                              optimize=args.optimize,
                              budget=args.budget)
                rows.append(att.as_dict())
                print("%-10s %-10s %-4d %-6s %-6s %-7s %-6d %-8.1f %.1fs"
                      % (kernel, backend, att.ii,
                         "PASS" if att.gates["G1"].passed else "fail",
                         "PASS" if att.gates["G2"].passed else "fail",
                         "PASS" if att.gates["G3"].passed else "fail",
                         att.lat, att.cost, att.elapsed))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print("\n写入 %s" % args.json)


if __name__ == "__main__":
    main()
