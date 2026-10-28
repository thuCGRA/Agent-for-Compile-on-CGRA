#!/usr/bin/env python3
"""E1: LLM picks (x, y) only; the compiler schedules the phases.

Replays the cached call-1 answers (same prompt as before), throws away the
model's t, runs ``tools/legalize.schedule`` and maps strictly.  Reports map
success, latency, and (when a trace exists) simulator mismatches.
"""
from __future__ import annotations

import argparse, csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

import eval_llm_placement as ev  # noqa: E402
from cgra_agent.llm.cache import LLMCache, sha256_file  # noqa: E402
from cgra_agent.llm.client import LLMClient  # noqa: E402
from cgra_agent.llm.prompts import build_messages  # noqa: E402
from cgra_agent.tools.facts import build_facts  # noqa: E402
from cgra_agent.tools.legalize import schedule  # noqa: E402

II = {"gemm_pace": 9}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernels", default="array_add,array_cond,trmm,hpcg,gemm,gemm_pace,conv2d,conv3,mult,mult")
    ap.add_argument("--out", default=os.path.join(ROOT, "work/space_time.csv"))
    a = ap.parse_args()
    names = [k for k in a.kernels.split(",") if k and k != "mult"]
    names = [k for k in names if os.path.isdir(os.path.join(ROOT, "work/kernels", k))]
    client, cache = LLMClient(), LLMCache(os.path.join(ROOT, "work/llm-cache"))
    rows = []
    for kernel in names:
        ii = II.get(kernel, 4)
        d = os.path.join(ROOT, "work/kernels", kernel)
        facts = build_facts(os.path.join(d, "dfg.xml"), os.path.join(d, "mem.json"), ii)
        try:
            text, cached, _ = ev.call_or_cache(client, cache, kernel, sha256_file(os.path.join(d, "dfg.xml")),
                                               ii, build_messages(facts, ii), os.path.join(ROOT, "work/e1", kernel), "call1")
        except Exception as exc:  # noqa: BLE001
            rows.append({"kernel": kernel, "ii": ii, "stage": "no_answer", "map": "", "lat": "", "sim": "",
                         "note": str(exc)[:80]})
            print(kernel, "no_answer", str(exc)[:60], flush=True)
            continue
        try:
            pins = ev.parse_placement(text, ii).pins
        except Exception as exc:  # noqa: BLE001
            rows.append({"kernel": kernel, "ii": ii, "stage": "no_answer", "map": "", "lat": "", "sim": "", "note": str(exc)[:80]})
            print(kernel, "no_answer", flush=True)
            continue
        spatial = {int(p.node): (int(p.x), int(p.y)) for p in pins}
        try:
            sch = schedule(facts, ii, spatial)
        except Exception as exc:  # noqa: BLE001
            rows.append({"kernel": kernel, "ii": ii, "stage": "legalize_fail", "map": "", "lat": "", "sim": "", "note": str(exc)[:100]})
            print(kernel, "legalize_fail", str(exc)[:60], flush=True)
            continue
        # keep the model's column choice where legalize had to move a node
        pins2 = [{"node": k, "x": v["x"], "y": v["y"], "t": v["t"]} for k, v in sorted(sch.items())]
        moved = sum(1 for k, v in sch.items() if spatial.get(k) != (v["x"], v["y"]))
        res = ev.mapper_run(kernel, pins2, "space_time", 1, ii)
        rows.append({"kernel": kernel, "ii": ii, "stage": "mapped" if res["ok"] else "map_fail",
                     "map": "ok" if res["ok"] else "fail", "lat": res.get("lat") or "",
                     "sim": "", "note": "%d node(s) moved off the model's (x,y)" % moved})
        print("%-10s ii=%d %s lat=%s moved=%d %s" % (kernel, ii, "ok" if res["ok"] else "fail",
                                                    res.get("lat"), moved, (res.get("error") or "")[:50]), flush=True)
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print("wrote", a.out)


if __name__ == "__main__":
    main()