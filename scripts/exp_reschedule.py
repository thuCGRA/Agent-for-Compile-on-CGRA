"""Re-time the LLM's spatial placements and see whether the aborts go away.

Takes the cached pins from an eval run, throws away ``t``, computes a legal
schedule with :mod:`cgra_agent.tools.legalize`, then maps and simulates exactly
as before.  If the SELECT aborts disappear, the model was doing fine on space and
the missing piece was the scheduler -- which is a compiler's job anyway.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

REPO = "/home/steve/CGRA_Agent_Compiler"
sys.path.insert(0, os.path.join(REPO, "src"))
from cgra_agent.tools.facts import build_facts  # noqa: E402
from cgra_agent.tools.legalize import schedule, schedule_anneal, to_pins  # noqa: E402

MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
SIM = os.path.join(REPO, "toolchain/out/hycube_simulator")
CANDIDATES = ["work/eval-llm2", "work/eval-llm"]


def find_pins(kernel, mode="strict"):
    for root in CANDIDATES:
        p = os.path.join(REPO, root, kernel, mode, "pins.json")
        if os.path.exists(p):
            return p
    return None


def run(kernel, ii=4):
    src = find_pins(kernel)
    if not src:
        return "no pins"
    pins = json.load(open(src))["pins"]
    spatial = {int(p["node"]): (int(p["x"]), int(p["y"])) for p in pins}

    facts = build_facts(os.path.join(REPO, "work/kernels", kernel, "dfg.xml"),
                        os.path.join(REPO, "work/kernels", kernel, "mem.json"), ii)
    try:
        try:
            placement = schedule(facts, ii, spatial)
        except ValueError:
            placement = schedule_anneal(facts, ii, spatial)
    except ValueError as e:
        return "schedule-fail (%s)" % e

    wd = os.path.join(REPO, "work/resched", kernel)
    shutil.rmtree(wd, ignore_errors=True)
    os.makedirs(wd, exist_ok=True)
    pin_path = os.path.join(wd, "pins.json")
    json.dump({"pins": to_pins(placement)}, open(pin_path, "w"))

    kdir = os.path.join(REPO, "work/kernels", kernel)
    binary = os.path.join(kdir, "dfg.xml_DP1_XDim=4_YDim=4_II=%d_MTP=1_binary.bin" % ii)
    if os.path.exists(binary):
        os.remove(binary)
    base = [MAPPER, "-d", os.path.join(kdir, "dfg.xml"), "-x", "4", "-y", "4",
            "-j", os.path.join(kdir, "mem.json"), "-i", str(ii), "-t", "HyCUBE_4REG"]
    mode = "1"
    m = subprocess.run(base + ["-m", "3", "--pins", pin_path, "--pins_mode", mode],
                       cwd=wd, capture_output=True, text=True, timeout=900)
    txt = (m.stdout or "") + (m.stderr or "")
    mm = re.search(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)", txt)
    if not mm:
        # strict refused a pin the mapper cannot host; let it drop just those and
        # keep the schedule everywhere else
        mode = "2"
        m = subprocess.run(base + ["-m", "3", "--pins", pin_path, "--pins_mode", mode],
                           cwd=wd, capture_output=True, text=True, timeout=900)
        txt = (m.stdout or "") + (m.stderr or "")
        mm = re.search(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)", txt)
        if not mm:
            return "map-fail (strict and prefer both refuse)"
    latt = int(mm.group(2))
    if not os.path.exists(binary):
        return "II=%d lat=%d no-binary" % (int(mm.group(1)), latt)

    trace = sorted(glob.glob(os.path.join(REPO, "work/traces", kernel, "*_trace_*.txt")))
    alloc = os.path.join(REPO, "work/traces", kernel, "%s_mem_alloc.txt" % kernel)
    if not trace:
        return "II=%d lat=%d sim-skipped" % (int(mm.group(1)), latt)
    s = subprocess.run([SIM, "-x", "4", "-y", "4", "-c", binary,
                        "-d", trace[0], "-a", alloc, "-m", "4096"],
                       cwd=wd, capture_output=True, text=True, timeout=900)
    t2 = (s.stdout or "") + (s.stderr or "")
    res = re.search(r"Matches::(\d+), Mismatches::(\d+)", t2)
    cyc = re.search(r"CGRA cycles: (\d+)", t2)
    if res:
        return "II=%d lat=%d cycles=%s %s/%s" % (
            int(mm.group(1)), latt, cyc.group(1) if cyc else "?",
            res.group(1), res.group(2))
    return "II=%d lat=%d ABORT" % (int(mm.group(1)), latt)


if __name__ == "__main__":
    for kernel in sys.argv[1:] or ["array_add", "array_cond", "conv3", "gemm"]:
        print("%-10s %s" % (kernel, run(kernel)), flush=True)
