"""Drive ``cgra_xml_mapper -m 3`` (placement pinned, PathFinder routing).

Method 3 is the patched mapper: it runs the ordinary PathFinder search but
constrains each node's candidate queue with the pins file.  ``--pins_mode``
selects what a missing/unplaceable pin means:

    0 off, 1 strict (a pin is required for every node), 2 prefer, 3 repair

The mapper writes a machine-readable report; we translate it into the frozen
``MapReport`` contract and keep the stdout log as evidence.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..interfaces import MapReport
from .jobdir import JobDir, RunResult, ToolError, tail

MAPPER_REL = "toolchain/out/cgra_xml_mapper"
SUCCESS_RE = re.compile(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)")
ELAPSED_RE = re.compile(r"Elapsed time in seconds:\s*([\d.]+)\s*ms")


@dataclass
class MapperOutcome:
    report: MapReport
    run: RunResult
    binary: str = ""
    binary_sha256: str = ""
    report_json: Dict[str, Any] = field(default_factory=dict)


def find_binary(job: JobDir, dfg: Optional[str] = None) -> str:
    """Newest ``*_binary.bin`` produced by this mapper call.

    The mapper names the bitstream after the DFG file, so it lands next to the
    DFG (and not in the working directory) unless the two coincide.  Both
    places are searched.
    """
    roots = [str(job.path)]
    if dfg:
        d = os.path.dirname(os.path.abspath(dfg))
        if d not in roots:
            roots.append(d)
    best, best_mtime = "", -1.0
    for root in roots:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".bin") or "_binary.bin" not in name:
                continue
            p = os.path.join(root, name)
            mtime = os.path.getmtime(p)
            if mtime > best_mtime:
                best, best_mtime = p, mtime
    return best


def run_mapper(
    job: JobDir,
    *,
    repo_root: str,
    dfg: str,
    arch: str,
    xdim: int,
    ydim: int,
    ii: int,
    pins: Optional[str] = None,
    pins_mode: int = 0,
    method: int = 3,
    timeout: float = 600.0,
    exe: Optional[str] = None,
    log_name: str = "mapper.log",
    report_name: str = "map_report.json",
) -> MapperOutcome:
    exe = exe or os.path.join(repo_root, MAPPER_REL)
    if not os.path.exists(exe):
        raise ToolError("mapper not found: %s" % exe)
    report_path = job.file(report_name)
    cmd = [
        exe,
        "-d", str(dfg),
        "-x", str(xdim),
        "-y", str(ydim),
        "-j", str(arch),
        "-i", str(ii),
        "-t", "HyCUBE_4REG",
        "-m", str(int(method)),
    ]
    if pins and method != 0:
        cmd += ["--pins", str(pins), "--pins_mode", str(pins_mode)]
    cmd += ["--report", report_path]

    run = job.run(cmd, timeout=timeout, log_name=log_name)
    log = run.output
    m = SUCCESS_RE.search(log)
    elapsed = ELAPSED_RE.search(log)
    rep = job.read_json(report_path, default={}) or {}
    if not isinstance(rep, dict):
        rep = {}

    ok = bool(m) and run.rc == 0
    if rep.get("ok") is False:
        ok = False

    report = MapReport(
        ok=ok,
        stage=str(rep.get("stage") or ("done" if ok else "route")),
        ii=int(m.group(1)) if m else int(rep.get("ii") or 0),
        lat=int(m.group(2)) if m else int(rep.get("lat") or 0),
        pins_total=int(rep.get("pins_total") or 0),
        pins_applied=int(rep.get("pins_applied") or 0),
        pins_rejected=int(rep.get("pins_rejected") or 0),
        error=str(rep.get("error") or ""),
        placement=list(rep.get("placement") or []),
        elapsed_s=run.secs,
    )
    if elapsed:
        report.elapsed_s = round(float(elapsed.group(1)) / 1000.0, 3)

    if not ok and not report.error:
        if run.timed_out:
            report.error = "mapper timeout after %ss" % timeout
        else:
            lines = [
                ln
                for ln in log.splitlines()
                if "Failed" in ln or "failed" in ln or "[pins]" in ln or "error" in ln.lower()
            ]
            report.error = "; ".join(lines[-4:]) or tail(log, 6) or (
                "mapper failed (rc=%s)" % run.rc
            )
            report.error = report.error[-800:]

    binary = find_binary(job, dfg)
    sha = JobDir.sha256(binary) if binary else ""
    return MapperOutcome(
        report=report,
        run=run,
        binary=binary,
        binary_sha256=sha,
        report_json=rep,
    )
