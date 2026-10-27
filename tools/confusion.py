"""verify x sim 的 2x2 混淆矩阵（同一份 pins+routes 判两次）。

  格子                    含义
  consistent_pass         verify 过 + 仿真过
  over_permissive         verify 过 + 仿真崩/不一致      （我们过宽）
  static_disagreement     verify 拒 + 仿真过            （我们过严，单列，不算失败）
  consistent_reject       verify 拒 + 仿真也拒
  incomparable            两次落出的二进制 sha 不同，无法判"同一份映射"
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
import shutil
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.join(REPO, "tools"))

from ablation_fixii import (II, XD, YD, TIMEOUT, MAPPER,  # noqa: E402
                            REPO as HREPO, dfg_edges, instance, prep)
SIM = os.path.join(HREPO, 'toolchain/out/hycube_simulator')
from cgra_agent.routers.base import shortest_path  # noqa: E402

KERNELS = os.environ.get(
    "KERNELS_ENV", "array_add,array_cond,conv2d,conv3,gemm,gemm_pace,hpcg,mac,mults1,trmm"
).split(",")
PINS_MODE = int(os.environ.get("PINS_MODE_ENV", "3"))
OUT = "work/armB/confusion"
SRC = "work/armB/fixii"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bin_of(wd):
    hits = sorted(glob.glob(os.path.join(wd, "*_binary.bin")), key=os.path.getmtime)
    return hits[-1] if hits else ""


def mapper_run(dfg, mem, wd, pins_path, routes_path, verify, tag):
    # the mapper resolves every path relative to its cwd (it mangles absolute ones),
    # so pass bare file names and run inside the per-config work dir
    for stale in glob.glob(os.path.join(wd, "*_binary.bin")):
        os.remove(stale)
    rep_name = "%s_report.json" % tag
    cmd = [MAPPER, "-m", "3", "-d", os.path.basename(dfg), "-x", str(XD), "-y", str(YD),
           "-j", os.path.basename(mem), "-i", str(II), "-t", "HyCUBE_4REG",
           "--pins", os.path.basename(pins_path), "--routes", os.path.basename(routes_path),
           "--pins_mode", str(PINS_MODE)]
    cmd += ["--report", rep_name]
    if verify:
        cmd.append("--verify")
    p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=TIMEOUT)
    open(os.path.join(wd, "%s.log" % tag), "w").write(p.stdout + p.stderr)
    ok = False
    if verify:
        try:
            ok = bool(json.load(open(os.path.join(wd, rep_name))).get("ok"))
        except (OSError, ValueError):
            ok = False
    else:
        ok = p.returncode == 0
    return {"ok": ok, "rc": p.returncode, "bin": bin_of(wd)}


def _local(path, wd):
    """copy an input next to the run dir and return its bare name"""
    if not path or not os.path.exists(path):
        return ""
    name = os.path.basename(path)
    if not os.path.exists(os.path.join(wd, name)):
        shutil.copy2(path, os.path.join(wd, name))
    return name


def sim_run(binpath, wd, trace, alloc):
    if not binpath or not trace:
        return {"ok": None, "verdict": "no-sim", "cls": ""}
    ltr, lal = _local(trace, wd), _local(alloc, wd)
    if not ltr:
        return {"ok": None, "verdict": "no-sim", "cls": ""}
    p = subprocess.run([SIM, "-x", str(XD), "-y", str(YD), "-c", os.path.basename(binpath),
                        "-d", ltr, "-a", lal or "", "-m", "4096"],
                       cwd=wd, capture_output=True, text=True, timeout=TIMEOUT)
    out = p.stdout + p.stderr
    open(os.path.join(wd, "sim.log"), "w").write(out)
    m = re.search(r"Matches::(\d+)\s*,?\s*Mismatches::(\d+)", out)
    if m:
        mm = int(m.group(2))
        return {"ok": mm == 0, "verdict": "mm:%d" % mm,
                "cls": "" if mm == 0 else "semantic"}
    if "Assertion" in out or p.returncode != 0:
        line = next((ln for ln in out.splitlines() if "Assertion" in ln), "")
        return {"ok": False, "verdict": "abort", "cls": "runtime"}
    return {"ok": False, "verdict": "no-verdict", "cls": "runtime"}


def cheap_place(dfg_path, edges, ii):
    asap, op, cur = {}, {}, None
    for ln in open(dfg_path, errors="replace"):
        m = re.search(r'<Node idx="(\d+)".*?ASAP="(\d+)"', ln)
        if m:
            cur = int(m.group(1))
            asap[cur] = int(m.group(2))
        m2 = re.search(r'op="([A-Z_0-9]+)"', ln)
        if m2 and cur is not None:
            op.setdefault(cur, m2.group(1))
    nodes = sorted(asap)
    mem_ops = {"LOAD", "LOADB", "LOADH", "STORE", "STOREB", "STOREH", "LS"}
    banks = [(0, y) for y in range(YD)]
    inner = [(x, y) for x in range(1, XD) for y in range(YD)]
    pins, pos, ib, ii2 = [], {}, 0, 0
    for n in nodes:
        if op.get(n) in mem_ops:
            xy = banks[ib % len(banks)]
            ib += 1
        else:
            xy = inner[ii2 % len(inner)]
            ii2 += 1
        pos[n] = xy
        pins.append({"node": n, "x": xy[0], "y": xy[1], "t": asap.get(n, 0) % ii})
    routes = []
    for a, b in edges:
        if a in pos and b in pos:
            p = shortest_path(pos[a], pos[b], XD, YD)
            if p and len(p) > 1:
                routes.append({"from": a, "to": b, "path": p})
    return pins, routes


def classify(a, b, sim):
    """2x2 cell of the two judges for one (kernel, config).

    Both judges see the same request (same pins + same routes) and the simulator is
    fed the bitstream of the *unverified* run, so a verify rejection cannot hide the
    object under test.  verify says nothing when the mapping stage itself failed
    (no bitstream): those cells are `no_mapping`.
    """
    if not a["bin"]:
        return "no_mapping"
    if b["ok"]:
        if sim["ok"] is True:
            return "consistent_pass"
        if sim["ok"] is False:
            return "over_permissive"
        return "unverifiable"
    if sim["ok"] is True:
        return "static_disagreement"
    if sim["ok"] is False:
        return "consistent_reject"
    return "unverifiable"


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for kernel in KERNELS:
        inst = instance(kernel)
        if not inst:
            print("SKIP", kernel, flush=True)
            continue
        edges = dfg_edges(inst["dfg"])
        pj = os.path.join(SRC, kernel, "placement.json")
        if not os.path.exists(pj):
            print("SKIP %s (no placement.json)" % kernel, flush=True)
            continue
        ext = json.load(open(pj))
        pins_all = ext["pins"]
        bank = {tuple(int(x) for x in k.split(",")): v for k, v in ext["bank"].items()}
        lat = {int(k): v for k, v in ext["lat"].items()}
        for e in edges:
            if e not in bank:
                pa, pb = lat.get(e[0]), lat.get(e[1])
                if e[0] in ext["pos"] and e[1] in ext["pos"]:
                    p = shortest_path(tuple(ext["pos"][str(e[0])]), tuple(ext["pos"][str(e[1])]), XD, YD)
                    if p:
                        bank[e] = p

        trace = ""
        for base in (inst["dir"], os.path.join(SRC, kernel)):
            for cand in ("%s_trace_1.txt" % kernel, "%s_trace_0.txt" % kernel):
                c = os.path.join(base, cand)
                if os.path.exists(c) and os.path.getsize(c) > 0:
                    trace = c
                    break
            if trace:
                break

        for cfg in ("pins", "k25", "k50", "k100", "rule"):
            wd = os.path.join(OUT, kernel, cfg)
            os.makedirs(wd, exist_ok=True)
            dfg, mem, alloc = prep(wd, inst)
            if cfg == "rule":
                pl, routes = cheap_place(dfg, edges, II)
            else:
                frac = {"pins": 0.0, "k25": 0.25, "k50": 0.5, "k100": 1.0}[cfg]
                n = int(round(len(edges) * frac))
                pl = [{"node": p["node"], "x": p["x"], "y": p["y"], "t": p["t"]} for p in pins_all]
                routes = [{"from": e[0], "to": e[1], "path": bank[e]}
                          for e in edges[:n] if e in bank]
            pp = os.path.join(wd, "pins.json")
            rp = os.path.join(wd, "routes.json")
            json.dump({"pins": pl}, open(pp, "w"))
            json.dump({"routes": routes}, open(rp, "w"))

            a = mapper_run(dfg, mem, wd, pp, rp, False, "noverify")
            abin = os.path.join(wd, "A_bin.bin")
            if a["bin"] and os.path.exists(a["bin"]):
                shutil.copy2(a["bin"], abin)
                a["bin"] = abin
            b = mapper_run(dfg, mem, wd, pp, rp, True, "verify")
            same = bool(a["bin"] and b["bin"]) and sha(a["bin"]) == sha(b["bin"])
            sim = sim_run(a["bin"], wd, trace, alloc)

            cell = classify(a, b, sim)

            rows.append({"kernel": kernel, "config": cfg, "edges": len(edges),
                         "verify_ok": b["ok"], "sim_ok": sim["ok"],
                         "sim_verdict": sim["verdict"], "sim_class": sim["cls"],
                         "cell": cell, "bin_same": same,
                         "routes": len(routes), "pins": len(pl),
                         "lat": lat.get(0, "")})
            print("%-10s %-5s verify=%-5s sim=%-9s %s" % (
                kernel, cfg, b["ok"], sim["verdict"], cell), flush=True)

    if not rows:
        print("no rows")
        return
    with open(os.path.join(OUT, "confusion.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    CLASSES = ["consistent_pass", "over_permissive", "static_disagreement",
               "consistent_reject", "no_mapping", "unverifiable", "incomparable"]
    for c in CLASSES:
        print("%-20s %d" % (c, sum(1 for r in rows if r["cell"] == c)))
    print("-" * 40)
    print("2x2: verify (rows) vs simulator (columns), comparable cells only")
    for vok in (True, False):
        row = [sum(1 for r in rows if r["cell"] == k and r["verify_ok"] == vok)
               for k in ("consistent_pass", "over_permissive", "unverifiable")]
        print("verify=%-5s pass=%-3d fail=%-3d undecided=%-3d"
              % (vok, row[0], row[1], row[2]))


if __name__ == "__main__":
    main()
