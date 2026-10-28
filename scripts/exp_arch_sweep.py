"""Architecture sweep: does shrinking the array buy anything the mapper can use?

For each kernel we build NxN variants of the known-good 4x4 HyCUBE ADL, let
PathFinder solve them, replay its own placement through the pinned path (that is
the only route in this build that emits a bitstream) and simulate.

The number that matters is simulated cycles at the *same* functional pass bar —
a smaller array is only interesting if it still passes and uses fewer PEs, or if
it passes at a lower cycle count for the same reason.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from cgra_agent.tools.arch import make_arch  # noqa: E402

REPO = "/home/steve/CGRA_Agent_Compiler"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
SIM = os.path.join(REPO, "toolchain/out/hycube_simulator")
OUT = os.path.join(REPO, "work/arch")


def dump_to_pins(dump_path, pins_path):
    d = json.load(open(dump_path))
    pins = [{"node": int(p["node"]), "x": int(p["x"]), "y": int(p["y"]),
             "t": int(p["t"]) % int(d["ii"])} for p in d["placement"]]
    json.dump({"pins": pins}, open(pins_path, "w"))
    return len(pins), int(d["ii"])


def run(kernel, xdim, ydim):
    kdir = os.path.join(REPO, "work/kernels", kernel)
    tag = "%s-%dx%d" % (kernel, xdim, ydim)
    wd = os.path.join(OUT, tag)
    shutil.rmtree(wd, ignore_errors=True)
    os.makedirs(wd, exist_ok=True)

    arch = os.path.join(wd, "mem.json")
    try:
        make_arch(os.path.join(kdir, "mem.json"), xdim, ydim, arch)
    except ValueError as e:
        return "arch-invalid (%s)" % e

    base = [MAPPER, "-d", os.path.join(kdir, "dfg.xml"), "-x", str(xdim), "-y", str(ydim),
            "-j", arch, "-i", "4", "-t", "HyCUBE_4REG"]
    dump = os.path.join(wd, "p0.json")
    m = subprocess.run(base + ["-m", "0", "--dump-placement", dump],
                       cwd=wd, capture_output=True, text=True, timeout=900)
    txt = (m.stdout or "") + (m.stderr or "")
    mm = re.search(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)", txt)
    if not mm:
        return "map-fail"
    ii, lat = int(mm.group(1)), int(mm.group(2))

    pins = os.path.join(wd, "pins.json")
    try:
        n, _ = dump_to_pins(dump, pins)
    except Exception as e:  # noqa: BLE001 - report, do not hide
        return "II=%d lat=%d dump-unreadable (%s)" % (ii, lat, e)

    rep = os.path.join(wd, "report.json")
    m3 = subprocess.run(base + ["-m", "3", "--pins", pins, "--pins_mode", "1",
                                "--report", rep],
                        cwd=wd, capture_output=True, text=True, timeout=900)
    # the mapper writes the bitstream next to the DFG, not into the cwd
    bins = glob.glob(os.path.join(kdir, "dfg.xml_DP1_XDim=%d_YDim=%d_II=%d_MTP=1_binary.bin"
                                  % (xdim, ydim, ii)))
    trace = sorted(glob.glob(os.path.join(REPO, "work/traces", kernel, "*_trace_*.txt")))
    alloc = os.path.join(REPO, "work/traces", kernel, "%s_mem_alloc.txt" % kernel)
    if not bins:
        return "II=%d lat=%d pins=%d no-binary" % (ii, lat, n)
    if not trace or not os.path.exists(alloc):
        return "II=%d lat=%d sim-skipped" % (ii, lat)

    s = subprocess.run([SIM, "-x", str(xdim), "-y", str(ydim),
                        "-c", max(bins, key=os.path.getmtime),
                        "-d", trace[0], "-a", alloc, "-m", "4096"],
                       cwd=wd, capture_output=True, text=True, timeout=900)
    t2 = (s.stdout or "") + (s.stderr or "")
    res = re.search(r"Matches::(\d+), Mismatches::(\d+)", t2)
    cyc = re.search(r"CGRA cycles: (\d+)", t2)
    if res:
        return "II=%d lat=%d pins=%d cycles=%s %s/%s" % (
            ii, lat, n, cyc.group(1) if cyc else "?", res.group(1), res.group(2))
    return "II=%d lat=%d pins=%d ABORT" % (ii, lat, n)


if __name__ == "__main__":
    kernels = sys.argv[1:] or ["array_add", "conv3", "trmm"]
    for kernel in kernels:
        print("== %s" % kernel)
        for dim in (4, 3):
            print("   %dx%d  %s" % (dim, dim, run(kernel, dim, dim)))
