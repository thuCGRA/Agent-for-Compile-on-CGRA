"""布线策略的公共部分：Policy 协议、选边器、端口避让的最短路。

设计要点：
  * 策略只决定"哪些边由我说了算 + 这些边的路径"，其余边仍交给 mapper 的 A*；
  * 选边器与被选边的路径质量解耦（`critical` vs `random` 才是消融的主对照）；
  * 全部确定性：random 必须带固定种子，否则消融不可复现；
  * 端口避让只是**软**约束——真正的裁判是 mapper 的 verify，不要在这里假装精确。
"""

from __future__ import annotations

import collections
import random
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from ..interfaces import MapReport, Placement, Routes


class Policy(Protocol):
    """canonical 签名（与 interfaces.Router 一致）。"""
    name: str

    def routes(self, placement: Placement, facts: dict, arch: dict,
               ii: int, coverage: float, rule: str) -> Routes:
        ...

    def route(self, placement: Placement, facts: dict, arch: dict,
              ii: int, budget: dict) -> MapReport:
        ...


Edge = Tuple[int, int]


def dfg_edges(pairs: Sequence[Sequence[int]]) -> List[Edge]:
    seen, out = set(), []
    for a, b in pairs:
        e = (int(a), int(b))
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def edge_slack(edges: Sequence[Edge], lat: Dict[int, int]) -> Dict[Edge, int]:
    """用固定布局里的 lat 估 slack：dst 比 src 晚几拍可用。

    slack 大 = 有富余，A* 可以从容绕路；slack 小 = 关键路径，卡住它就顶 II/lat。
    """
    out: Dict[Edge, int] = {}
    for a, b in edges:
        la, lb = lat.get(a), lat.get(b)
        if la is None or lb is None:
            continue
        out[(a, b)] = lb - la
    return out


def select_edges(edges: Sequence[Edge], rule: str, k: int,
                 lat: Dict[int, int], seed: int = 1) -> List[Edge]:
    """挑出前 k 条边。`critical`: slack 降序；`random`: 固定种子打乱。"""
    if k <= 0:
        return []
    k = min(k, len(edges))
    if rule == "critical":
        sl = edge_slack(edges, lat)
        ordered = sorted(edges, key=lambda e: (-sl.get(e, 0), e[0], e[1]))
    elif rule == "random":
        ordered = list(edges)
        random.Random(seed).shuffle(ordered)
    else:
        raise ValueError("unknown edge rule: %r" % (rule,))
    return ordered[:k]


def shortest_path(a: Tuple[int, int], b: Tuple[int, int],
                  x_max: int = 4, y_max: int = 4,
                  avoid: Optional[Dict[Tuple[int, int, int, int], int]] = None,
                  bump: int = 4) -> Optional[List[List[int]]]:
    """网格上的确定性最短路（BFS，先 x 后 y 的邻居顺序固定）。

    `avoid` 里记录已被占用的"有向跳"，命中就加代价但不禁行——离线模型不知道
    真实的端口时序，硬禁会凭空造出不可达。真正的判罚在 verify。
    """
    if not (0 <= a[0] < x_max and 0 <= a[1] < y_max
            and 0 <= b[0] < x_max and 0 <= b[1] < y_max):
        return None
    avoid = avoid or {}
    dist = {a: 0}
    prev: Dict[Tuple[int, int], Tuple[int, int]] = {}
    q = collections.deque([a])
    while q:
        cur = q.popleft()
        if cur == b:
            break
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (cur[0] + dx, cur[1] + dy)
            if not (0 <= nxt[0] < x_max and 0 <= nxt[1] < y_max):
                continue
            w = 1 + (bump if (cur[0], cur[1], nxt[0], nxt[1]) in avoid else 0)
            nd = dist[cur] + w
            if nxt not in dist or nd < dist[nxt]:
                dist[nxt] = nd
                prev[nxt] = cur
                q.append(nxt)
    if b not in dist:
        return None
    path, cur = [], b
    while cur != a:
        path.append([cur[0], cur[1]])
        cur = prev[cur]
    path.append([a[0], a[1]])
    return path[::-1]
