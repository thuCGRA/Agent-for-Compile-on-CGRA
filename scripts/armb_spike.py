#!/usr/bin/env python3
"""Arm B spike: does the mapper's verify mode accept externally given routes?

The verify mode (`cgra_xml_mapper --verify`) takes a whole placement as *pins*
and a set of *routes* (one PE coordinate sequence per DFG edge), places every
node on its pinned PE at its pinned cycle, realises each given route on the
port graph, and writes a JSON report.  Everything the arm B router would have
to produce is therefore checked by the same code that maps and prints the
HyCUBE binary.

Usage:
    python3 scripts/armb_spike.py bootstrap   # copy the array_add fixtures
    python3 scripts/armb_spike.py matrix      # positive + negative cases
    python3 scripts/armb_spike.py reinject    # replay the toolchain's own routes

The toolchain must be built first (toolchain/build.sh) and the smoke run must
have produced work/smoke/array_add_mem.json (scripts/smoke.sh).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

DFG = "array_add_PartPredDFG.xml"
ARCH = "array_add_mem.json"
ALLOC = "array_add_mem_alloc.txt"
TRACE = "array_add_trace_0.txt"
II = 4
BIN_GLOB = re.compile(r".*_II=%d_MTP=1_binary\.bin$" % II)
# the mapper prints one [route] line per edge
ROUTE_RE = re.compile(r"^\[route\] (\d+)->(\d+) \((\d+) ports\):(.*)$")
PE_RE = re.compile(r"PE_(?:MEM_)?X(\d+)\|?_Y(\d+)")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "toolchain", "out")
LIB = os.path.join(ROOT, "toolchain", "vendor", "simulator", "test", "library")
SMOKE = os.path.join(ROOT, "work", "smoke")
WORK = os.path.join(ROOT, "work", "armB", "spike")


def tool(name):
    p = os.path.join(OUT, name)
    if not os.path.exists(p):
        sys.exit("missing %s: run toolchain/build.sh first" % p)
    return p


def bootstrap(args):
    os.makedirs(WORK, exist_ok=True)
    # the memory allocation must match the generated mem.json: prefer the copy
    # the smoke pipeline produced, fall back to the upstream library one
    for f in (DFG, ALLOC, TRACE):
        src = os.path.join(SMOKE, f)
        if not os.path.exists(src):
            src = os.path.join(LIB, f)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(WORK, f))
    arch = args.arch or (os.path.join(SMOKE, ARCH) if os.path.exists(os.path.join(SMOKE, ARCH))
                         else os.path.join(WORK, ARCH))
    if not os.path.exists(arch):
        sys.exit("missing %s: run scripts/smoke.sh (or pass --arch)" % arch)
    shutil.copyfile(arch, os.path.join(WORK, ARCH))
    print("fixtures in", WORK)

    # the pin set of every case is the placement the internal PathFinder
    # produces, so record it once (mode 3 without --verify = pins ignored)
    rep = os.path.join(WORK, "base_report.json")
    if not os.path.exists(rep):
        log = os.path.join(WORK, "base_mapper.log")
        with open(log, "w") as fh:
            rc = subprocess.call([tool("cgra_xml_mapper"), "-d", DFG, "-x", "4", "-y", "4",
                                  "-j", ARCH, "-i", "2", "-t", "HyCUBE_4REG", "-m", "3",
                                  "--report", rep], cwd=WORK, stdout=fh, stderr=subprocess.STDOUT)
        if rc != 0 or not os.path.exists(rep):
            sys.exit("baseline mapper run failed, see " + log)
        r = json.load(open(rep))
        print("baseline: ok=%s II=%d nodes=%d" % (r["ok"], r["ii"], len(r["placement"])))


def base_placement():
    """The PathFinder placement, used as the pin set of every case."""
    rep = os.path.join(WORK, "base_report.json")
    if not os.path.exists(rep):
        sys.exit("missing %s: record the baseline run first" % rep)
    r = json.load(open(rep))
    return [{"node": e["node"], "x": e["x"], "y": e["y"], "t": e["t"], "lat": e["lat"]}
            for e in r["placement"]]


def run_case(name, pins, routes, arch=ARCH):
    for f in sorted(os.listdir(WORK)):
        if BIN_GLOB.match(f):
            os.remove(os.path.join(WORK, f))
    js = os.path.join(WORK, name + "_pins.json")
    rj = os.path.join(WORK, name + "_routes.json")
    rep = os.path.join(WORK, name + "_report.json")
    json.dump({"pins": pins}, open(js, "w"), indent=1)
    json.dump({"routes": routes}, open(rj, "w"), indent=1)
    cmd = [tool("cgra_xml_mapper"), "-d", DFG, "-x", "4", "-y", "4", "-j", arch,
           "-i", "2", "-t", "HyCUBE_4REG", "-m", "3",
           "--pins", js, "--routes", rj, "--report", rep, "--verify"]
    log = os.path.join(WORK, name + "_mapper.log")
    with open(log, "w") as fh:
        rc = subprocess.call(cmd, cwd=WORK, stdout=fh, stderr=subprocess.STDOUT)
    r = json.load(open(rep))

    binary = None
    for f in sorted(os.listdir(WORK)):
        if BIN_GLOB.match(f):
            binary = os.path.join(WORK, f)
    kept = None
    if binary and os.path.getsize(binary) > 0:
        kept = os.path.join(WORK, name + "_binary.bin")
        shutil.copyfile(binary, kept)
        os.remove(binary)

    sim = "-"
    if kept and rc == 0:
        slog = os.path.join(WORK, name + "_sim.log")
        with open(slog, "w") as fh:
            subprocess.call([tool("hycube_simulator"), "-x", "4", "-y", "4", "-c", kept,
                             "-d", TRACE, "-a", ALLOC, "-m", "4096"],
                            cwd=WORK, stdout=fh, stderr=subprocess.STDOUT)
        txt = open(slog).read()
        hits = re.search(r"Matches::\s*(\d+)", txt)
        miss = re.search(r"Mismatches::\s*(\d+)", txt)
        if hits and miss:
            sim = "Matches::%s, Mismatches::%s" % (hits.group(1), miss.group(1))
            if miss.group(1) != "0":
                sim += " (MISMATCH)"
        else:
            sim = "no simulator report (FAIL)"
    first = ""
    if r.get("conflicts"):
        c = r["conflicts"][0]
        first = "%s: %s" % (c.get("type"), c.get("msg"))
    digest = hashlib.sha256(open(kept, "rb").read()).hexdigest()[:12] if kept else "-"
    print("%-6s exit=%d ok=%-5s pins=%d/%d routes=%d/%d bin=%-13s sim=%s"
          % (name, rc, r["ok"], r["pins_applied"], r["pins_total"],
             r["routes_applied"], r["routes_total"], digest, sim))
    if first:
        print("       conflict[0] " + first)
    return r, kept


def matrix(args):
    pins = base_placement()
    run_case("exp1", pins, [])
    # 11 (LOADB, (0,1)) -> 12 (CMERGE, (1,1)): one east hop, no slack
    run_case("exp2", pins, [{"from": 11, "to": 12, "path": [[0, 1], [1, 1]]}])
    # 11 -> 12 again, but over (0,2) and (1,2): three combinational hops inside
    # the single cycle the edge has, which the router never picks.  The
    # simulator accepts it, so the binary really does change.
    run_case("exp3", pins, [{"from": 11, "to": 12,
                             "path": [[0, 1], [0, 2], [1, 2], [1, 1]]}])
    # negative: a path whose second hop is not a single PE step
    run_case("neg1", pins, [{"from": 0, "to": 16, "path": [[1, 1], [3, 3], [2, 2]]}])
    # negative: a path that ends somewhere else than the pinned consumer
    run_case("neg2", pins, [{"from": 11, "to": 12, "path": [[0, 1], [0, 2]]}])
    # negative: a placement that schedules a consumer before its producer
    bad = [dict(p) for p in pins]
    for p in bad:
        if p["node"] == 12:
            p["lat"] = 0
            p["t"] = 0
    run_case("neg3", bad, [])
    # negative: a five hop detour for 4 -> 5 that ends up double booking a
    # crossbar port of a node that already routes in that cycle
    pins5 = pins
    run_case("neg4", pins5, [{"from": 4, "to": 5,
                              "path": [[1, 0], [0, 0], [0, 1], [0, 2], [0, 3]]}])


def parse_route_log(name):
    routes = {}
    for line in open(os.path.join(WORK, name)):
        m = ROUTE_RE.match(line.strip())
        if not m:
            continue
        coords = []
        for port in m.group(4).split(" "):
            pm = PE_RE.search(port)
            if not pm:
                continue
            xy = (int(pm.group(1)), int(pm.group(2)))
            if not coords or coords[-1] != xy:
                coords.append(xy)
        routes[(int(m.group(1)), int(m.group(2)))] = coords
    return routes


def reinject(args):
    """Replay the A* routes of exp1 as coordinate sequences.

    If the interface is sound, the coordinates the internal router chose are
    accepted, the mapping is the same, and the binary is byte identical.
    """
    pinned = {}
    r = json.load(open(os.path.join(WORK, "exp1_report.json")))
    for e in r["placement"]:
        pinned[e["node"]] = (e["x"], e["y"])
    routes = parse_route_log("exp1_mapper.log")
    payload = []
    for (src, dst), coords in sorted(routes.items()):
        if dst not in pinned or src not in pinned:
            continue
        if coords and coords[-1] != pinned[dst]:
            coords = coords + [pinned[dst]]
        payload.append({"from": src, "to": dst, "path": [list(c) for c in coords]})
    print("replaying %d routes (%d parsed)" % (len(payload), len(routes)))
    run_case("reinj", base_placement(), payload)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["bootstrap", "matrix", "reinject"])
    ap.add_argument("--arch", help="path to the memory dependence json for array_add")
    a = ap.parse_args()
    {"bootstrap": bootstrap, "matrix": matrix, "reinject": reinject}[a.cmd](a)


if __name__ == "__main__":
    main()
