#!/usr/bin/env python3
"""Freeze per-kernel DFG/arch/trace artifacts in a stable location.

The pipeline bundles in work/armA/<kernel>/<ts>/ are timestamped and were
overwritten once by another agent's probe; this script exports the inputs
themselves (DFG, updated arch, original arch, mem_alloc, traces) to
work/kernels/<kernel>/, which is what B should read.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "src"))

from cgra_agent.pipeline import load_config  # noqa: E402
from cgra_agent.tools.dfg import build_dfg  # noqa: E402
from cgra_agent.tools.jobdir import JobDir  # noqa: E402

KERNELS = sys.argv[1:] or [
    "array_add", "array_cond", "trmm", "hpcg", "gemm", "gemm_pace",
    "conv3", "conv2d", "mac", "mults1",
]

ORIGINAL_ARCH = os.path.join(REPO, "toolchain/vendor/mapper/json_arch/hycube_original.json")
TRACE_ROOT = os.path.join(REPO, "work/traces")


def export_one(kernel: str) -> dict:
    cfg = load_config(REPO, kernel)
    out = os.path.join(REPO, "work/kernels", kernel)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    job = JobDir(os.path.join(out, "build"))
    res = build_dfg(job, repo_root=REPO, kernel=kernel, config=cfg)
    shutil.copy(res.dfg, os.path.join(out, "dfg.xml"))
    shutil.copy(res.arch, os.path.join(out, "mem.json"))
    if os.path.isfile(ORIGINAL_ARCH):
        shutil.copy(ORIGINAL_ARCH, os.path.join(out, "arch.json"))
    else:
        shutil.copy(res.arch, os.path.join(out, "arch.json"))
    if res.mem_alloc and os.path.isfile(res.mem_alloc):
        shutil.copy(res.mem_alloc, os.path.join(out, "mem_alloc.txt"))
    traces = []
    tdir = os.path.join(TRACE_ROOT, kernel)
    if os.path.isdir(tdir):
        names = sorted(
            n
            for n in os.listdir(tdir)
            if n.endswith(".txt") and "_trace_" in n
        )
        for name in names[:4]:
            shutil.copy(os.path.join(tdir, name), os.path.join(out, name))
            traces.append(name)
    shutil.rmtree(os.path.join(out, "build"), ignore_errors=True)
    manifest = {
        "kernel": kernel,
        "function": cfg.get("function"),
        "source": cfg.get("c_source"),
        "xdim": cfg.get("xdim", 4),
        "ydim": cfg.get("ydim", 4),
        "nodes": len([line for line in open(os.path.join(out, "dfg.xml"), encoding="utf-8", errors="replace") if "<Node " in line]),
        "dfg_sha256": JobDir.sha256(os.path.join(out, "dfg.xml")),
        "mem_sha256": JobDir.sha256(os.path.join(out, "mem.json")),
        "arch_sha256": JobDir.sha256(os.path.join(out, "arch.json")),
        "traces": traces,
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
        fh.write("\n")
    return manifest


def main() -> int:
    rows = []
    for kernel in KERNELS:
        try:
            rows.append(export_one(kernel))
            print("ok  ", kernel, rows[-1]["nodes"], "nodes", rows[-1]["dfg_sha256"][:12])
        except Exception as exc:  # noqa: BLE001
            print("FAIL", kernel, type(exc).__name__, str(exc)[:160])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
