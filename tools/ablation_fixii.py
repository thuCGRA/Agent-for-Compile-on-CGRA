"""Fixed-II coverage ablation on real kernels (4x4, II=4, k in 0/25/50/100).

Why fixed II: the mapper usually buys success by raising II (docs/ARM_B_KNEE.md).
A compiler is given an II, so the ablation pins II and varies only "who decides the
routing of k% of the edges".

Per kernel (real dfggen output taken from arm A's pipeline runs, so the same DFG and
arch the traces were produced against):
  m0      pure A* (`-m 0 -i 4`), 3 reps -> time/IQR, placement + A*'s own paths
  pins    all nodes pinned, no routes (`-m 3`), A* still decides every route
  k25/50/100  all nodes pinned + that fraction of edges given a fixed path
              (RulePolicy, path_mode="bank", rule="critical")
Each cell runs 3 reps; timing is reported as median [IQR].

Usage: python3 tools/ablation_fixii.py [--stage probe|full] [--reps 3]
"""
import argparse
import glob
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

REPO = "/home/steve/cac-armB"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
OUT = os.path.join(REPO, "work/armB/fixii")
ARMA = "/home/steve/cac-armA/work/armA"
KERNELS = os.environ.get("KERNELS_ENV", "array_add,conv2d,conv3,mac,mults1").split(",")
KERNELS_ROOT = os.environ.get("KERNELS_ROOT", "")
II, XD, YD = 4, 4, 4
TIMEOUT = 300

TILE_RE = re.compile(r"PE(?:_MEM)?_X(\d+)\|?_Y(\d+)")
NODE_RE = re.compile(r"^node=(\d+),mapped=PE(?:_MEM)?_X(\d+)\|?_Y(\d+)[^\s]*-T(\d+)")


# ------------------------------------------------------------------ instances

def instance(kernel):
    if KERNELS_ROOT:
        kd = os.path.join(KERNELS_ROOT, kernel)
        dfg = os.path.join(kd, "dfg.xml")
        mem = os.path.join(kd, "mem.json")
        if os.path.exists(dfg) and os.path.exists(mem):
            alloc = os.path.join(kd, "mem_alloc.txt")
            return {"kernel": kernel, "dfg": dfg, "mem": mem, "dir": kd,
                    "alloc": alloc if os.path.exists(alloc) else ""}
    ps = sorted(glob.glob(os.path.join(ARMA, kernel, "*", "dfg", "*_PartPredDFG.xml")),
                key=os.path.getmtime)
    if not ps:
        return None
    dfg = ps[-1]
    d = os.path.dirname(dfg)
    mem = glob.glob(os.path.join(d, "*_mem.json"))
    return {"kernel": kernel, "dfg": dfg, "mem": mem[0] if mem else "", "dir": d}


def dfg_edges(path):
    edges, cur = [], None
    for ln in open(path, errors="replace"):
        m = re.search(r'<Node idx="(\d+)"', ln)
        if m:
            cur = int(m.group(1))
            continue
        m = re.search(r'<Input idx="(\d+)"', ln)
        if m and cur is not None:
            edges.append((int(m.group(1)), cur))
    return edges


def parse_routeinfo(text):
    """-> (place {node: (x, y, lat)}, paths {(p, n): [(x, y), ...]})"""
    place, paths = {}, {}
    node = par = None
    seq = []

    def flush():
        if node is not None and par is not None and seq:
            ded = [seq[0]]
            for c in seq[1:]:
                if c != ded[-1]:
                    ded.append(c)
            paths[(par, node)] = ded

    for ln in text.splitlines():
        m = NODE_RE.match(ln)
        if m:
            flush()
            node, x, y, t = (int(m.group(i)) for i in range(1, 5))
            place[node] = (x, y, t)
            par, seq = None, [(x, y)]
            continue
        pm = re.match(r"\s*parent:(\d+)", ln)
        if pm:
            flush()
            par, seq = int(pm.group(1)), []
            continue
        if par is not None:
            for mm in TILE_RE.finditer(ln):
                seq.append((int(mm.group(1)), int(mm.group(2))))
    flush()
    return place, paths


