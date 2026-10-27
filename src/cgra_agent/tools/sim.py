"""Run ``hycube_simulator`` and turn its stdout into a ``SimResult``.

The simulator aborts with a bare assertion when the schedule violates the
one-operand-per-cycle rule.  A second run with ``SIMULATOR=1`` prints the
instruction stream, which lets us name the PE and cycle that broke -- the
only piece of evidence a diagnosis layer can actually use.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..interfaces import SimResult
from .jobdir import JobDir, RunResult, ToolError
from .trace import TraceNotFound, find_traces, resolve_trace  # noqa: F401  (re-exported)

SIM_REL = "toolchain/out/hycube_simulator"
RESULT_RE = re.compile(r"Simulation Result: Matches::(\d+), Mismatches::(\d+)")
CYCLES_RE = re.compile(r"CGRA cycles:\s*(\d+)")
ABORT_RE = re.compile(r"Assertion .* failed")


@dataclass
class SimOutcome:
    result: SimResult
    run: Optional[RunResult] = None
    trace: str = ""
    log_path: str = ""
    log: str = ""



def _diagnose_abort(
    sim: str,
    binary: str,
    trace: str,
    alloc: str,
    xdim: int,
    ydim: int,
    mem_size: int,
    workdir: str,
    timeout: float,
) -> str:
    """Re-run with SIMULATOR=1 and name the instruction that broke."""
    cmd = [sim, "-x", str(xdim), "-y", str(ydim), "-c", binary, "-d", trace,
           "-a", alloc, "-m", str(mem_size)]
    env = dict(os.environ)
    env["SIMULATOR"] = "1"
    try:
        p = subprocess.Popen(
            cmd, cwd=workdir, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=env,
        )
        out, _ = p.communicate(timeout=timeout)
    except Exception as exc:  # pragma: no cover - best effort evidence
        return "could not trace the simulator (%s)" % exc
    lines = out.decode("utf-8", "replace").splitlines()
    at = next((i for i, ln in enumerate(lines) if "Assertion" in ln), None)
    if at is None:
        return ""
    pe = cyc = op = None
    for line in reversed(lines[:at]):
        if pe is None:
            m = re.search(r"kII =(\d+),PC =(\d+),y=(\d+),x=(\d+)", line)
            if m:
                cyc, _pc, y, x = m.groups()
                pe = "(x=%s, y=%s)" % (x, y)
        if op is None:
            m = re.search(r"OP=([A-Z_]+)", line)
            if m:
                op = m.group(1)
        if pe is not None and op is not None:
            break
    if pe is None:
        return ""
    return (
        "PE %s, cycle %s: instruction %s received both operands in the same cycle"
        % (pe, cyc, op or "?")
    )


def run_sim(
    job: JobDir,
    *,
    repo_root: str,
    binary: str,
    trace: str,
    mem_alloc: str,
    xdim: int,
    ydim: int,
    mem_size: int = 4096,
    timeout: float = 300.0,
    exe: Optional[str] = None,
    log_name: str = "sim.log",
) -> SimOutcome:
    exe = exe or os.path.join(repo_root, SIM_REL)
    if not os.path.exists(exe):
        raise ToolError("simulator not found: %s" % exe)
    if not binary or not os.path.exists(binary):
        return SimOutcome(
            result=SimResult(ok=False, evidence="no mapper binary to simulate"),
            trace=trace,
        )
    if not trace or not os.path.exists(trace):
        return SimOutcome(
            result=SimResult(ok=False, evidence="no instrumentation trace for this kernel"),
            trace=trace,
        )

    cmd = [
        exe,
        "-x", str(xdim),
        "-y", str(ydim),
        "-c", str(binary),
        "-d", str(trace),
        "-a", str(mem_alloc),
        "-m", str(mem_size),
    ]
    run = job.run(cmd, timeout=timeout, log_name=log_name)
    log = run.output
    m = RESULT_RE.search(log)
    if not m:
        evidence = "simulator did not report a result (rc=%s)" % run.rc
        if ABORT_RE.search(log):
            detail = _diagnose_abort(
                exe, binary, trace, mem_alloc, xdim, ydim, mem_size,
                str(job.path), timeout,
            )
            evidence = "simulator aborted: %s" % (detail or ABORT_RE.search(log).group(0))
        return SimOutcome(
            result=SimResult(ok=False, evidence=evidence),
            run=run,
            trace=trace,
            log_path=run.log_path,
            log=log,
        )

    matches, mismatches = int(m.group(1)), int(m.group(2))
    c = CYCLES_RE.search(log)
    cycles = int(c.group(1)) if c else 0
    evidence = ""
    if mismatches:
        evidence = "%d mismatching address(es) on %s" % (
            mismatches,
            os.path.basename(trace),
        )
    return SimOutcome(
        result=SimResult(
            ok=(mismatches == 0 and matches > 0),
            matches=matches,
            mismatches=mismatches,
            cycles=cycles,
            evidence=evidence,
        ),
        run=run,
        trace=trace,
        log_path=run.log_path,
        log=log,
    )
