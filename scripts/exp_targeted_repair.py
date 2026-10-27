#!/usr/bin/env python3
"""Targeted-repair experiment: does the model move only the nodes the tool names?

Reuses the eval harness (same cache, same mapper wrapper).  For every kernel
whose first placement fails, call 2 now carries an explicit CONFLICT list and an
order to keep every other node verbatim.  We then measure how many nodes moved,
how many of those were on the list, and whether the mapping/simulation改善.
"""
import importlib.util, json, os, re, sys

ROOT = "/home/steve/CGRA_Agent_Compiler"
spec = importlib.util.spec_from_file_location("ev", os.path.join(ROOT, "scripts/eval_llm_placement.py"))
ev = importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)

from cgra_agent.llm.cache import LLMCache, cache_key, prompt_sha, sha256_file
from cgra_agent.llm.client import LLMClient
from cgra_agent.llm.prompts import build_messages
from cgra_agent.tools.facts import build_facts

KERNELS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["gemm", "conv2d", "gemm_pace", "hpcg", "mac", "mults1"]
client = LLMClient(); cache = LLMCache(ev.CACHE)

def roundtrip(facts, ii, kernel, dfg_sha, messages, out_dir, tag):
    key = cache_key(kernel, dfg_sha, ii, prompt_sha(messages))
    hit = None if os.environ.get("FOCUS_NOCACHE") else cache.get(key)
    if hit is not None and (hit.get("response") or "").strip():
        return hit.get("response", ""), True
    text = ""
    for attempt in range(3):
        try:
            text = client.chat(messages, temperature=0.0, max_tokens=8192)
        except Exception as exc:
            print("  call error:", exc)
            text = ""
        if (text or "").strip():
            break
        print("  empty response, retry", attempt + 1)
    cache.put(key, text, usage=(client.records[-1].get("usage") or {}))
    os.makedirs(out_dir, exist_ok=True)
    json.dump({"messages": messages, "response": text}, open(os.path.join(out_dir, tag + ".json"), "w"), indent=1)
    return text, False

rows = []
for kernel in KERNELS:
    dfg = os.path.join(ev.KROOT, kernel, "dfg.xml")
    arch = os.path.join(ev.KROOT, kernel, "mem.json")
    ii = 4
    facts = build_facts(dfg, arch, ii)
    dfg_sha = sha256_file(dfg)
    out = os.path.join(ev.WORK, kernel, "llm")
    raw1, c1 = roundtrip(facts, ii, kernel, dfg_sha, build_messages(facts, ii), out, "call1")
    p1 = ev.parse_placement(raw1, ii)
    pins1 = {p.node: (p.x, p.y, p.t) for p in p1.pins}
    strict = ev.mapper_run(kernel, [dict(node=p.node, x=p.x, y=p.y, t=p.t) for p in p1.pins], "strict", 1, ii)
    row = {"kernel": kernel, "strict1": strict["ok"], "n": len(pins1)}
    if strict["ok"]:
        row["note"] = "already ok at call 1"
        rows.append(row); print(json.dumps(row)); continue
    err = strict["error"] or ""
    conflict = sorted({int(m) for m in re.findall(r"node (\d+)", err)})
    prev = {"pins": [{"node": n, "x": v[0], "y": v[1], "t": v[2]} for n, v in sorted(pins1.items())],
            "reasoning": p1.note}
    msgs2 = build_messages(facts, ii, feedback=err, previous=prev, focus_nodes=conflict)
    raw2, c2 = roundtrip(facts, ii, kernel, dfg_sha, msgs2, out, "call2_focus")
    try:
        p2 = ev.parse_placement(raw2, ii)
    except Exception as exc:
        row.update(conflict=conflict, parse_error=str(exc)); rows.append(row); print(json.dumps(row)); continue
    pins2 = {p.node: (p.x, p.y, p.t) for p in p2.pins}
    moved = [n for n in pins2 if n in pins1 and pins2[n] != pins1[n]]
    on_list = [n for n in moved if n in conflict]
    off_list = [n for n in moved if n not in conflict]
    strict2 = ev.mapper_run(kernel, [dict(node=p.node, x=p.x, y=p.y, t=p.t) for p in p2.pins], "strict", 1, ii)
    sim = ev.sim_run(kernel, os.path.join(ev.WORK, kernel, "strict")) if strict2["ok"] else {}
    row.update(conflict=conflict, moved=len(moved), moved_on_list=len(on_list),
               moved_off_list=len(off_list), strict2=strict2["ok"],
               sim=(None if not sim else (sim.get("mismatches"), sim.get("cycles"))),
               cached=(c1, c2))
    rows.append(row); print(json.dumps(row))

os.makedirs(os.path.join(ev.WORK, "focus"), exist_ok=True)
json.dump(rows, open(os.path.join(ev.WORK, "focus", "rows.json"), "w"), indent=1)
print("\n%-10s %-6s %-8s %-8s %-9s %-6s %s" % ("kernel", "strict1", "conflict", "moved", "off_list", "strict2", "sim(mm,cyc)"))
for r in rows:
    print("%-10s %-6s %-8s %-8s %-9s %-6s %s" % (r["kernel"], r.get("strict1"), str(r.get("conflict")), r.get("moved"), r.get("moved_off_list"), r.get("strict2"), r.get("sim")))