"""Locate the memory-access trace a kernel needs before it can be simulated.

Tracing is a *build* step (instrument the 32-bit binary, run it under the host
loader, record every memory access), not a search variable, and it is not
implemented here.  What is implemented is the lookup policy, so every arm
resolves traces identically:

(a) explicit --config ``memtraces:`` or ``--trace`` wins;
(b) otherwise a repository fixture ``<kernel>_trace_*.txt`` is reused
    (array_add ships one with the simulator);
(c) otherwise the run fails with an actionable message: generating a trace
    needs the 32-bit i386 runtime (``gcc-multilib`` + ``libc6-dev-i386``),
    which this machine does not have and cannot apt-install without sudo.
"""

from __future__ import annotations

import glob
import os
from typing import Any, Dict, List, Optional, Tuple

from .jobdir import ToolError

# Fixture locations that are part of the repository (vendor snapshot).
TRACE_DIRS = (
    "toolchain/vendor/simulator/test/library",
    "work/smoke",
)
TRACE_DIRS_BY_KERNEL = (
    "work/traces/{kernel}",
    "toolchain/vendor/dfggen/benchmarks/morpher_benchmarks/{kernel}/memtraces",
    "toolchain/vendor/dfggen/benchmarks/woset_benchmarks/{kernel}/memtraces",
)

GENERATION_HINT = (
    "no {kernel}_trace_*.txt found. Generate it with scripts/gen_trace.sh "
    "(needs gcc-multilib + libc6-dev-i386, now installed), or pass "
    "--trace <file|dir>.  Traces produced by the batch script land in "
    "work/traces/{kernel}/."
)


class TraceNotFound(ToolError):
    """No trace for this kernel and none can be generated on this host."""


def _is_trace(path: str, kernel: str) -> bool:
    name = os.path.basename(path)
    return name.endswith(".txt") and name.startswith(kernel + "_trace_")


def _expand(path: str, repo_root: str) -> List[str]:
    p = path if os.path.isabs(path) else os.path.join(repo_root, path)
    if os.path.isdir(p):
        return sorted(glob.glob(os.path.join(p, "*.txt")))
    if os.path.isfile(p):
        return [p]
    return sorted(glob.glob(p))


def find_traces(
    repo_root: str,
    kernel: str,
    config: Optional[Dict[str, Any]] = None,
    extra: Optional[str] = None,
) -> List[str]:
    """Explicit traces first, then repository fixtures, in priority order."""
    out: List[str] = []
    explicit: List[str] = []
    if extra:
        explicit.append(extra)
    cfg_traces = (config or {}).get("memtraces")
    if cfg_traces:
        if isinstance(cfg_traces, str):
            explicit.append(cfg_traces)
        else:
            explicit.extend(str(p) for p in cfg_traces)
    for raw in explicit:
        cands = [p for p in _expand(raw, repo_root) if os.path.isfile(p)]
        # A directory argument often also holds mapper/simulator logs; only
        # accept real trace fixtures when any are present.
        named = [p for p in cands if _is_trace(p, kernel)]
        for path in (named or cands):
            if path not in out:
                out.append(path)

    for rel in TRACE_DIRS:
        for path in _expand(os.path.join(repo_root, rel), repo_root):
            if _is_trace(path, kernel) and path not in out:
                out.append(path)
    for rel in TRACE_DIRS_BY_KERNEL:
        for path in _expand(os.path.join(repo_root, rel.format(kernel=kernel)), repo_root):
            if _is_trace(path, kernel) and path not in out:
                out.append(path)
    return out


def resolve_trace(
    repo_root: str,
    kernel: str,
    config: Optional[Dict[str, Any]] = None,
    extra: Optional[str] = None,
) -> Tuple[str, str]:
    """Return ``(path, tier)`` where tier is ``explicit`` or ``repository``."""
    traces = find_traces(repo_root, kernel, config, extra)
    if traces:
        explicit = []
        if extra:
            explicit.append(extra)
        cfg_traces = (config or {}).get("memtraces")
        if cfg_traces:
            if isinstance(cfg_traces, str):
                explicit.append(cfg_traces)
            else:
                explicit.extend(str(p) for p in cfg_traces)
        tier = "explicit" if any(os.path.abspath(traces[0]) == os.path.abspath(p)
                                 for raw in explicit for p in _expand(raw, repo_root)) else "repository"
        return traces[0], tier
    raise TraceNotFound(GENERATION_HINT.format(kernel=kernel))
