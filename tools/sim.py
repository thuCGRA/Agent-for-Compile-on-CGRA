"""跑 hycube_simulator，拿回 Matches / Mismatches。

这是整个系统里**唯一**的功能判据。历史上我们吃过亏：拿 mapper 自己报的
"Map Success" 当成功率，实测 88% 是假阳性（bitstream 落了盘但功能是错的）。
所以 G1 必须真跑这个二进制。
"""
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from .mapper import acc_thu_root, kernel_dir

_MATCH_RE = re.compile(r"Matches::\s*(\d+)\s*,\s*Mismatches::\s*(\d+)")


@dataclass
class SimRun:
    ok: bool = False
    matches: int = 0
    mismatches: int = 0
    cycles: int = 0
    rc: int = 0
    elapsed: float = 0.0
    stdout: str = ""
    error: str = ""

    def as_dict(self):
        return {
            "ok": self.ok, "matches": self.matches, "mismatches": self.mismatches,
            "cycles": self.cycles, "rc": self.rc, "elapsed": self.elapsed,
            "error": self.error,
        }


def simulator_binary(root=None):
    path = os.path.join(root or acc_thu_root(), "out", "hycube_simulator")
    if not os.path.exists(path):
        raise RuntimeError("仿真器不存在: %s" % path)
    return path


def run_sim(binary, kernel, mem=4096, xdim=4, ydim=4, timeout=600.0,
            root=None, debug=False):
    """binary 是 mapper 产出的 *_binary.bin。trace / mem_alloc 从 kernel 目录取。"""
    root = root or acc_thu_root()
    kdir = kernel_dir(kernel, root)
    trace = os.path.join(kdir, "trace_0.txt")
    alloc = os.path.join(kdir, "mem_alloc.txt")
    for p in (trace, alloc):
        if not os.path.exists(p):
            return SimRun(error="缺 %s（先跑 acc-thu 的 gen_trace.sh）" % p)

    cmd = [simulator_binary(root), "-c", os.path.abspath(binary),
           "-d", trace, "-a", alloc, "-m", str(mem),
           "-x", str(xdim), "-y", str(ydim), "-t", "1"]
    env = dict(os.environ)
    if debug:
        env["SIMULATOR"] = "1"

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, env=env)
        out = proc.stdout.decode("utf-8", "replace")
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") + "\n[仿真超时 %ss]" % timeout
        rc = -9
    elapsed = time.time() - t0

    run = SimRun(rc=rc, elapsed=round(elapsed, 3), stdout=out[-4000:])
    m = _MATCH_RE.search(out)
    if not m:
        run.error = "仿真器没打印 Matches/Mismatches（rc=%s，多为断言中止）" % rc
        return run
    run.matches, run.mismatches = int(m.group(1)), int(m.group(2))
    # 周期数：仿真器在 epilogue 后打印，格式随版本变，拿不到就留 0
    cm = re.search(r"[Cc]ycles?[^0-9]{0,8}(\d+)", out)
    if cm:
        run.cycles = int(cm.group(1))
    run.ok = (run.mismatches == 0 and run.matches > 0)
    return run
