"""B 臂放置槽位：直接借用 Morpher 自带的搜素式放置器。

Morpher 的 mapper 里本来就有一批非 A* 的放置器，用 ``-m`` 选择：

    1 = SAMapper   （模拟退火）
    2 = LISAMapper （LISA，需要额外的 arch name 参数）
    0/3 = PathFinder / pinned PathFinder

它们的共同优点是不依赖 LLM 也不依赖 pins：给 DFG 与架构就能给出一个完整的
node -> (x, y, t) 布局。B 臂要的正是这个——一个"不是 LLM、也不是 A*"的布局来源，
再交给 LLM 去布路径。

取布局的方式是 ``--dump-placement``（本仓库的 0004-mapper-dump 补丁），它让
mapper 在写 bitstream 之后把最终放置落成 JSON。补丁只改 CLI，不改任何算法。

限制：``-m 2`` 在当前 vendor 版本里要求 ``arg.lisa_arg.arch_name``，命令行没有
暴露这个参数，调用会 abort。所以 ``lisa`` 注册了但默认不可用。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any, Dict, Optional

from ..interfaces import Pin, Placement
from ..tools.jobdir import ToolError

MAPPER_REL = os.path.join("toolchain", "out", "cgra_xml_mapper")


class MorpherPlacer:
    """Run one of Morpher's built-in placers and read back its placement."""

    def __init__(
        self,
        method: int = 1,
        *,
        name: str = "sa",
        repo_root: Optional[str] = None,
        timeout: float = 600.0,
        xdim: int = 4,
        ydim: int = 4,
    ):
        self.method = int(method)
        self.name = name
        self.repo_root = os.path.abspath(repo_root or os.getcwd())
        self.timeout = float(timeout)
        self.xdim = int(xdim)
        self.ydim = int(ydim)

    def place(
        self,
        facts: Dict[str, Any],
        arch: Dict[str, Any],
        ii: int,
        budget: Optional[Dict[str, Any]] = None,
        *,
        feedback: Optional[str] = None,
        previous: Optional[Any] = None,
    ) -> Placement:
        paths = (facts or {}).get("paths") or (budget or {}).get("paths") or {}
        dfg = paths.get("dfg")
        arch_json = paths.get("arch_json") or paths.get("arch")
        if not dfg or not arch_json:
            raise ToolError("morpher placer needs facts['paths'] = {dfg, arch_json}")

        exe = os.path.join(self.repo_root, MAPPER_REL)
        if not os.path.exists(exe):
            raise ToolError("mapper not found: %s" % exe)

        xdim = int((arch or {}).get("xdim") or self.xdim)
        ydim = int((arch or {}).get("ydim") or self.ydim)
        tmpdir = tempfile.mkdtemp(prefix="morpher-place-")
        dump = os.path.join(tmpdir, "placement.json")
        cmd = [
            exe, "-d", dfg, "-x", str(xdim), "-y", str(ydim), "-j", arch_json,
            "-i", str(int(ii)), "-t", "HyCUBE_4REG", "-m", str(self.method),
            "--dump-placement", dump,
        ]
        # 在 dfg 所在目录跑：mapper 会把 routeInfo/mapping 之类的副产物写在 cwd
        proc = subprocess.run(
            cmd, cwd=os.path.dirname(os.path.abspath(dfg)),
            capture_output=True, text=True, timeout=self.timeout,
        )
        if not os.path.exists(dump):
            raise ToolError(
                "morpher -m %d produced no placement (rc=%s)\n%s"
                % (self.method, proc.returncode, (proc.stderr or proc.stdout)[-400:])
            )
        data = json.load(open(dump))
        pins = [
            Pin(node=int(e["node"]), x=int(e["x"]), y=int(e["y"]), t=int(e["t"]))
            for e in data.get("placement") or []
        ]
        if not pins:
            raise ToolError("morpher -m %d returned an empty placement" % self.method)
        return Placement(
            pins=pins,
            ii=int(data.get("ii") or ii),
            note="morpher -m %d (%s)" % (self.method, data.get("mapping_method", "?")),
            source=self.name,
        )
