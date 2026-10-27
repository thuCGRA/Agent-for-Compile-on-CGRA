"""Verifier soundness/completeness by mutation testing (trace-free).

Oracle: the mapper's own successful placement is, by construction, routable.
So the verifier MUST accept it (soundness).  We then inject known-illegal
mutations and require the verifier to reject them (detection).
"""
import json, os, subprocess, sys, tempfile, glob, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from cgra_agent.tools.facts import build_facts
from cgra_agent.tools.validate import validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "toolchain", "out", "cgra_xml_mapper")
KDIR = os.path.join(ROOT, "work", "kernels")
II = 4

def run_mapper(dfg, arch, ii):
    fd, rep = tempfile.mkstemp(suffix=".json"); os.close(fd)
    fp, pins = tempfile.mkstemp(suffix=".json"); os.close(fp)
    json.dump({"pins": []}, open(pins, "w"))
    # method 3 + empty pins + repair == plain PathFinder, but writes a report
    cmd = [EXE, "-d", dfg, "-x", "4", "-y", "4", "-j", arch, "-i", str(ii),
           "-t", "HyCUBE_4REG", "-m", "3", "--pins", pins, "--pins_mode", "3",
           "--report", rep]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    try:
        d = json.load(open(rep))
    except Exception:
        d = {"ok": False, "placement": [], "error": p.stdout[-300:] + p.stderr[-300:]}
    try: os.unlink(pins)
    except OSError: pass
    os.unlink(rep)
    return d

def mutate(facts, pl):
    """Return list of (name, mutated placement)."""
    out = []
    pe_all = [tuple(x) for x in facts["pe_all"]]
    pe_mem = {tuple(x) for x in facts["pe_mem"]}
    nodes = {n["idx"]: n for n in facts["nodes"]}
    mem_nodes = [p["node"] for p in pl if nodes.get(p["node"], {}).get("op") in ("LOAD", "STORE")]
    alu_pe = next((p for p in pe_all if p not in pe_mem), None)

    if mem_nodes and alu_pe:
        m = [dict(p) for p in pl]
        for p in m:
            if p["node"] == mem_nodes[0]:
                p["x"], p["y"] = alu_pe
        out.append(("bank", m))

    if len(pl) >= 2:
        m = [dict(p) for p in pl]
        a, b = 0, 1
        m[a]["t"], m[b]["t"] = pl[b]["t"], pl[a]["t"]
        out.append(("swap_t", m))

    m = [dict(p) for p in pl]
    for p in m:
        n = nodes.get(p["node"], {})
        if n.get("parents"):
            par = n["parents"][0]
            src = next((q for q in pl if q["node"] == par), None)
            if src is not None:
                p["t"] = src["t"]
                out.append(("dep0", m))
                break

    if len(pl) >= 2:
        m = [dict(p) for p in pl]
        m[0]["x"], m[0]["y"], m[0]["t"] = pl[1]["x"], pl[1]["y"], pl[1]["t"]
        out.append(("dup", m))

    m = [dict(p) for p in pl]
    m[0]["x"] = int(facts["xdim"])
    out.append(("bounds", m))
    return out

rows = []
for kd in sorted(glob.glob(os.path.join(KDIR, "*"))):
    k = os.path.basename(kd)
    dfg, arch = os.path.join(kd, "dfg.xml"), os.path.join(kd, "arch.json")
    if not (os.path.exists(dfg) and os.path.exists(arch)):
        continue
    rep = run_mapper(dfg, arch, II)
    ii_act = int(rep.get("ii") or II)
    try:
        facts = build_facts(dfg, arch, ii_act)
    except Exception as e:
        rows.append({"kernel": k, "error": "facts:%s" % e}); continue
    pl = rep.get("placement") or []
    if not rep.get("ok") or not pl:
        rows.append({"kernel": k, "error": "mapper:%s" % (rep.get("error") or rep.get("stage"))})
        continue
    base = validate(facts, pl, ii_act)
    row = {"kernel": k, "nodes": len(pl), "ii": ii_act, "baseline_errors": len(base["errors"]),
           "baseline_msgs": [str(e)[:60] for e in base["errors"]][:3],
           "detect": {}, "miss": {}}
    for name, mp in mutate(facts, pl):
        v = validate(facts, mp, ii_act)
        hit = len(v["errors"]) > 0
        row["detect"][name] = hit
        if not hit:
            row["miss"][name] = "accepted illegal mutation"
    rows.append(row)

out = os.path.join(ROOT, "work", "verifier_mutation.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(rows, open(out, "w"), indent=1, ensure_ascii=False)

print("%-12s %5s %8s %6s %s" % ("kernel", "nodes", "base_err", "detect", "missed"))
tot_d = tot_n = 0
for r in rows:
    if "error" in r:
        print("%-12s  --  %s" % (r["kernel"], r["error"])); continue
    d = sum(1 for v in r["detect"].values() if v); n = len(r["detect"])
    tot_d += d; tot_n += n
    print("%-12s %5d %8d %5d/%-2d %s" % (r["kernel"], r["nodes"], r["baseline_errors"], d, n,
          ",".join(k for k, v in r["detect"].items() if not v)))
print("TOTAL detection %d/%d" % (tot_d, tot_n))
bad = [r["kernel"] for r in rows if "error" not in r and r["baseline_errors"] > 0]
print("SOUNDNESS violations (mapper-ok rejected):", bad or "none")
