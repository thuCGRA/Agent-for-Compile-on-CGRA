#!/usr/bin/env python3
"""E2: repair the model's *space* until it becomes schedulable.

The model's (x, y) is a proposal.  When it has no legal schedule we move one
node at a time to another legal PE, keeping the rest of the proposal, and stop
at the first schedulable assignment.  The control is the same repair starting
from a random assignment: if both reach the same place with the same budget,
the model contributed nothing.
"""
from __future__ import annotations

import argparse, csv, json, os, random, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

import eval_llm_placement as ev  # noqa: E402
from cgra_agent.llm.cache import LLMCache, sha256_file  # noqa: E402
from cgra_agent.llm.client import LLMClient  # noqa: E402
from cgra_agent.llm.prompts import build_messages  # noqa: E402
from cgra_agent.tools.facts import build_facts  # noqa: E402
from cgra_agent.tools.feasible import feasible_pes, is_mem_op  # noqa: E402
from cgra_agent.tools.legalize import schedule  # noqa: E402

II = {"gemm_pace": 9}
UNRESOLVED = re.compile(r"unresolved nodes \[([0-9, ]+)\]")


def overloaded(spatial, ii):
    """Nodes on a PE that physics cannot fit (more nodes than phases)."""
    load = {}
    for node, xy in spatial.items():
        load.setdefault(xy, []).append(node)
    out = []
    for xy, nodes in load.items():
        if len(nodes) > ii:
            out.extend(sorted(nodes)[ii:])
    return out


def repair(facts, ii, spatial, budget=200, seed=0):
    rng = random.Random(seed)
    pool = feasible_pes(facts)
    s = dict(spatial)
    for step in range(1, budget + 1):
        try:
            return schedule(facts, ii, s), step
        except ValueError as exc:
            msg = str(exc)
            hot = [int(x) for x in (UNRESOLVED.search(msg).group(1).split(",") if UNRESOLVED.search(msg) else [])]
            if not hot:
                hot = overloaded(s, ii)
            if not hot:
                return None, step
            node = rng.choice(hot)
            choices = [xy for xy in pool.get(node, []) if xy != s.get(node)]
            if not choices:
                return None, step
            s[node] = rng.choice(choices)
    return None, budget


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernels", default="array_cond,hpcg,gemm,gemm_pace,conv2d,conv3,mults1,mac")
    ap.add_argument("--out", default=os.path.join(ROOT, "work/repair_space.csv"))
    a = ap.parse_args()
    client, cache = LLMClient(), LLMCache(os.path.join(ROOT, "work/llm-cache"))
    rows = []
    for kernel in [k for k in a.kernels.split(",") if k]:
        ii = II.get(kernel, 4)
        d = os.path.join(ROOT, "work/kernels", kernel)
        facts = build_facts(os.path.join(d, "dfg.xml"), os.path.join(d, "mem.json"), ii)
        try:
            text, _, _ = ev.call_or_cache(client, cache, kernel, sha256_file(os.path.join(d, "dfg.xml")),
                                          ii, build_messages(facts, ii), os.path.join(ROOT, "work/e2", kernel), "call1")
            pins = ev.parse_placement(text, ii).pins
            start = {int(p.node): (int(p.x), int(p.y)) for p in pins}
            origin = "llm"
        except Exception as exc:  # noqa: BLE001
            print(kernel, "no llm answer:", str(exc)[:60], flush=True)
            start, origin = {}, "random"
        pool = feasible_pes(facts)
        if not start:
            start = {int(n["idx"]): random.Random(0).choice(pool[int(n["idx"])]) for n in facts["nodes"]}
        for label, s0 in (("llm", start), ("random", {int(n["idx"]): random.Random(7).choice(pool[int(n["idx"])])
                                                      for n in facts["nodes"]})):
            best = None
            for seed in range(3):
                sch, steps = repair(facts, ii, s0, budget=200, seed=seed)
                if sch is None:
                    continue
                pins2 = [{"node": k, "x": v["x"], "y": v["y"], "t": v["t"]} for k, v in sorted(sch.items())]
                res = ev.mapper_run(kernel, pins2, "e2-%s-%d" % (label, seed), 1, ii)
                if res["ok"]:
                    lat = res.get("lat") or 10 ** 6
                    if best is None or lat < best[0]:
                        best = (lat, steps, seed)
            rows.append({"kernel": kernel, "ii": ii, "start": label, "mapped": bool(best),
                         "lat": best[0] if best else "", "steps": best[1] if best else "",
                         "seed": best[2] if best else ""})
            print("%-10s ii=%d start=%-6s %s lat=%s steps=%s" % (kernel, ii, label,
                  "ok" if best else "fail", best[0] if best else "", best[1] if best else ""), flush=True)
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print("wrote", a.out)


if __name__ == "__main__":
    main()