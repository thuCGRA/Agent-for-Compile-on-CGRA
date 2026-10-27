"""Routing slot: the vendor PathFinder behind the canonical ``route`` signature.

Arm A keeps its thesis in the placer; the router is fixed toolchain behaviour.
Putting it behind a class means an arm can be assembled from a placer and a
router without editing the pipeline, which is what the merge needs.

    route(placement, facts, arch, ii, budget) -> MapReport

文件位置从 ``facts["paths"]`` 取，router 不再猜路径：

    paths = {"dfg": ..., "arch_json": ..., "mem_alloc": ..., "workdir": ...}

``budget`` 里带 ``pins_mode`` 与 ``timeout_s``；placement.pins 为空时走
``-m 0`` 基线，否则走 pinned PathFinder（``-m 3``）。
"""

from __future__ import annotations

import os

from ..interfaces import MapReport, Placement
from ..tools.jobdir import JobDir, ToolError
from ..tools.mapper import run_mapper


class PathFinderRouter:
    """The vendor mapper in pinned PathFinder mode (``-m 3``)."""

    name = "pathfinder"

    def __init__(self, *, repo_root: str, xdim: int, ydim: int, timeout: float = 600.0):
        self.repo_root = repo_root
        self.xdim = int(xdim)
        self.ydim = int(ydim)
        self.timeout = float(timeout)

    def route(
        self,
        placement: Placement,
        facts: dict,
        arch: dict,
        ii: int,
        budget: dict,
    ) -> MapReport:
        paths = (facts or {}).get("paths") or {}
        workdir = paths.get("workdir")
        dfg = paths.get("dfg")
        arch_json = paths.get("arch_json") or paths.get("arch")
        if not workdir or not dfg or not arch_json:
            raise ToolError(
                "router needs facts['paths'] = {dfg, arch_json, mem_alloc, workdir}; got %r"
                % sorted(paths)
            )
        job = JobDir(workdir)
        pins = [
            {"node": p.node, "x": p.x, "y": p.y, "t": p.t} for p in (placement.pins or [])
        ]
        pins_mode = int(budget.get("pins_mode") or 0)
        timeout = float(budget.get("timeout_s") or self.timeout)
        pins_path = None
        if pins:
            pins_path = job.write_json(
                "pins.json",
                {
                    "pins": pins,
                    "reasoning": (placement.note or "")[:800],
                    "ii": int(ii),
                    "source": placement.source,
                    "pins_mode": pins_mode,
                },
            )
        method = 3 if pins else 0
        out = run_mapper(
            job,
            repo_root=self.repo_root,
            dfg=dfg,
            arch=arch_json,
            xdim=int(facts.get("xdim") or self.xdim),
            ydim=int(facts.get("ydim") or self.ydim),
            ii=int(ii),
            pins=pins_path,
            pins_mode=pins_mode,
            method=method,
            timeout=timeout,
        )
        report = out.report
        # 产物路径随 MapReport 一起返回，pipeline 不再需要 MapperOutcome。
        report.binary = out.binary
        report.binary_sha256 = out.binary_sha256
        return report


__all__ = ["PathFinderRouter"]
