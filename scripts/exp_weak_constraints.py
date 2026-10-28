"""Weak constraints instead of exact pins.

The exact-pin interface failed for a structural reason (FINDINGS §17): a pin is a
promise about (x, y, t) that the mapper's DP candidate set may not honour, and
fixing time breaks space and vice versa.

This experiment tests the opposite end of the spectrum: the constraint set only
say "this node belongs to this bank" (memory nodes) — a commitment the mapper can
always realise — and leaves every ALU node to the mapper.  Three questions:

1. is the constraint set accepted (mapper repair mode drops nothing)?
2. does the result still simulate correctly?
3. what does it cost in latency vs -m 0?

If (1) and (2) hold, the interface is viable and the LLM's job becomes "which
banks / which regions", not "which PE".
"""
import glob
import json
import os
import re
import subprocess
import sys

REPO = "/home/steve/CGRA_Agent_Compiler"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
SIM = os.path.join(REPO, "toolchain/out/hycube_simulator")


def facts_of(kernel):
    """Bank layout lives in mem.json (arch.json is the pre-update copy)."""
    import sys
    sys.path.insert(0, os.path.join(REPO, "src"))
    from cgra_agent.tools.facts import build_facts

    kdir = os.path.join(REPO, "work/kernels", kernel)
    f = build_facts(os.path.join(kdir, "dfg.xml"), os.path.join(kdir, "mem.json"), 4)
    return f


def build_constraints(kernel, ii):
    f = facts_of(kernel)
    pe_mem = sorted(f["pe_mem"])
    node_bank = f.get("node_bank") or {}
    pins = []
    for n in sorted(f["nodes"], key=lambda n: n["idx"]):
        bank = node_bank.get(n["idx"])
        if bank is None or int(bank) >= len(pe_mem):
            continue
        x, y = pe_mem[int(bank)]
        pins.append({"node": int(n["idx"]), "x": int(x), "y": int(y),
                     "t": int(n.get("asap", 0)) % ii})
    return pins


def run(kernel, pins, tag, mode):
    kdir = os.path.join(REPO, "work/kernels", kernel)
    dfg = os.path.join(kdir, "dfg.xml")
    arch = os.path.join(kdir, "arch.json")
    alloc = os.path.join(REPO, "work/traces", kernel, "%s_mem_alloc.txt" % kernel)
    trace = sorted(glob.glob(os.path.join(REPO, "work/traces", kernel, "*_trace_*.txt")))
    out = os.path.join(REPO, "work/weak", "%s-%s" % (kernel, tag))
    os.makedirs(out, exist_ok=True)
    pins_path = os.path.join(out, "pins.json")
    json.dump({"pins": pins, "ii": 4, "source": "weak"}, open(pins_path, "w"), indent=1)
    rep = os.path.join(out, "report.json")
    cmd = [MAPPER, "-d", dfg, "-x", "4", "-y", "4", "-j", arch, "-i", "4",
           "-t", "HyCUBE_4REG", "-m", "3", "--pins", pins_path, "--report", rep]
    if mode:
        cmd += ["--pins_mode", str(mode)]
    m = subprocess.run(cmd, cwd=kdir, capture_output=True, text=True, timeout=900)
    txt = (m.stdout or "") + (m.stderr or "")
    mm = re.search(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)", txt)
    if not mm:
        return "map-fail rc=%d" % m.returncode
    ii, lat = int(mm.group(1)), int(mm.group(2))
    try:
        rep_json = json.load(open(rep))
    except Exception:
        rep_json = {}
    applied = rep_json.get("pins_applied")
    bins = glob.glob(os.path.join(kdir, "*binary.bin"))
    if not bins or not trace or not os.path.exists(alloc):
        return "II=%d lat=%d applied=%s sim-skipped" % (ii, lat, applied)
    binp = max(bins, key=os.path.getmtime)
    s = subprocess.run([SIM, "-x", "4", "-y", "4", "-c", binp, "-d", trace[0],
                        "-a", alloc, "-m", "4096"],
                       cwd=out, capture_output=True, text=True, timeout=900)
    t2 = (s.stdout or "") + (s.stderr or "")
    res = re.search(r"Matches::(\d+), Mismatches::(\d+)", t2)
    if res:
        return "II=%d lat=%d applied=%s matches=%s mismatches=%s" % (
            ii, lat, applied, res.group(1), res.group(2))
    if "Assertion" in t2:
        return "II=%d lat=%d applied=%s ABORT" % (ii, lat, applied)
    return "II=%d lat=%d applied=%s sim-rc=%d" % (ii, lat, applied, s.returncode)


if __name__ == "__main__":
    for kernel in sys.argv[1:]:
        pins = build_constraints(kernel, 4)
        print("%-10s weak pins=%d" % (kernel, len(pins)))
        for mode in (1, 3):
            print("   mode%d: %s" % (mode, run(kernel, pins, "m%d" % mode, mode)))
