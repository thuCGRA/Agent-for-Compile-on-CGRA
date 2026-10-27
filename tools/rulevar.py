"""重复运行 `rule` 行，量它的非确定性有多大。

`rule` 配置的 pins+routes 是同一份输入，但 mapper 每次落出的二进制不同。这里对每个
kernel 跑 N 次，记录 (verify_ok, sim verdict, cell)，从而回答两个问题：

1. 同一份输入下结论是否翻转（`flips`）；
2. 8/10 个单元格里到底有几个是"结论依赖运气"的（`unstable`）。

对不稳定的格子，"rule 行比 pins 行更快/更准"这类结论不成立，只能报分布。
"""

import json
import os
import shutil
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.join(REPO, "tools"))

import confusion as C  # noqa: E402
from ablation_fixii import XD, YD, II, dfg_edges, instance, prep  # noqa: E402

REPS = int(os.environ.get("REPS_ENV", "4"))
OUT = os.environ.get("OUT_ENV", "work/armB/rulevar")


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for kernel in C.KERNELS:
        inst = instance(kernel)
        pj = os.path.join(C.SRC, kernel, "placement.json")
        if not inst or not os.path.exists(pj):
            print("SKIP", kernel, flush=True)
            continue
        edges = dfg_edges(inst["dfg"])
        wd = os.path.join(OUT, kernel)
        os.makedirs(wd, exist_ok=True)
        dfg, mem, alloc = prep(wd, inst)
        pins, routes = C.cheap_place(dfg, edges, II)
        pp = os.path.join(wd, "pins.json")
        rp = os.path.join(wd, "routes.json")
        json.dump({"pins": pins}, open(pp, "w"))
        json.dump({"routes": routes}, open(rp, "w"))

        trace = ""
        for base in (inst["dir"], os.path.join(C.SRC, kernel)):
            c = os.path.join(base, "%s_trace_1.txt" % kernel)
            if os.path.exists(c) and os.path.getsize(c) > 0:
                trace = c
                break

        verdicts = []
        for rep in range(REPS):
            a = C.mapper_run(dfg, mem, wd, pp, rp, False, "noverify_r%d" % rep)
            abin = ""
            if a["bin"]:
                abin = os.path.join(wd, "A_bin_r%d.bin" % rep)
                shutil.copy2(a["bin"], abin)
            b = C.mapper_run(dfg, mem, wd, pp, rp, True, "verify_r%d" % rep)
            sim = C.sim_run(abin, wd, trace, alloc)
            cell = C.classify({"bin": abin}, b, sim)
            verdicts.append((b["ok"], sim["verdict"], cell))
            print("%-10s rep%d verify=%-5s sim=%-9s %s" % (
                kernel, rep, b["ok"], sim["verdict"], cell), flush=True)
        cells = Counter(v[2] for v in verdicts)
        rows.append({"kernel": kernel, "reps": REPS, "cells": dict(cells),
                     "flips": len(cells) > 1,
                     "detail": verdicts})
        print("%-10s stable=%s %s" % (kernel, not rows[-1]["flips"], dict(cells)), flush=True)

    stable = sum(1 for r in rows if not r["flips"])
    with open(os.path.join(OUT, "rulevar.json"), "w") as f:
        json.dump(rows, f, indent=1)
    print("----")
    print("cells=%d stable=%d unstable=%d" % (len(rows), stable, len(rows) - stable))
    for r in rows:
        if r["flips"]:
            print("  UNSTABLE %-10s %s" % (r["kernel"], r["cells"]))


if __name__ == "__main__":
    main()
