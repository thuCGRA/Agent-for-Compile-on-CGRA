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
from . import router as router_mod
from . import cgra

# 默认评测的 kernel（acc-thu 的三个）
KERNELS = ("array_add", "mac", "trmm")

# II=4 是 mapper 的基线（三个 kernel 都能到），II=3 是演化要冲的目标
# II 不再是全局常量：每个 kernel 的 RecMinII 不同，由 prob["tight"]/["loose"] 决定。
# 历史：这里曾经是 II_BASE=4 / II_TARGET=3，但三个 kernel 的 RecMinII 实测都是 4，
# 追 II=3 是在追一个不可达的解（见 README「适应度」一节）。
II_FALLBACK = 4

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
        prob = cgra.build_problem(k, dfg, arch, II_FALLBACK)
        tight = max(int(prob.get("rec_min_ii") or 1), 1)
        prob["tight"] = tight
        prob["loose"] = tight + 1
        prob["ii"] = tight
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
    per_ii = {{}}
    for ii in ({iis!r} if {iis!r} else (prob["tight"], prob["loose"])):
        try:
            got = mod.place(prob, ii)
            per_ii[ii] = {{"ok": True, "place": got}}
        except Exception:
            per_ii[ii] = {{"ok": False, "error": traceback.format_exc()}}
    results[name] = per_ii
with open({out!r}, "wb") as f:
    pickle.dump({{"results": results}}, f)
'''


def run_program(program_path, probs, iis=None, timeout=120):
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
                                   out=out_path, probs=probs_path,
                                   iis=tuple(iis) if iis else ()))
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
    """把子进程结果折算成 (combined_score, validity, metrics)。

    合法 = 静态约束过（check.py）**且** 时间扩展图上真布得出来（router.py）。
    代价只在紧档 II 上比较，用 router 实际走出来的绕路跳数。
    """
    per_kernel = {}
    details = {}
    n_legal_tight = 0
    n_routable = 0
    for name, prob in probs.items():
        got = results.get(name) or {}
        tight = int(prob.get("tight") or II_FALLBACK)
        loose = int(prob.get("loose") or tight + 1)
        d = {}
        for ii in (tight, loose):
            slot = got.get(ii) or {"ok": False, "error": "not run"}
            if not slot.get("ok"):
                d["ii%d" % ii] = {"legal": False, "error": (slot.get("error") or "")[-300:]}
                continue
            verdict = check_mod.check(prob, slot["place"], ii)
            entry = {
                "legal": verdict["ok"],
                "cost": verdict["cost"],
                "errors": verdict["errors"][:3],
            }
            if verdict["ok"]:
                rt = router_mod.route(prob, slot["place"], ii)
                entry["routable"] = rt["ok"]
                entry["detour"] = rt["detour"]
                entry["unrouted"] = rt["unrouted"][:4]
            d["ii%d" % ii] = entry

        tight_entry = d.get("ii%d" % tight, {})
        legal_tight = bool(tight_entry.get("legal"))
        routable_tight = bool(tight_entry.get("routable"))
        legal_loose = bool(d.get("ii%d" % loose, {}).get("legal"))

        if legal_tight:
            n_legal_tight += 1
        part = 0.0
        if legal_tight and routable_tight:
            n_routable += 1
            part = 0.85 / (1.0 + float(tight_entry.get("detour") or 0) / DETOUR_WEIGHT)
        if legal_loose:
            part += 0.15
        d["tight"], d["loose"] = tight, loose
        d["score"] = round(part, 6)
        per_kernel[name] = round(part, 6)
        details[name] = d

    n = max(1, len(probs))
    combined = sum(per_kernel.values()) / n
    validity = n_routable / (n * 1.0)

    metrics = {
        "combined_score": round(combined, 6),
        "validity": round(validity, 6),
        "kernels_routable_at_tight_ii": n_routable,
        "kernels_legal_at_tight_ii": n_legal_tight,
        "n_kernels": n,
    }
    for name, part in per_kernel.items():
        metrics[name] = part
        metrics[name + "_detail"] = details[name]
    return round(combined, 6), round(validity, 6), metrics


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
