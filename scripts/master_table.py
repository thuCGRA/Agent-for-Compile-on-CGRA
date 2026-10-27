#!/usr/bin/env python3
"""Master table: kernel x pin策略, for the "what do pins buy" claim.

Rows per kernel:
  none        : mapper -m 0 (no pins)
  oracle      : mapper's own placement (--dump-placement) fed back, strict (1)
  memory      : only memory nodes (LOAD*/STORE*) of that placement, strict (1)
  memory-rep  : same memory pins, repair (3)

strict_accept = pins applied / pins pinned.  Simulation uses work/kernels traces
(best over up to four traces); simulator aborts are classified as toolchain.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
import shutil
import subprocess
import time

REPO = "/home/steve/cac-armA"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
SIM = os.path.join(REPO, "toolchain/out/hycube_simulator")
KROOT = os.path.join(REPO, "work/kernels")
OUT_CSV = os.path.join(REPO, "work/master_table.csv")
OUT_MD = os.path.join(REPO, "work/master_table.md")
TARGET_II = {"gemm_pace": 9}
MEMORY_OPS = {"LOAD", "LOADB", "LOADH", "LOADCL", "STORE", "STOREB", "STOREH"}

RESULT_RE = re.compile(r"Simulation Result: Matches::(\d+), Mismatches::(\d+)")
ELAPSED_RE = re.compile(r"Elapsed time in seconds:\s*(\d+)\s*ms")
SUCCESS_RE = re.compile(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)")


def run(cmd, cwd):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return proc


def map_once(kernel, ii, pins, pins_mode, tag):
    work = os.path.join(REPO, "work", "master", kernel, tag)
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    for name in ("dfg.xml", "mem.json"):
        shutil.copy(os.path.join(KROOT, kernel, name), os.path.join(work, name))
    cmd = [MAPPER, "-d", "dfg.xml", "-x", "4", "-y", "4", "-j", "mem.json",
           "-i", str(ii), "-t", "HyCUBE_4REG", "-m", "3" if pins else "0",
           "--report", "rep.json", "--dump-placement", "dump.json"]
    if pins:
        with open(os.path.join(work, "pins.json"), "w", encoding="utf-8") as fh:
            json.dump({"pins": pins}, fh)
        cmd += ["--pins", "pins.json", "--pins_mode", str(pins_mode)]
    proc = run(cmd, work)
    report = {}
    rp = os.path.join(work, "rep.json")
    if os.path.isfile(rp):
        try:
            report = json.load(open(rp, encoding="utf-8"))
        except Exception:
            report = {}
    m = ELAPSED_RE.search(proc.stdout)
    ms = int(m.group(1)) / 1000.0 if m else 0.0
    s = SUCCESS_RE.search(proc.stdout)
    if s:
        report["ii"] = int(s.group(1))
        report["lat"] = int(s.group(2))
    binaries = sorted(glob.glob(os.path.join(work, "*_binary.bin")), key=os.path.getmtime)
    return work, report, ms, (binaries[-1] if binaries else ""), proc


def dump_pins(path):
    if not os.path.isfile(path):
        return []
    d = json.load(open(path, encoding="utf-8"))
    return [{"node": int(e["node"]), "x": int(e["x"]), "y": int(e["y"]),
             "t": int(e["t"]), "op": e.get("op")} for e in d.get("placement") or []]


def sim_best(kernel, binary, alloc):
    traces = sorted(glob.glob(os.path.join(KROOT, kernel, "%s_trace_*.txt" % kernel)))
    best = None
    crash = ""
    for trace in traces:
        proc = run([SIM, "-x", "4", "-y", "4", "-c", binary, "-d", trace,
                    "-a", alloc, "-m", "4096"], os.path.dirname(binary) or ".")
        if proc.returncode != 0:
            crash = (proc.stderr or proc.stdout).strip().splitlines()[-1][:80]
            continue
        m = RESULT_RE.search(proc.stdout)
        if not m:
            continue
        matches, mism = int(m.group(1)), int(m.group(2))
        cand = (mism, -matches, os.path.basename(trace), matches)
        if best is None or cand < best:
            best = cand
        if mism == 0:
            break
    if best is None:
        return {"matches": "", "mismatches": "", "trace": "", "status": "sim-crash", "note": crash}
    mism, _neg, trace, matches = best
    return {"matches": matches, "mismatches": mism, "trace": trace,
            "status": "verified" if mism == 0 else "unverified", "note": ""}


def main():
    kernels = sorted(os.listdir(KROOT))
    rows = []
    for k in kernels:
        if not os.path.isfile(os.path.join(KROOT, k, "dfg.xml")):
            continue
        ii = TARGET_II.get(k, 4)
        alloc = os.path.join(KROOT, k, "mem_alloc.txt")
        work0, rep0, ms0, bin0, proc0 = map_once(k, ii, None, 0, "none")
        orc = dump_pins(os.path.join(work0, "dump.json"))
        mem = [p for p in orc if p.get("op") in MEMORY_OPS]
        modes = [("none", None, 0, work0, rep0, ms0, bin0, proc0),
                 ("oracle", orc, 1, None, None, None, None, None),
                 ("memory", mem, 1, None, None, None, None, None),
                 ("memory-rep", mem, 3, None, None, None, None, None)]
        for mode, pins, pmode, w, r, ms, b, pr in modes:
            if w is None:
                w, r, ms, b, pr = map_once(k, ii, pins, pmode, mode)
            total = r.get("pins_total", len(pins) if pins else 0)
            applied = r.get("pins_applied", 0)
            ok = bool(r.get("ok")) or bool(SUCCESS_RE.search(pr.stdout))
            accept = "—" if mode == "none" else "%d/%d" % (applied, total)
            info = {"matches": "", "mismatches": "", "trace": "", "status": "no-map", "note": ""}
            if ok and b:
                info = sim_best(k, b, alloc)
            rows.append({
                "kernel": k, "mode": mode, "ii": r.get("ii", "") if ok else "",
                "map_ok": "yes" if ok else "no",
                "lat": r.get("lat", "") if ok else "",
                "strict_accept": accept,
                "pins_mode": "" if mode == "none" else pmode,
                "mapper_time_s": "%.2f" % ms,
                "sim": info["status"],
                "sim_trace": info.get("trace", ""),
                "matches": info.get("matches", ""),
                "mismatches": info.get("mismatches", ""),
                "note": info.get("note", ""),
            })
            print("%-10s %-10s map=%-3s II=%-2s lat=%-3s accept=%-6s %5.2fs %s %s/%s %s" % (
                k, mode, rows[-1]["map_ok"], rows[-1]["ii"], rows[-1]["lat"],
                accept, ms, info["status"], info.get("matches", ""),
                info.get("mismatches", ""), info.get("note", "")))

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("wrote", OUT_CSV)


if __name__ == "__main__":
    raise SystemExit(main())
