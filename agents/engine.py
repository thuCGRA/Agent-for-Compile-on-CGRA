"""agent 的执行层：一次"尝试"= 后端跑一次 + 四门判定。

这里是唯一碰 mapper / 仿真器的地方；策略层只发意图，不碰进程。
"""
from __future__ import annotations

import os
import time
from typing import Optional

import backends
from backends.base import Hints
from core import build_problem
from gates import (Attempt, GateResult, gate_functional, gate_quality,
                   gate_resource, gate_timing)
from tools import mapper as mapper_mod


def load_problem(kernel: str, ii: int, root: Optional[str] = None) -> dict:
    kdir = mapper_mod.kernel_dir(kernel, root)
    return build_problem(kernel, os.path.join(kdir, "dfg.xml"),
                         os.path.join(kdir, "arch.json"), ii)


def _norm_params(backend: str, params: dict) -> dict:
    """只把该后端认识的参数传下去，避免 typo 静默生效。"""
    params = dict(params or {})
    if backend == "sa":
        keep = ("force_anneal", "optimize", "budget", "max_ii",
                "select", "pick", "cost_mode")
    else:
        keep = ("max_ii",)
    return {k: v for k, v in params.items() if k in keep}


def run_attempt(kernel: str, ii: int, backend: str, params: Optional[dict] = None,
                pins: Optional[dict] = None, forbidden: Optional[list] = None,
                root: Optional[str] = None, do_sim: bool = True,
                prob: Optional[dict] = None, note: str = "") -> Attempt:
    prob = prob or load_problem(kernel, ii, root)
    hints = Hints(pins=dict(pins or {}), forbidden=list(forbidden or []),
                  note=note)
    be = backends.make(backend)
    kw = _norm_params(backend, params or {})
    t0 = time.time()
    try:
        res = be.run(prob, ii, hints=hints, root=root, **kw)
        err = res.error
    except Exception as exc:                      # 后端抛异常也算一次尝试，不是崩溃
        from backends.base import BackendResult
        res = BackendResult(backend=backend, ok=False, ii=ii)
        err = "%s: %s" % (type(exc).__name__, exc)
    wall = time.time() - t0

    att = Attempt(kernel=kernel, backend=backend, ii=res.ii or ii,
                  placement=res.placement, elapsed=round(wall, 2),
                  artifacts=dict(res.artifacts or {}),
                  hints={"pins": dict(pins or {}), "forbidden": list(forbidden or []),
                         "params": kw, "note": note})
    att.gates["G2"] = gate_resource(prob, res.placement, res.ii or ii) if res.placement \
        else GateResult(gate="G2", passed=False, reason="没有放置，无从检查")
    att.gates["G3"] = gate_timing(res.raw.get("maprun"), prob["n_nodes"])
    if do_sim and res.artifacts.get("binary"):
        att.gates["G1"] = gate_functional(kernel, res.artifacts.get("binary"), root=root)
    else:
        att.gates["G1"] = GateResult(gate="G1", passed=False,
                                     reason=err or "后端没有产出二进制，无法仿真")
    g4 = gate_quality(prob, res.placement, res.ii or ii) if res.placement else \
        GateResult(gate="G4", passed=False, reason="没有放置")
    att.gates["G4"] = g4
    if g4.passed:
        att.cost = g4.detail.get("cost", 0.0)
        att.quality = g4.detail.get("quality", 0.0)
    att.lat = res.lat
    if err:
        att.artifacts["backend_error"] = err
    return att


def summarize(att: Attempt) -> dict:
    """给策略层和报告用的紧凑视图——不要塞整个 placement。"""
    g = att.gates
    return {
        "backend": att.backend, "ii": att.ii, "ok": att.ok,
        "lat": att.lat, "cost": att.cost,
        "n_placed": len(att.placement),
        "elapsed": att.elapsed,
        "gates": {k: {"passed": v.passed, "reason": v.reason[:160]}
                  for k, v in g.items()},
        "pins": list((att.hints or {}).get("pins", {}).items()),
        "params": (att.hints or {}).get("params", {}),
        "error": att.artifacts.get("backend_error", ""),
    }
