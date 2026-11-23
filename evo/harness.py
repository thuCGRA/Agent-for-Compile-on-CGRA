"""Run an evolved placement program on acc-thu's real kernels and score it.

The evolved interface is a single function inside the EVOLVE-BLOCK:

    def place(prob, ii):
        '''return {node_idx: (x, y)}'''
        ...

Why (x, y) and not (x, y, t): morpher's own mapper derives the phase from data
arrival times -- see estimateRouting() in PathFinderMapper.cpp, where the
assertion is (minLatDestVal % t_max == destPE->T). Pinning a phase as if it
were an input makes almost every placement infeasible: on our three kernels a
strict (x, y, t) pin was rejected 3/3, while pinning only (x, y) let the mapper
solve 2/3. So the evolved program decides *space* and the mapper decides *time*.

The score therefore comes from the real mapper and the real simulator, not from
a static model of ours. A static router that allowed "wait one cycle in place"
called 8 of 8 placements routable while the mapper accepted 3 of 8, so it is not
a usable gate. Each evaluation costs a few seconds instead of ~50 ms; that is
the price of a verdict that means something.

ASCII only: this file is pushed over a PowerShell pipe.
"""
import io
import json
import os
import pickle
import re
import subprocess
import sys
import tempfile
import time
import uuid

from . import check as check_mod

KERNELS = ("array_add", "mac", "trmm")
II_FALLBACK = 4
MAP_TIMEOUT = 20
SIM_TIMEOUT = 20
MAP_DIR = "_evo_pins"

# Scoring shape: a placement the mapper refuses is worth nothing no matter how
# tidy it looks; one that maps but computes the wrong answer is worth a little
# (the mapper did find a schedule); one that simulates clean is the goal, and
# faster lat is better.
SCORE_SIM_OK = 0.85
SCORE_MAP_ONLY = 0.15
LAT_SCALE = 40.0


