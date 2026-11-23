"""把演化出的 Python 程序跑在 acc-thu 的真实 DFG 上，算出一个可比较的分数。

被演化的接口只有一个函数（写在 EVOLVE-BLOCK 里）：

    def place(prob, ii):
        '''返回 {node_idx: (x, y, t)}'''
        ...

`prob` 是 evo.cgra.build_problem 产出的 dict（见 docs），`ii` 是本次要求的启动间隔。
分数由 evo.check.check 判定：不合法记 0，合法则按绕路跳数与端口压力打折。
"""
import os
import pickle
import subprocess
import sys
import tempfile
import time

from . import check as check_mod
from . import cgra

# 默认评测的 kernel（acc-thu 的三个）
KERNELS = ("array_add", "mac", "trmm")

# II=4 是 mapper 的基线（三个 kernel 都能到），II=3 是演化要冲的目标
II_BASE = 4
II_TARGET = 3

# 绕路 1 跳扣多少分；越大越重视布线质量
DETOUR_WEIGHT = 20.0


def root_dir():
    return os.environ.get("ACC_THU_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))


def kernel_paths(kernel):
    out = os.path.join(root_dir(), "out", kernel)
    return os.path.join(out, "dfg.xml"), os.path.join(out, "arch.json")


def load_problems(kernels=KERNELS):
    probs = {}
    for k in kernels:
        dfg, arch = kernel_paths(k)
        if not (os.path.exists(dfg) and os.path.exists(arch)):
            raise FileNotFoundError(
                "missing %s or %s — run scripts/run_all.sh first" % (dfg, arch))
        probs[k] = cgra.build_problem(k, dfg, arch, II_BASE)
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
    per_ii = {{}}
    for ii in {iis!r}:
        try:
            got = mod.place(prob, ii)
            per_ii[ii] = {{"ok": True, "place": got}}
        except Exception:
            per_ii[ii] = {{"ok": False, "error": traceback.format_exc()}}
    results[name] = per_ii
with open({out!r}, "wb") as f:
    pickle.dump({{"results": results}}, f)
'''


def run_program(program_path, probs, iis=(II_BASE, II_TARGET), timeout=120):
    """在子进程里跑用户程序，返回 {kernel: {ii: {"ok", "place"|"error"}}}。"""
    evo_root = os.path.dirname(root_dir())
    with tempfile.TemporaryDirectory() as td:
        probs_path = os.path.join(td, "probs.pkl")
        out_path = os.path.join(td, "out.pkl")
        runner_path = os.path.join(td, "runner.py")
        with open(probs_path, "wb") as f:
            pickle.dump(probs, f)
        with open(runner_path, "w", encoding="utf-8") as f:
            f.write(_RUNNER.format(evo_root=evo_root, program=os.path.abspath(program_path),
                                   out=out_path, probs=probs_path, iis=tuple(iis)))
        proc = subprocess.Popen([sys.executable, runner_path],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return {"__error__": "program timed out after %ss" % timeout}
        if not os.path.exists(out_path):
            return {"__error__": "no result file (exit %s)\n%s" % (
                proc.returncode, (stderr or b"").decode("utf-8", "ignore")[-800:])}
        with open(out_path, "rb") as f:
            payload = pickle.load(f)
    if "error" in payload:
        return {"__error__": payload["error"] + "\n" + payload.get("traceback", "")}
    return payload["results"]


def score(probs, results):
    """把子进程结果折算成 (combined_score, validity, metrics)。"""
    per_kernel = {}
    n_legal_base = 0
    for name, prob in probs.items():
        got = results.get(name) or {}
        best = 0.0
        detail = {}
        for ii in (II_BASE, II_TARGET):
            slot = got.get(ii) or {"ok": False, "error": "not run"}
            if not slot.get("ok"):
                detail["ii%d" % ii] = {"legal": False, "error": (slot.get("error") or "")[-300:]}
                continue
            verdict = check_mod.check(prob, slot["place"], ii)
            detail["ii%d" % ii] = {
                "legal": verdict["ok"],
                "cost": verdict["cost"],
                "errors": verdict["errors"][:3],
            }
            if verdict["ok"]:
                s = 1.0 / (1.0 + verdict["cost"] / DETOUR_WEIGHT)
                if ii == II_BASE:
                    best += 0.6 * s
                    n_legal_base += 1
                else:
                    best += 0.4 * s
        per_kernel[name] = round(best, 6)
        detail["score"] = round(best, 6)
        per_kernel[name + "_detail"] = detail

    n = max(1, len(probs))
    combined = sum(v for k, v in per_kernel.items() if not k.endswith("_detail")) / n
    validity = n_legal_base / (n * 1.0)
    metrics = {
        "combined_score": round(float(combined), 6),
        "validity": round(float(validity), 6),
        "kernels_legal_at_ii4": n_legal_base,
        "n_kernels": n,
    }
    for k, v in per_kernel.items():
        metrics[k] = v
    return metrics["combined_score"], metrics["validity"], metrics


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
