"""Does the variable->bank assignment change anything the mapper cares about?

The previous three experiments pinned *positions*.  This one moves one level up:
the DFG is untouched, the trace is untouched, only the bank layout in
``mem.json`` changes.  If the mapping quality (II / latency / simulated cycles)
moves with it, then the bank assignment is a real design variable and the LLM has
something to choose.  If every variant ties, the space is flat and the whole
direction is dead.

Ground truth is the simulator, not the mapper's own success flag.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

REPO = "/home/steve/CGRA_Agent_Compiler"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
SIM = os.path.join(REPO, "toolchain/out/hycube_simulator")
SKIP = ("loopstart", "loopend")


def banks_of(mem):
    out = {}
    for name, mod in mem["ARCH"].items():
        if name.startswith("SPM_B") and name.endswith("_WRAPPER"):
            idx = int(re.search(r"SPM_B(\d+)", name).group(1))
            for var, off in (mod.get("DATA_LAYOUT") or {}).items():
                out[var] = (idx, off)
    return out


def variants(kernel):
    base = json.load(open(os.path.join(REPO, "work/kernels", kernel, "mem.json")))
    layout = banks_of(base)
    n_banks = len({b for b, _ in layout.values()}) or 2
    vars_ = [v for v in layout if v not in SKIP]
    out = [("base", json.loads(json.dumps(base)))]
    for var in vars_:
        for b in range(n_banks):
            if b == layout[var][0]:
                continue
            m = json.loads(json.dumps(base))
            for name, mod in m["ARCH"].items():
                if name.startswith("SPM_B") and name.endswith("_WRAPPER"):
                    (mod.get("DATA_LAYOUT") or {}).pop(var, None)
            target = "SPM_B%d_WRAPPER" % b
            m["ARCH"][target].setdefault("DATA_LAYOUT", {})[var] = layout[var][1]
            out.append(("%s->B%d" % (var, b), m))
    return out


def run(kernel, mem, tag):
    kdir = os.path.join(REPO, "work/kernels", kernel)
    out = os.path.join(REPO, "work/bank", "%s-%s" % (kernel, tag.replace("->", "2")))
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    mem_path = os.path.join(out, "mem.json")
    json.dump(mem, open(mem_path, "w"), indent=1)
    dfg = os.path.join(kdir, "dfg.xml")
    m = subprocess.run(
        [MAPPER, "-d", dfg, "-x", "4", "-y", "4", "-j", mem_path, "-i", "4",
         "-t", "HyCUBE_4REG", "-m", "1"],
        cwd=out, capture_output=True, text=True, timeout=600)
    txt = (m.stdout or "") + (m.stderr or "")
    mm = re.search(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)", txt)
    if not mm:
        return "map-fail"
    ii, lat = int(mm.group(1)), int(mm.group(2))
    bins = glob.glob(os.path.join(out, "*binary.bin"))
    trace = sorted(glob.glob(os.path.join(REPO, "work/traces", kernel, "*_trace_*.txt")))
    alloc = os.path.join(REPO, "work/traces", kernel, "%s_mem_alloc.txt" % kernel)
    if not bins or not trace:
        return "II=%d lat=%d sim-skipped" % (ii, lat)
    s = subprocess.run([SIM, "-x", "4", "-y", "4", "-c", max(bins, key=os.path.getmtime),
                        "-d", trace[0], "-a", alloc, "-m", "4096"],
                       cwd=out, capture_output=True, text=True, timeout=600)
    t2 = (s.stdout or "") + (s.stderr or "")
    res = re.search(r"Matches::(\d+), Mismatches::(\d+)", t2)
    cyc = re.search(r"CGRA cycles: (\d+)", t2)
    if res:
        return "II=%d lat=%d cycles=%s %s/%s" % (
            ii, lat, cyc.group(1) if cyc else "?", res.group(1), res.group(2))
    return "II=%d lat=%d ABORT" % (ii, lat)


if __name__ == "__main__":
    for kernel in sys.argv[1:]:
        print("== %s" % kernel)
        for tag, mem in variants(kernel):
            print("   %-12s %s" % (tag, run(kernel, mem, tag)))