def root_dir():
    return os.environ.get("ACC_THU_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))


def kernel_paths(kernel):
    root = root_dir()
    base = os.path.join(root, "out", kernel)
    return os.path.join(base, "dfg.xml"), os.path.join(base, "arch.json")


def load_problems(kernels=KERNELS):
    from . import cgra
    probs = {}
    for k in kernels:
        dfg, arch = kernel_paths(k)
        if not (os.path.exists(dfg) and os.path.exists(arch)):
            raise FileNotFoundError(
                "missing %s or %s -- run scripts/run_all.sh first" % (dfg, arch))
        prob = cgra.build_problem(k, dfg, arch, II_FALLBACK)
        prob["ii"] = max(int(prob.get("rec_min_ii") or 1), 1)
        probs[k] = prob
    return probs


_RUNNER = r'''
import json, pickle, sys, traceback
sys.path.insert(0, {evo_root!r})
import importlib.util
spec = importlib.util.spec_from_file_location("evolved_program", {program!r})
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception:
    with open({out!r}, "wb") as f:
        pickle.dump({{"error": "import failed", "traceback": traceback.format_exc()}}, f)
    sys.exit(0)

if not hasattr(mod, "place"):
    with open({out!r}, "wb") as f:
        pickle.dump({{"error": "no place(prob, ii) function"}}, f)
    sys.exit(0)

probs = pickle.load(open({probs!r}, "rb"))
results = {{}}
for name, prob in probs.items():
    try:
        got = mod.place(prob, prob["ii"])
        results[name] = {{"ok": True, "place": got}}
    except Exception:
        results[name] = {{"ok": False, "error": traceback.format_exc()}}
with open({out!r}, "wb") as f:
    pickle.dump({{"results": results}}, f)
'''


def run_program(program_path, probs, timeout=120):
    """Execute the evolved program in a subprocess; return {kernel: result}."""
    tmpdir = tempfile.mkdtemp(prefix="evo_run_")
    probs_path = os.path.join(tmpdir, "probs.pkl")
    out_path = os.path.join(tmpdir, "out.pkl")
    script_path = os.path.join(tmpdir, "runner.py")
    with open(probs_path, "wb") as fh:
        pickle.dump(probs, fh)
    code = _RUNNER.format(evo_root=root_dir(), program=os.path.abspath(program_path),
                          probs=probs_path, out=out_path)
    with io.open(script_path, "w", encoding="utf-8") as fh:
        fh.write(code)
    try:
        subprocess.call([sys.executable, script_path], cwd=root_dir(),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=timeout)
    except Exception as exc:                                    # noqa: BLE001
        return {"__error__": "%s: %s" % (type(exc).__name__, exc)}
    if not os.path.exists(out_path):
        return {"__error__": "runner produced no output (timeout or crash)"}
    with open(out_path, "rb") as fh:
        got = pickle.load(fh)
    return got.get("results") or {"__error__": got.get("error") or "no results"}


def _write_pins(path, place):
    items = []
    for idx in sorted(place, key=lambda v: int(v)):
        val = place[idx]
        if isinstance(val, dict):
            x, y = int(val["x"]), int(val["y"])
        else:
            x, y = int(val[0]), int(val[1])
        # t = -1 means "the mapper picks the phase"
        items.append({"node": int(idx), "x": x, "y": y, "t": -1})
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump({"pins": items}, fh)


def _sh(cmd, timeout):
    try:
        subprocess.call(cmd, shell=True, cwd=root_dir(),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=timeout)
    except Exception:                                           # noqa: BLE001
        return False
    return True


_RE_SIM = re.compile(r"Matches::\s*(\d+)\s*,\s*Mismatches::\s*(\d+)")


def map_and_sim(prob, place, tag):
    """Pin the spatial placement, let the mapper schedule it, then simulate.

    Returns {map_ok, lat, stage, applied, total, map_error, sim_ok, matches,
    mismatches, cycles, sim_error}. sim_ok is None when the mapper failed --
    "not measured", which is not the same as "failed".
    """
    kernel = prob["kernel"]
    ii = int(prob["ii"])
    root = root_dir()
    # Concurrent evaluations (islands x workers) must not share a pins file or an
    # output dir: the last writer would decide everybody's verdict.
    tag = "%s_%d_%s" % (tag, os.getpid(), uuid.uuid4().hex[:8])
    pins_dir = os.path.join(root, "out", MAP_DIR)
    if not os.path.isdir(pins_dir):
        os.makedirs(pins_dir)
    pins_path = os.path.join(pins_dir, "%s_%s_ii%d.json" % (tag, kernel, ii))
    _write_pins(pins_path, place)

    subdir = "_evo_%s_%s_ii%d" % (tag, kernel, ii)
    subdir_path = os.path.join(root, "out", kernel, subdir)
    # stale verdicts are worse than no verdict
    if os.path.isdir(subdir_path):
        import shutil
        shutil.rmtree(subdir_path, ignore_errors=True)
    cmd = ("bash scripts/map_pinned.sh %s %s 1 %d %d %d %s"
           % (kernel, pins_path, ii, prob.get("xdim") or 4,
              prob.get("ydim") or 4, subdir))
    if not _sh(cmd, MAP_TIMEOUT):
        return {"map_ok": False, "lat": None, "stage": "timeout",
                "map_error": "mapper exceeded %ds" % MAP_TIMEOUT, "sim_ok": None}

    rep_path = os.path.join(root, "out", kernel, subdir, "pins_report.json")
    if not os.path.isfile(rep_path):
        return {"map_ok": False, "lat": None, "stage": "harness",
                "map_error": "no report at %s" % rep_path,
                "sim_ok": None}
    with io.open(rep_path, encoding="utf-8") as fh:
        rep = json.load(fh)

    lat = None
    for item in (rep.get("placement") or []):
        v = item.get("lat")
        if v is not None and (lat is None or v > lat):
            lat = v
    out = {"map_ok": bool(rep.get("ok")), "lat": lat,
           "stage": rep.get("stage") or "",
           "applied": rep.get("pins_applied"), "total": rep.get("pins_total"),
           "map_error": (rep.get("error") or "")[:300], "sim_ok": None}
    if not out["map_ok"]:
        _cleanup(kernel, subdir, pins_path)
        return out

    if not _sh("bash scripts/sim.sh %s 1 4096 %s" % (kernel, subdir), SIM_TIMEOUT):
        out["sim_ok"] = False
        out["sim_error"] = "simulator exceeded %ds" % SIM_TIMEOUT
        return out
    sim_path = os.path.join(root, "out", kernel, subdir, "sim_result.txt")
    text = ""
    if os.path.isfile(sim_path):
        text = io.open(sim_path, errors="replace").read()
    m = _RE_SIM.search(text)
    if not m:
        out["sim_ok"] = False
        out["sim_error"] = "no Simulation Result line (assert or crash)"
        _cleanup(kernel, subdir, pins_path)
        return out
    out["matches"] = int(m.group(1))
    out["mismatches"] = int(m.group(2))
    cyc = re.search(r"CGRA cycles:\s*(\d+)", text)
    out["cycles"] = int(cyc.group(1)) if cyc else None
    out["sim_ok"] = (out["mismatches"] == 0 and out["matches"] > 0)
    _cleanup(kernel, subdir, pins_path)
    return out

def _cleanup(kernel, subdir, pins_path):
    """Remove one evaluation's scratch files. Best effort; never raises."""
    import shutil
    try:
        shutil.rmtree(os.path.join(root_dir(), "out", kernel, subdir),
                      ignore_errors=True)
    except Exception:                                           # noqa: BLE001
        pass
    try:
        if os.path.isfile(pins_path):
            os.remove(pins_path)
    except Exception:                                           # noqa: BLE001
        pass


def score(probs, results):
    """Turn per-kernel placements into (combined_score, validity, metrics)."""
    per_kernel = {}
    details = {}
    n_map_ok = 0
    n_sim_ok = 0
    for name, prob in probs.items():
        got = results.get(name) or {}
        d = {}
        part = 0.0
        if not got.get("ok"):
            d["error"] = (got.get("error") or "no placement")[-400:]
            per_kernel[name] = 0.0
            details[name] = d
            continue

        verdict = check_mod.check(prob, got["place"], prob["ii"])
        d["legal"] = verdict["ok"]
        d["errors"] = verdict["errors"][:3]
        if not verdict["ok"]:
            per_kernel[name] = 0.0
            details[name] = d
            continue

        mv = map_and_sim(prob, got["place"], "fit")
        d.update(mv)
        if mv.get("map_ok"):
            n_map_ok += 1
            if mv.get("sim_ok"):
                n_sim_ok += 1
                part = SCORE_SIM_OK / (1.0 + float(mv.get("lat") or 0) / LAT_SCALE)
                part += SCORE_MAP_ONLY
            else:
                part = SCORE_MAP_ONLY
        d["score"] = round(part, 6)
        per_kernel[name] = round(part, 6)
        details[name] = d

    n = max(1, len(probs))
    combined = sum(per_kernel.values()) / n
    metrics = {
        "combined_score": round(combined, 6),
        "validity": round(n_sim_ok / (n * 1.0), 6),
        "kernels_mapped": n_map_ok,
        "kernels_sim_ok": n_sim_ok,
        "n_kernels": n,
    }
    for name, part in per_kernel.items():
        metrics[name] = part
        metrics[name + "_detail"] = details[name]
    return round(combined, 6), metrics["validity"], metrics


def evaluate_program(program_path, kernels=KERNELS, timeout=120):
    probs = load_problems(kernels)
    t0 = time.time()
    results = run_program(program_path, probs, timeout=timeout)
    if "__error__" in results:
        return {
            "combined_score": 0.0,
            "validity": 0.0,
            "eval_time": round(time.time() - t0, 3),
            "error_info": results["__error__"][-1500:],
        }
    combined, validity, metrics = score(probs, results)
    metrics["eval_time"] = round(time.time() - t0, 3)
    return metrics
