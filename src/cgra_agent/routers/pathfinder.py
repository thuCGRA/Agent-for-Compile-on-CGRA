"""Routing slot: the vendor PathFinder behind one ``route`` method.

Arm A keeps its thesis in the placer; the router is fixed toolchain behaviour.
Putting it behind a class means an arm can be assembled from a placer and a
router without editing the pipeline, which is what the merge needs.
"""

from __future__ import annotations

from typing import Optional

from ..tools.jobdir import JobDir
from ..tools.mapper import MapperOutcome, run_mapper


class PathFinderRouter:
    """The vendor mapper in pinned PathFinder mode (``-m 3``)."""

    def __init__(self, *, repo_root: str, xdim: int, ydim: int, timeout: float = 600.0):
        self.repo_root = repo_root
        self.xdim = int(xdim)
        self.ydim = int(ydim)
        self.timeout = float(timeout)

    def route(
        self,
        *,
        job: JobDir,
        dfg: str,
        arch: str,
        ii: int,
        pins: Optional[str] = None,
        pins_mode: int = 0,
    ) -> MapperOutcome:
        return run_mapper(
            job,
            repo_root=self.repo_root,
            dfg=dfg,
            arch=arch,
            xdim=self.xdim,
            ydim=self.ydim,
            ii=ii,
            pins=pins,
            pins_mode=pins_mode,
            timeout=self.timeout,
        )
