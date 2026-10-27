"""The fixed LLVM segment: C -> DFG XML + mem_alloc + bank-annotated arch.

These are compiler options, not search variables (docs/WORKFLOW.md section 0),
so the command lines are literal.  The only inputs are the per-kernel config
and the vendor sources; the two later stages (map, simulate) never see C.

Trace generation (instrument the program, run it, capture memory accesses) is
*not* part of this module yet; ``tools/sim.py`` locates an existing trace.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .jobdir import JobDir, ToolError, tail

PASS_REL = "toolchain/out/libdfggenPass.so"
ARCH_REL = "toolchain/vendor/mapper/json_arch/hycube_original.json"
UPDATE_REL = "toolchain/vendor/mapper/update_mem_alloc.py"
VENDOR_SRC = "toolchain/vendor/dfggen/benchmarks/morpher_benchmarks/{kernel}/{file}"
DEFAULT_TC_BIN = "/home/steve/miniconda3/envs/cgra-tc/bin"


@dataclass
class DFGOutcome:
    dfg: str
    mem_alloc: str
    arch: str
    source: str
    node_count: int
    secs: float = 0.0
    sha256: Dict[str, str] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _tc_bin(repo_root: str) -> str:
    for cand in (
        os.environ.get("CGRA_TC_BIN", ""),
        DEFAULT_TC_BIN,
        os.path.join(repo_root, "toolchain", "bin"),
    ):
        if cand and os.path.exists(os.path.join(cand, "clang")):
            return cand
    found = shutil.which("clang")
    if found:
        return os.path.dirname(found)
    raise ToolError("no clang found; set CGRA_TC_BIN to the cgra-tc environment bin/")


def _tool_env(repo_root: str) -> Dict[str, str]:
    tc = _tc_bin(repo_root)
    path = [tc, os.path.join(repo_root, "toolchain", "bin"), os.environ.get("PATH", "")]
    env = {"PATH": ":".join(p for p in path if p)}
    lib = os.path.join(os.path.dirname(tc), "lib")
    if os.path.isdir(lib):
        env["LD_LIBRARY_PATH"] = lib + (
            ":" + os.environ["LD_LIBRARY_PATH"] if os.environ.get("LD_LIBRARY_PATH") else ""
        )
    return env


def resolve_source(repo_root: str, kernel: str, config: Dict[str, Any]) -> str:
    candidates = []
    c_source = config.get("c_source")
    if c_source:
        candidates.append(c_source if os.path.isabs(c_source) else os.path.join(repo_root, c_source))
    candidates.append(
        os.path.join(
            repo_root,
            VENDOR_SRC.format(kernel=kernel, file=config.get("function") or kernel) + ".c",
        )
    )
    candidates.append(
        os.path.join(
            repo_root,
            VENDOR_SRC.format(kernel=kernel, file=kernel) + ".c",
        )
    )
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise ToolError(
        "kernel source not found, tried:\n  " + "\n  ".join(c for c in candidates if c)
    )


def build_dfg(
    job: JobDir,
    *,
    repo_root: str,
    kernel: str,
    config: Dict[str, Any],
    timeout: float = 300.0,
) -> DFGOutcome:
    started = time.monotonic()
    src = resolve_source(repo_root, kernel, config)
    func = str(config.get("function") or kernel)
    banks = int(config.get("numberofbanks") or 2)
    bsize = int(config.get("banksize") or 2048)
    dfg_type = str(config.get("dfg_type") or "PartPred")
    env = _tool_env(repo_root)
    pass_so = os.path.join(repo_root, PASS_REL)
    if not os.path.exists(pass_so):
        raise ToolError("DFG pass not built: %s" % pass_so)

    c_copy = job.copy_in(src, kernel + ".c")
    ll = kernel + ".ll"
    ll_opt = kernel + "_opt.ll"
    ll_inst = kernel + "_opt_instrument.ll"
    dfg_name = "%s_%sDFG.xml" % (func, dfg_type)
    alloc_name = func + "_mem_alloc.txt"
    arch_name = kernel + "_mem.json"

    def step(cmd: List[str], log: str, what: str) -> str:
        run = job.run(cmd, timeout=timeout, log_name=log, env=env)
        if not run.ok:
            raise ToolError(
                "%s failed (rc=%s):\n%s" % (what, run.rc, tail(run.output, 25)),
                log=run.log_path,
            )
        return run.output

    clang = [
        "clang",
        "-D", "CGRA_COMPILER",
        "-target", "i386-unknown-linux-gnu",
        "-g0",
        "-isystem", os.path.join(repo_root, "toolchain", "include32"),
        "-isystem", "/usr/include/x86_64-linux-gnu",
        "-O2",
        "-fno-tree-vectorize",
        "-fno-unroll-loops",
        "-S",
        "-emit-llvm",
        os.path.basename(c_copy),
        "-o", ll,
    ]
    step(clang, "clang.log", "clang")

    step(
        [
            "opt",
            "-gvn", "-mem2reg", "-memdep", "-memcpyopt", "-lcssa",
            "-loop-simplify", "-licm", "-loop-deletion", "-indvars",
            "-simplifycfg", "-mergereturn",
            ll, "-S", "-o", ll_opt,
        ],
        "opt.log",
        "opt",
    )
    step(
        [
            "opt",
            "-load", pass_so,
            "-fn", func,
            "-nobanks", str(banks),
            "-banksize", str(bsize),
            "-type", dfg_type,
            "-skeleton",
            ll_opt, "-S", "-o", ll_inst,
        ],
        "dfggen.log",
        "DFG pass",
    )

    dfg = job.file(dfg_name)
    alloc = job.file(alloc_name)
    arch = job.file(arch_name)
    if not os.path.isfile(dfg):
        candidates = sorted(
            glob.glob(job.file("*_%sDFG.xml" % dfg_type)), key=os.path.getmtime
        )
        if candidates:
            dfg = candidates[-1]
            dfg_name = os.path.basename(dfg)
    if not os.path.isfile(alloc):
        candidates = sorted(
            glob.glob(job.file("*_mem_alloc.txt")), key=os.path.getmtime
        )
        if candidates:
            alloc = candidates[-1]
            alloc_name = os.path.basename(alloc)
    if not os.path.isfile(dfg):
        raise ToolError("DFG pass did not write %s" % dfg_name)
    if not os.path.isfile(alloc):
        raise ToolError("DFG pass did not write %s" % alloc_name)

    run = job.run(
        [
            sys.executable,
            os.path.join(repo_root, UPDATE_REL),
            os.path.join(repo_root, ARCH_REL),
            alloc_name,
            str(bsize),
            str(banks),
            arch_name,
        ],
        timeout=timeout,
        log_name="update_mem_alloc.log",
    )
    if not run.ok or not os.path.isfile(arch):
        raise ToolError(
            "update_mem_alloc.py failed (rc=%s):\n%s" % (run.rc, tail(run.output, 20)),
            log=run.log_path,
        )

    text = open(dfg, "r", errors="replace").read()
    node_count = text.count("<Node ")
    if node_count == 0:
        raise ToolError("DFG has no nodes: %s" % dfg)

    warnings: List[str] = []
    if node_count < 2:
        warnings.append("DFG has %d node(s), the mapper needs at least 2" % node_count)

    secs = round(time.monotonic() - started, 3)
    hashes = {
        "dfg_sha256": JobDir.sha256(dfg),
        "mem_alloc_sha256": JobDir.sha256(alloc),
        "arch_sha256": JobDir.sha256(arch),
        "source_sha256": JobDir.sha256(c_copy),
    }
    return DFGOutcome(
        dfg=dfg,
        mem_alloc=alloc,
        arch=arch,
        source=c_copy,
        node_count=node_count,
        secs=secs,
        sha256=hashes,
        logs=list(job.logs),
        warnings=warnings,
    )
