"""Router 槽位工厂。

pipeline 只依赖 ``interfaces.Router`` 协议（``route(job, dfg, arch, ii, pins,
pins_mode) -> MapReport``）。当前实现：

    PathFinderRouter  vendor mapper 的 pinned 模式（``-m 3``），所有臂共用。
    B 臂的 routers/llm.py 合并时注册进 ``_ROUTERS``，pipeline 不改。
"""

from __future__ import annotations

from typing import Any

from .pathfinder import PathFinderRouter

_ROUTERS = {"pathfinder": "vendor PathFinder (pinned, -m 3)"}
DEFAULT_ROUTER = "pathfinder"


def make_router(kind: str = DEFAULT_ROUTER, *, repo_root: str, xdim: int, ydim: int,
                timeout: float = 600.0) -> Any:
    kind = (kind or DEFAULT_ROUTER).lower()
    if kind not in _ROUTERS:
        raise ValueError("unknown router %r (known: %s)" % (kind, ", ".join(sorted(_ROUTERS))))
    return PathFinderRouter(repo_root=repo_root, xdim=xdim, ydim=ydim, timeout=timeout)


__all__ = ["PathFinderRouter", "make_router", "DEFAULT_ROUTER"]