# ------------------------------------------------------------------ mapper

def run(cmd, wd, log_name):
    t0 = time.time()
    to = False
    with open(os.path.join(wd, log_name), "w") as f:
        try:
            p = subprocess.run(cmd, cwd=wd, stdout=f, stderr=subprocess.STDOUT, timeout=TIMEOUT)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            to, rc = True, -1
    wall = time.time() - t0
    log = open(os.path.join(wd, log_name), errors="replace").read()
    return wall, rc, log, to


def parse_log(log, to):
    r = {"mappable": False, "ii": "", "lat": "", "stage": ""}
    s = re.search(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)", log)
    if s:
        r.update(mappable=True, ii=int(s.group(1)), lat=int(s.group(2)), stage="success")
    else:
        inc = re.findall(r"Increasing II to (\d+)", log)
        r["stage"] = "timeout" if to else ("ii_upperbound" if "upperbound II" in log
                                          else ("fail" if "Map Fail" in log else "crash"))
        r["last_ii"] = inc[-1] if inc else ""
    return r


def prep(wd, inst):
    """Copy the dfg/arch into the run dir: the mapper writes its side files next to the
    DFG, so running against arm A's copy would overwrite their artefacts."""
    os.makedirs(wd, exist_ok=True)
    dfg = os.path.join(wd, os.path.basename(inst["dfg"]))
    mem = os.path.join(wd, os.path.basename(inst["mem"]))
    if not os.path.exists(dfg):
        shutil.copy(inst["dfg"], dfg)
    if not os.path.exists(mem):
        shutil.copy(inst["mem"], mem)
    alloc = ""
    if inst.get("alloc"):
        alloc = os.path.join(wd, os.path.basename(inst["alloc"]))
        if not os.path.exists(alloc):
            shutil.copy(inst["alloc"], alloc)
        return dfg, mem, alloc
    a = os.path.join(inst["dir"], os.path.basename(inst["dfg"]).replace("PartPredDFG.xml", "mem_alloc.txt"))
    if os.path.exists(a):
        alloc = os.path.join(wd, os.path.basename(a))
        if not os.path.exists(alloc):
            shutil.copy(a, alloc)
    return dfg, mem, alloc


def map_m0(inst, wd, rep):
    dfg, mem, _alloc = prep(wd, inst)
    cmd = [MAPPER, "-m", "0", "-d", dfg, "-x", str(XD), "-y", str(YD),
           "-j", mem, "-i", str(II), "-t", "HyCUBE_4REG"]
    wall, rc, log, to = run(cmd, wd, "m0_r%d.log" % rep)
    r = parse_log(log, to)
    r.update(wall_s=round(wall, 2), rc=rc)
    rt = sorted(glob.glob(os.path.join(wd, "*routeInfo.log")), key=os.path.getmtime)
    if rt:
        place, paths = parse_routeinfo(open(rt[-1], errors="replace").read())
        json.dump({"placement": {str(k): list(v) for k, v in place.items()},
                   "paths": {"%d,%d" % k: v for k, v in paths.items()}},
                  open(os.path.join(wd, "extract.json"), "w"))
        r["n_place"], r["n_paths"] = len(place), len(paths)
    return r


def map_verify(inst, wd, rep, pins, routes_doc, tag):
    dfg, mem, _alloc = prep(wd, inst)
    pp, rp, rep_p = (os.path.join(wd, "_pins.json"), os.path.join(wd, "_routes.json"),
                     os.path.join(wd, "_report.json"))
    json.dump({"pins": pins}, open(pp, "w"))
    json.dump(routes_doc, open(rp, "w"))
    if os.path.exists(rep_p):
        os.remove(rep_p)
    cmd = [MAPPER, "-m", "3", "-d", dfg, "-x", str(XD), "-y", str(YD),
           "-j", mem, "-i", str(II), "-t", "HyCUBE_4REG",
           "--pins", pp, "--routes", rp, "--report", rep_p, "--pins_mode", "2", "--verify"]
    wall, rc, log, to = run(cmd, wd, "%s_r%d.log" % (tag, rep))
    doc = {}
    if os.path.exists(rep_p):
        try:
            doc = json.load(open(rep_p))
        except ValueError:
            doc = {}
    return wall, rc, log, to, doc


