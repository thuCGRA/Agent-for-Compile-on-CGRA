#!/usr/bin/env python3
"""pass@k: is one call enough, or is the model just a sampler?

For each kernel draw k independent placements at temperature 0.8 (seed = draw),
map each strictly, and report how many draws are legal and what latency they
get.  No feedback, no repair -- this isolates *sampling* from *repair*.
"""
from __future__ import annotations

import argparse, csv, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from cgra_agent.interfaces import Placement
from cgra_agent.llm.client import LLMClient, LLMError
from cgra_agent.llm.prompts import build_messages
from cgra_agent.placers.llm import PlacementSchemaError, extract_json_object
from cgra_agent.tools.facts import build_facts

KERNELS = ["array_add", "array_cond", "trmm", "hpcg", "gemm", "conv2d", "conv3", "mults1"]


def parse(text, ii):
    obj = extract_json_object(text)
    return [{"node": int(p["node"]), "x": int(p["x"]), "y": int(p["y"]), "t": int(p["t"])}
            for p in obj["pins"]]


def map_strict(kernel, pins, ii):
    d = os.path.join(ROOT, "work/kernels", kernel)
    tmp = os.path.join(ROOT, "work/passk-%s.json" % kernel)
    json.dump({"pins": pins}, open(tmp, "w"))
    import subprocess
    r = subprocess.run([os.path.join(ROOT, "toolchain/out/cgra_xml_mapper"),
                        "-d", os.path.join(d, "dfg.xml"), "-x", "4", "-y", "4",
                        "-j", os.path.join(d, "mem.json"), "-i", str(ii),
                        "-t", "HyCUBE_4REG", "-m", "3", "--pins", tmp, "--pins_mode", "1"],
                       cwd=os.path.join(ROOT, "work"), capture_output=True, text=True, timeout=1200)
    out = r.stdout + r.stderr
    ok = "Map Success" in out
    import re
    m = re.search(r"Map Success with II = (\d+) \(lat = (\d+)\)", out)
    return ok, (int(m.group(2)) if m else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--ii", type=int, default=4)
    ap.add_argument("--kernels", default=",".join(KERNELS))
    ap.add_argument("--out", default=os.path.join(ROOT, "work/passk.csv"))
    a = ap.parse_args()
    client = LLMClient()
    rows = []
    for kernel in [k for k in a.kernels.split(",") if k]:
        d = os.path.join(ROOT, "work/kernels", kernel)
        facts = build_facts(os.path.join(d, "dfg.xml"), os.path.join(d, "mem.json"), a.ii)
        oks, lats, errs = 0, [], 0
        for draw in range(a.k):
            try:
                text = client.chat(build_messages(facts, a.ii), temperature=0.8,
                                   max_tokens=16384, seed=draw)
                p = parse(text, a.ii)
            except (LLMError, PlacementSchemaError, KeyError, ValueError) as exc:
                errs += 1
                print("  %s#%d  no answer: %s" % (kernel, draw, str(exc)[:80]), flush=True)
                continue
            ok, lat = map_strict(kernel, p, a.ii)
            oks += 1 if ok else 0
            if ok and lat is not None:
                lats.append(lat)
            print("  %s#%d  %s lat=%s" % (kernel, draw, "ok" if ok else "no", lat), flush=True)
        rows.append({"kernel": kernel, "k": a.k, "legal": oks, "no_answer": errs,
                     "best_lat": min(lats) if lats else "", "lats": " ".join(str(x) for x in lats)})
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    for r in rows:
        print("%-10s legal %d/%d  no_answer %d  best_lat %s" % (r["kernel"], r["legal"], r["k"], r["no_answer"], r["best_lat"]))


if __name__ == "__main__":
    main()