"""LLM 放置行的 2x2：把 arm A 的真端点放置（strict/ repair 两份）喂进同一套双裁判。

放置来自 `/home/steve/CGRA_Agent_Compiler/work/eval-llm/<kernel>/<variant>/pins.json`，
不是 A* 自己的解，所以这一行才是 `over_permissive` 可能出现的地方。

每格记录：
  accepted_xyz  最终映射里与请求 pin **完全一致**（x, y, t）的节点数
  accepted_xy   只有 (x, y) 一致的节点数（时间被挪了）
  cell          2x2 分类
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
from ablation_fixii import II, XD, YD, dfg_edges, instance, parse_routeinfo, prep  # noqa: E402

LLM_ROOT = "/home/steve/CGRA_Agent_Compiler/work/eval-llm"
KERNELS = os.environ.get(
    "KERNELS_ENV", "array_add,array_cond,trmm,conv3,gemm,conv2d,gemm_pace").split(",")
VARIANTS = os.environ.get("VARIANTS_ENV", "strict,repair").split(",")
MODES = [int(m) for m in os.environ.get("MODES_ENV", "1,2,3").split(",")]
OUT = os.environ.get("OUT_ENV", "work/armB/confusion_llm")


def accepted(pins, place):
    xyz = xy = 0
    for p in pins:
        got = place.get(int(p["node"]))
        if not got:
            continue
        if int(got[0]) == int(p["x"]) and int(got[1]) == int(p["y"]):
            xy += 1
            if int(got[2]) == int(p["t"]) % II:
                xyz += 1
    return xyz, xy


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for kernel in KERNELS:
        inst = instance(kernel)
        if not inst:
            print("SKIP", kernel, flush=True)
            continue
        edges = dfg_edges(inst["dfg"])
        trace = ""
        for cand in ("%s_trace_1.txt" % kernel, "%s_trace_0.txt" % kernel):
            c = os.path.join(inst["dir"], cand)
            if os.path.exists(c) and os.path.getsize(c) > 0:
                trace = c
                break
        for variant in VARIANTS:
            pj = os.path.join(LLM_ROOT, kernel, variant, "pins.json")
            if not os.path.exists(pj):
                print("SKIP %s/%s" % (kernel, variant), flush=True)
                continue
            pins = json.load(open(pj))["pins"]
            for mode in MODES:
                C.PINS_MODE = mode
                wd = os.path.join(OUT, kernel, "%s_m%d" % (variant, mode))
                os.makedirs(wd, exist_ok=True)
                dfg, mem, alloc = prep(wd, inst)
                pp = os.path.join(wd, "pins.json")
                rp = os.path.join(wd, "routes.json")
                json.dump({"pins": [{"node": p["node"], "x": p["x"], "y": p["y"], "t": p["t"]}
                                    for p in pins]}, open(pp, "w"))
                json.dump({"routes": []}, open(rp, "w"))

                a = C.mapper_run(dfg, mem, wd, pp, rp, False, "noverify")
                abin = os.path.join(wd, "A_bin.bin")
                if a["bin"] and os.path.exists(a["bin"]):
                    shutil.copy2(a["bin"], abin)
                    a["bin"] = abin
                b = C.mapper_run(dfg, mem, wd, pp, rp, True, "verify")
                sim = C.sim_run(a["bin"], wd, trace, alloc)
                cell = C.classify(a, b, sim)

                xyz = xy = 0
                for tag in ("noverify", "verify"):
                    for cand in ("%s.routeInfo.log" % tag, "%s_report.json" % tag):
                        p = os.path.join(wd, cand)
                        if cand.endswith(".json"):
                            try:
                                rep = json.load(open(p))
                            except (OSError, ValueError):
                                continue
                            place = {int(q["node"]): (q["x"], q["y"], q["t"])
                                     for q in rep.get("placement", [])}
                        else:
                            if not os.path.exists(p):
                                continue
                            place = parse_routeinfo(open(p, errors="replace").read())[0]
                        x, y = accepted(pins, place)
                        xyz, xy = max(xyz, x), max(xy, y)

                rows.append({"kernel": kernel, "variant": variant, "mode": mode,
                             "edges": len(edges), "pins": len(pins),
                             "accepted_xyz": xyz, "accepted_xy": xy,
                             "verify_ok": b["ok"], "sim_ok": sim["ok"],
                             "sim_verdict": sim["verdict"], "cell": cell})
                print("%-10s %-7s m%d verify=%-5s sim=%-9s %-20s pinned=%d/%d xy=%d" % (
                    kernel, variant, mode, b["ok"], sim["verdict"], cell, xyz, len(pins), xy),
                    flush=True)

    if rows:
        with open(os.path.join(OUT, "confusion_llm.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        from collections import Counter
        print("----")
        for m in MODES:
            print("mode %d:" % m, Counter(r["cell"] for r in rows if r["mode"] == m))
        print("by variant:", Counter((r["variant"], r["cell"]) for r in rows))


if __name__ == "__main__":
    main()
