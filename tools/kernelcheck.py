"""Kernel-level attribution for the constant simulation mismatches.

`hpcg`, `mults1` and `gemm_pace` report the same mismatch count for every coverage
level, which points at the kernel/trace pair rather than at the routing.  This runs
the mapper's own A* (`-m 0`, no pins, no routes) and simulates it: if the plain
toolchain solution also mismatches, the kernel is `unverified(kernel-level)` and must
be excluded from routing conclusions.
"""

import glob
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
SIM = os.path.join(REPO, "toolchain/out/hycube_simulator")
KROOT = "/home/steve/cac-armA/work/kernels"
OUT = "work/armB/kernelcheck"
II, XD, YD = 4, 4, 4
KERNELS = [k for k in os.environ.get(
    "KERNELS_ENV", "array_add,hpcg,mults1,gemm_pace").split(",")]


def run_map(wd, ii):
    for stale in glob.glob(os.path.join(wd, "*_binary.bin")):
        os.remove(stale)
    cmd = [MAPPER, "-m", "0", "-d", "dfg.xml", "-x", str(XD), "-y", str(YD),
           "-j", "mem.json", "-i", str(ii), "-t", "HyCUBE_4REG", "--report", "map.json"]
    p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=1800)
    bins = glob.glob(os.path.join(wd, "*_binary.bin"))
    ok = False
    rep = {}
    try:
        rep = json.load(open(os.path.join(wd, "map.json")))
        ok = bool(rep.get("ok"))
    except (OSError, ValueError):
        pass
    return p.returncode, ok, rep, (bins[0] if bins else None)


def run_sim(wd, binpath, trace):
    cmd = [SIM, "-x", str(XD), "-y", str(YD), "-c", os.path.basename(binpath),
           "-d", trace, "-a", "mem_alloc.txt", "-m", "4096"]
    p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=1800)
    out = p.stdout + p.stderr
    m = re.search(r"Mismatches::(\d+)", out)
    if m:
        return int(m.group(1))
    return -1 if "Assertion" in out else -2


def main():
    rows = []
    for k in KERNELS:
        src = os.path.join(KROOT, k)
        if not os.path.exists(os.path.join(src, "dfg.xml")):
            print("SKIP", k)
            continue
        wd = os.path.join(OUT, k)
        os.makedirs(wd, exist_ok=True)
        for name in ("dfg.xml", "mem.json", "mem_alloc.txt"):
            tgt = os.path.join(wd, name)
            if not os.path.exists(tgt):
                with open(os.path.join(src, name), "rb") as fi, open(tgt, "wb") as fo:
                    fo.write(fi.read())
        for trace in sorted(glob.glob(os.path.join(src, "*-trace_*.txt")) +
                            glob.glob(os.path.join(src, "%s_trace_*.txt" % k))):
            local = os.path.join(wd, os.path.basename(trace))
            if not os.path.exists(local) or os.path.getsize(local) == 0:
                with open(trace, "rb") as fi, open(local, "wb") as fo:
                    fo.write(fi.read())
            rc, ok, rep, binpath = run_map(wd, II)
            if not binpath:
                rows.append((k, os.path.basename(trace), "no_bin", rc, "", ""))
                print("%-10s %-22s no binary (rc=%d)" % (k, os.path.basename(trace), rc), flush=True)
                continue
            mm = run_sim(wd, binpath, os.path.basename(local))
            rows.append((k, os.path.basename(trace), ok, rep.get("ii"), rep.get("lat"), mm))
            print("%-10s %-22s ok=%-5s II=%-3s lat=%-3s mismatches=%s" % (
                k, os.path.basename(trace), ok, rep.get("ii"), rep.get("lat"), mm), flush=True)
    with open(os.path.join(OUT, "kernelcheck.csv"), "w") as f:
        f.write("kernel,trace,map_ok,ii,lat,mismatches\n")
        for r in rows:
            f.write("%s,%s,%s,%s,%s,%s\n" % r)
    from collections import Counter
    print("----")
    print(Counter(r[0] for r in rows), Counter((r[0], r[5]) for r in rows))


if __name__ == "__main__":
    main()
