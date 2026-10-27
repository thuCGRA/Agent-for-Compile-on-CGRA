"""布线槽位：PathFinderRouter 工厂 + 策略（RulePolicy / LLMPolicy）。

pipeline 只依赖 ``interfaces.Router`` 协议；策略类只产出 Routes，不碰 mapper。
"""

from __future__ import annotations

from typing import Any

from .base import (Edge, Policy, dfg_edges, edge_slack, select_edges,
                   shortest_path)
from .llm import LLMPolicy, NoEndpoint, mock_from_bank
from .pathfinder import PathFinderRouter
from .rule import RulePolicy, load_bank, paths_of

_ROUTERS = {"pathfinder": "vendor PathFinder (pinned, -m 3)"}
DEFAULT_ROUTER = "pathfinder"


def make_router(kind: str = DEFAULT_ROUTER, *, repo_root: str, xdim: int, ydim: int,
                timeout: float = 600.0) -> Any:
    kind = (kind or DEFAULT_ROUTER).lower()
    if kind not in _ROUTERS:
        raise ValueError("unknown router %r (known: %s)" % (kind, ", ".join(sorted(_ROUTERS))))
    return PathFinderRouter(repo_root=repo_root, xdim=xdim, ydim=ydim, timeout=timeout)


__all__ = ["PathFinderRouter", "make_router", "DEFAULT_ROUTER", "Policy",
           "RulePolicy", "LLMPolicy", "NoEndpoint", "Edge", "dfg_edges",
           "edge_slack", "select_edges", "shortest_path", "load_bank",
           "paths_of", "mock_from_bank"]