# ------------------------------------------------------------------ main

def iqr(v):
    if len(v) < 4:
        return 0.0
    q = statistics.quantiles(v, n=4)
    return q[2] - q[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="full", choices=["probe", "full"])
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    sys.path.insert(0, os.path.join(REPO, "src"))
    from cgra_agent.interfaces import Placement, Pin, Routes
    from cgra_agent.routers.base import shortest_path
    from cgra_agent.routers.rule import RulePolicy
    from cgra_agent.mapper import Mapper

    rows = []
    for kernel in KERNELS:
        inst = instance(kernel)
        if not inst or not inst["mem"]:
            print("%-10s SKIP (missing dfg/mem)" % kernel, flush=True)
            continue
        edges = dfg_edges(inst["dfg"])
        base = os.path.join(OUT, kernel)
        t_m0 = []
        m0 = None
        for rep in range(1, args.reps + 1):
            wd = os.path.join(base, "m0", "r%d" % rep)
            r = map_m0(inst, wd, rep)
            t_m0.append(r["wall_s"])
            if rep == 1:
                m0 = r
        row = {"kernel": kernel, "config": "m0", "edges": len(edges),
               "mappable": m0["mappable"], "ii": m0["ii"], "lat": m0["lat"],
               "first_call_ok": m0["mappable"], "verify_rounds": 0,
               "conflicts_first_round": 0,
               "time_med": round(statistics.median(t_m0), 2),
               "time_iqr": round(iqr(t_m0), 2), "reps": args.reps,
               "applied": "", "proposed": "", "stage": m0["stage"]}
        rows.append(row)
        print("m0       %-10s ok=%-5s II=%-2s lat=%-3s %5.2fs [%4.2f] edges=%d"
              % (kernel, m0["mappable"], m0["ii"], m0["lat"], row["time_med"], row["time_iqr"],
                 len(edges)), flush=True)
        if not m0["mappable"] or args.stage == "probe":
            continue

        # placement + A*'s own paths from rep 1
        ext = json.load(open(os.path.join(base, "m0", "r1", "extract.json")))
        place = {int(k): tuple(v) for k, v in ext["placement"].items()}
        bank = {tuple(int(x) for x in k.split(",")): v for k, v in ext["paths"].items()}
        hit = sum(1 for e in edges if e in bank)
        # routeInfo's -T is the phase (lat % II), not the latency: pin with t only and let
        # the mapper pick the latency, which is what the reference solution does too.
        pins = [{"node": n, "x": p[0], "y": p[1], "t": p[2] % II}
                for n, p in sorted(place.items())]
        lat = {n: p[2] for n, p in place.items()}   # provisional; replaced by the pins run
        pos = {n: (p[0], p[1]) for n, p in place.items()}
        print("   extracted pins=%d paths=%d (edges %d, hit %d/%d)"
              % (len(pins), len(bank), len(edges), hit, len(edges)), flush=True)
        json.dump({"pins": pins, "bank": {"%d,%d" % k: v for k, v in bank.items()},
                   "lat": {str(k): v for k, v in lat.items()},
                   "pos": {str(k): list(v) for k, v in pos.items()}},
                  open(os.path.join(base, "placement.json"), "w"))

        # fill any edge the mapper did not report a path for with a deterministic shortest path
        facts = {"lat": lat, "edges": edges, "pos": pos, "arch": {"x": XD, "y": YD},
                 "paths": {"workdir": os.path.join(base, "run"), "dfg": inst["dfg"],
                           "arch_json": inst["mem"], "mem_alloc": ""}}
        full_bank = dict(bank)
        for e in edges:
            if e not in full_bank and e[0] in pos and e[1] in pos:
                p = shortest_path(pos[e[0]], pos[e[1]], XD, YD)
                if p:
                    full_bank[e] = p
        policy = RulePolicy(Mapper(home=REPO, ii=II), path_mode="bank", bank=full_bank, seed=1)

        for cov in [0.0, 0.25, 0.5, 1.0]:
            tag = "pins" if cov == 0.0 else "k%02d" % int(cov * 100)
            times, rounds, confs, oks, applied, proposed, finals = [], [], [], [], [], [], []
            for rep in range(1, args.reps + 1):
                routes = policy.routes(Placement(pins=[Pin(node=p["node"], x=p["x"], y=p["y"],
                                                          t=p["t"]) for p in pins]), facts,
                                       {}, II, cov, "critical")
                flex = []
                doc = {}
                t0 = time.time()
                for rnd in range(3):
                    rdoc = {"routes": [{"from": e.src, "to": e.dst,
                                        "path": [list(c) for c in e.path]}
                                       for e in routes.edges]}
                    if flex:
                        rdoc["flex"] = [list(e) for e in flex]
                    wd = os.path.join(base, tag, "r%d" % rep)
                    wall, rc, log, to, doc = map_verify(inst, wd, rep, pins, rdoc,
                                                        "%s_r%d" % (tag, rep))
                    if rnd == 0:
                        first_wall = wall
                        first_ok = bool(doc.get("ok"))
                        first_conf = len(doc.get("conflicts") or [])
                    if doc.get("ok"):
                        break
                    fresh = [tuple(e) for e in (doc.get("suggest_edges") or []) if tuple(e) not in flex]
                    if not fresh:
                        break
                    flex.extend(fresh)
                times.append(round(first_wall, 2))
                rounds.append(rnd + 1)
                confs.append(first_conf)
                oks.append(first_ok)
                applied.append(doc.get("routes_applied", 0))
                proposed.append(len(routes.edges))
                finals.append(bool(doc.get("ok")))
            row = {"kernel": kernel, "config": tag, "edges": len(edges),
                   "mappable": all(finals), "ii": doc.get("ii", II),
                   "lat": doc.get("lat", ""), "first_call_ok": oks[0],
                   "verify_rounds": statistics.median(rounds),
                   "conflicts_first_round": statistics.median(confs),
                   "time_med": round(statistics.median(times), 2),
                   "time_iqr": round(iqr(times), 2), "reps": args.reps,
                   "applied": statistics.median(applied), "proposed": proposed[0],
                   "stage": "ok" if all(finals) else "fail"}
            rows.append(row)
            print("%-8s %-10s ok=%-5s first_ok=%-5s rounds=%s conf0=%s %6.2fs [%5.2f] "
                  "routed=%s/%s lat=%s"
                  % (tag, kernel, row["mappable"], row["first_call_ok"],
                     row["verify_rounds"], row["conflicts_first_round"],
                     row["time_med"], row["time_iqr"], row["applied"], row["proposed"],
                     row["lat"]), flush=True)
            if tag == "pins":
                # the pins-only run gives the latency assignment A* settles on at this II;
                # the k-configs need it for the slack ranking (honest source: the mapper's
                # own report, not routeInfo's phase values)
                got = {int(p["node"]): int(p["lat"]) for p in (doc.get("placement") or [])
                       if "lat" in p}
                if got:
                    lat.update(got)
                    facts["lat"] = lat
                    print("   lat from pins run: %d nodes, max=%d"
                          % (len(got), max(got.values())), flush=True)

    keys = ["kernel", "config", "edges", "mappable", "first_call_ok", "ii", "lat",
            "verify_rounds", "conflicts_first_round", "time_med", "time_iqr", "reps",
            "applied", "proposed", "stage"]
    csv = os.path.join(OUT, "fixii.csv")
    with open(csv, "w") as f:
        f.write(",".join(keys) + "\n")
        for r in rows:
            f.write(",".join(str(r.get(k, "")) for k in keys) + "\n")
    print("wrote", csv)


if __name__ == "__main__":
    main()
