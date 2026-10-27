"""mapper verify 模式的 Python 封装：注入 placement/routes，读报告，跑修复循环。

agent 侧只需要：给一份 placement、一份 routes，拿到 MapReport；落不下时把
`report.suggest_edges` 塞进 `routes.flex` 再调一次（见 repair()）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .interfaces import MapReport, Placement, RouteEdge, Routes, SimResult

DEFAULT_HOME = os.environ.get("CGRA_HOME", "/home/steve/cac-armB")


@dataclass
class VerifyResult:
    report: MapReport
    binary: Optional[str] = None      # 二进制路径（如果打印成功）
    log: str = ""


@dataclass
class RepairStep:
    """修复循环里的一轮。"""
    round: int
    ok: bool
    flex: List[Tuple[int, int]]
    applied: int
    suggested: List[Tuple[int, int]]
    reason: str = ""


class Mapper:
    """把 `cgra_xml_mapper --verify` 包成可编程接口。

    `-m 3` 选 LLM mapper（verify 模式的宿主），`mtp` 只影响二进制文件名。
    """

    def __init__(self, home: str = DEFAULT_HOME,
                 arch: str = "HyCUBE_4REG", x: int = 4, y: int = 4,
                 ii: int = 2, mtp: int = 1, method: int = 3):
        self.home = home
        self.mapper = os.path.join(home, "toolchain/out/cgra_xml_mapper")
        self.sim = os.path.join(home, "toolchain/out/hycube_simulator")
        self.arch, self.x, self.y, self.ii = arch, x, y, ii
        self.mtp, self.method = mtp, method

    # ------------------------------------------------------------ 布线

    def verify(self, fixture_dir: str, dfg: str, mem: str, trace: str,
               alloc: str, placement: Placement, routes: Routes,
               workdir: Optional[str] = None, timeout: int = 300) -> VerifyResult:
        del trace, alloc  # 只有 simulate() 用得上，留在签名里方便调用方统一传参
        work = os.path.abspath(workdir or fixture_dir)
        ppath = os.path.join(work, "_pins.json")
        rpath = os.path.join(work, "_routes.json")
        rep = os.path.join(work, "_report.json")

        with open(ppath, "w") as f:
            json.dump(placement.to_dict(), f)
        with open(rpath, "w") as f:
            json.dump(routes_doc(routes), f)
        if os.path.exists(rep):
            os.remove(rep)

        cmd = [self.mapper, "-d", dfg, "-x", str(self.x), "-y", str(self.y),
               "-j", mem, "-i", str(self.ii), "-t", self.arch,
               "-m", str(self.method),
               "--pins", ppath, "--routes", rpath, "--report", rep, "--pins_mode", "2", "--verify"]
        p = subprocess.run(cmd, cwd=os.path.abspath(fixture_dir),
                           capture_output=True, text=True, timeout=timeout)
        log = p.stdout + p.stderr
        try:
            with open(rep) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return VerifyResult(MapReport(ok=False, stage="verify",
                                          error="mapper produced no report: " + log[-300:]),
                                None, log)

        r = MapReport.from_dict(data)
        r.flex_total = data.get("flex_total", 0)
        r.flex_rerouted = data.get("flex_rerouted", 0)
        r.suggest_edges = [tuple(e) for e in data.get("suggest_edges", [])]
        r.routes_total = data.get("routes_total", 0)
        r.routes_applied = data.get("routes_applied", 0)
        r.failure_class = "" if r.ok else "static"

        binary = None
        if r.ok:
            name = "%s_DP1_XDim=%d_YDim=%d_II=%d_MTP=%d_binary.bin" % (
                dfg, self.x, self.y, r.ii, self.mtp)
            cand = os.path.join(os.path.abspath(fixture_dir), name)
            if os.path.exists(cand):
                binary = cand
        return VerifyResult(r, binary, log)

    # ------------------------------------------------------------ 仿真

    def simulate(self, binary: str, fixture_dir: str, trace: str, alloc: str,
                 mem_size: int = 4096, timeout: int = 300) -> SimResult:
        p = subprocess.run([self.sim, "-x", str(self.x), "-y", str(self.y),
                            "-c", binary, "-d", trace, "-a", alloc,
                            "-m", str(mem_size)],
                           cwd=os.path.abspath(fixture_dir),
                           capture_output=True, text=True, timeout=timeout)
        out = p.stdout + p.stderr
        m = re.search(r"Matches::(\d+)\s*,?\s*Mismatches::(\d+)", out)
        if m:
            mm = int(m.group(2))
            return SimResult(ok=mm == 0, matches=int(m.group(1)), mismatches=mm,
                             failure_class="" if mm == 0 else "semantic")
        if "Assertion" in out or p.returncode != 0:
            line = next((ln for ln in out.splitlines() if "Assertion" in ln), "")
            return SimResult(ok=False, evidence="simulator aborted: %s" % line[:200],
                             failure_class="runtime")
        return SimResult(ok=False, evidence="simulator printed no verdict",
                         failure_class="runtime")

    # ------------------------------------------------------------ 修复循环

    def repair(self, fixture_dir: str, dfg: str, mem: str, trace: str,
               alloc: str, placement: Placement, routes: Routes,
               max_rounds: int = 3,
               verbosity: bool = False) -> Tuple[VerifyResult, List[RepairStep]]:
        """一轮冲突 → 释放 suggest_edges → 重跑，直到 ok 或轮数用尽。"""
        steps: List[RepairStep] = []
        res = self.verify(fixture_dir, dfg, mem, trace, alloc, placement, routes)
        for rnd in range(max_rounds):
            first = res.report.conflicts[0].get("msg", "") if res.report.conflicts else ""
            steps.append(RepairStep(rnd, res.report.ok, list(routes.flex),
                                    res.report.routes_applied,
                                    list(res.report.suggest_edges), first[:120]))
            if res.report.ok:
                break
            mine = set(routes.flex)
            fresh = [e for e in res.report.suggest_edges if e not in mine]
            if not fresh:
                break
            routes.flex.extend(fresh)
            if verbosity:
                print("  repair round %d: releasing %s" % (rnd + 1, fresh))
            res = self.verify(fixture_dir, dfg, mem, trace, alloc, placement, routes)
        return res, steps


def routes_doc(routes: Routes) -> dict:
    doc = {"routes": [{"from": e.src, "to": e.dst,
                       "path": [list(c) for c in e.path]} for e in routes.edges]}
    if routes.flex:
        doc["flex"] = [list(e) for e in routes.flex]
    return doc
