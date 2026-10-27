"""LLM pins + 完整 route bank：让 verify 有机会说 ok，从而让 `over_permissive` 有格子。

`--pins` 单独喂给 verify 时，它要自己把其余边全部布通，几乎必然先因为 route 失败而拒，
于是 `over_permissive`（verify 过、仿真崩）永远轮不到。这里把 LLM 的 pin 和 A* 解的
route bank 拼在一起跑，才是"静态通过、动态失败"能被观测到的组合。
"""

import csv
import json
import os
import shutil
import sys

REPO = "/home/steve/cac-armB"
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.join(REPO, "tools"))

import confusion as C  # noqa: E402
from ablation_fixii import II, XD, YD, dfg_edges, instance, prep  # noqa: E402
from cgra_agent.routers.base import shortest_path  # noqa: E402

LLM_ROOT = "/home/steve/CGRA_Agent_Compiler/work/eval-llm"
KERNELS = os.environ.get(
    "KERNELS_ENV", "array_add,array_cond,trmm,conv3,gemm,conv2d,gemm_pace").split(",")
MODE = int(os.environ.get("PINS_MODE_ENV", "2"))
OUT = os.environ.get("OUT_ENV", "work/armB/confusion_llm2")


def main():
    os.makedirs(OUT, exist_ok=True)
    C.PINS_MODE = MODE
    rows = []
    for kernel in KERNELS:
        inst = instance(kernel)
        if not inst:
            print("SKIP", kernel, flush=True)
            continue
        edges = dfg_edges(inst["dfg"])
        ext_p = os.path.join(REPO, "work/armB/fixii", kernel, "placement.json")
        if not os.path.exists(ext_p):
            print("SKIP %s (no bank)" % kernel, flush=True)
            continue
        ext = json.load(open(ext_p))
        bank = {}
        pos = {int(k): tuple(v) for k, v in ext.get("pos", {}).items()}
        for e in edges:
            key = "%d,%d" % e
            if key in ext.get("bank", {}):
                bank[e] = [tuple(p) for p in ext["bank"][key]]
            elif e[0] in pos and e[1] in pos:
                p = shortest_path(pos[e[0]], pos[e[1]], XD, YD)
                if p:
                    bank[e] = p

        pins = json.load(open(os.path.join(LLM_ROOT, kernel, "strict", "pins.json")))["pins"]
        trace = ""
        for cand in ("%s_trace_1.txt" % kernel, "%s_trace_0.txt" % kernel):
            c = os.path.join(inst["dir"], cand)
            if os.path.exists(c) and os.path.getsize(c) > 0:
                trace = c
                break

        wd = os.path.join(OUT, kernel)
        os.makedirs(wd, exist_ok=True)
        dfg, mem, alloc = prep(wd, inst)
        pp = os.path.join(wd, "pins.json")
        rp = os.path.join(wd, "routes.json")
        json.dump({"pins": [{"node": p["node"], "x": p["x"], "y": p["y"], "t": p["t"]}
                            for p in pins]}, open(pp, "w"))
        routes = [{"from": e[0], "to": e[1], "path": bank[e]} for e in edges if e in bank]
        json.dump({"routes": routes}, open(rp, "w"))

        a = C.mapper_run(dfg, mem, wd, pp, rp, False, "noverify")
        abin = os.path.join(wd, "A_bin.bin")
        if a["bin"] and os.path.exists(a["bin"]):
            shutil.copy2(a["bin"], abin)
            a["bin"] = abin
        b = C.mapper_run(dfg, mem, wd, pp, rp, True, "verify")
        sim = C.sim_run(a["bin"], wd, trace, alloc)
        cell = C.classify(a, b, sim)
        rows.append({"kernel": kernel, "mode": MODE, "edges": len(edges),
                     "routes": len(routes), "pins": len(pins),
                     "verify_ok": b["ok"], "sim_ok": sim["ok"],
                     "sim_verdict": sim["verdict"], "cell": cell})
        print("%-10s m%d verify=%-5s sim=%-9s %-20s routes=%d/%d" % (
            kernel, MODE, b["ok"], sim["verdict"], cell, len(routes), len(edges)), flush=True)

    if rows:
        with open(os.path.join(OUT, "confusion_llm2.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        from collections import Counter
        print("----", Counter(r["cell"] for r in rows))


if __name__ == "__main__":
    main()
