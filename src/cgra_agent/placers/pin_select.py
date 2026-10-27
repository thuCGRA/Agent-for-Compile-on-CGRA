"""A 臂专有：pin 选点规则（密度消融用）。

这个模块与 A 臂的实验设计强耦合，B/C/D 臂不需要它：所有规则都在这里写死，
消融脚本只按 (rule, ratio) 组合调用，不允许每轮手挑节点。

规则
----
degree   出入度之和最高的节点（把“结构上最忙”的点钉住）
critical 延迟加权关键路径上的节点（slack == 0）
bank     跨 bank 的 LOAD/STORE 及其一跳邻居（把内存素材钉住）
"""

from __future__ import annotations

from typing import Any, Dict, List

RULES = ("degree", "critical", "bank")


def _degree_order(facts: Dict[str, Any]) -> List[int]:
    nodes = facts["nodes"]
    order = []
    for node in nodes:
        deg = len(node.get("parents") or ()) + len(node.get("children") or ())
        order.append((-deg, int(node["idx"])))
    order.sort()
    return [idx for _, idx in order]


def _critical_order(facts: Dict[str, Any]) -> List[int]:
    nodes = facts["nodes"]
    by_idx = {int(n["idx"]): n for n in nodes}
    ii = int(facts.get("ii") or 0)
    indeg = {idx: 0 for idx in by_idx}
    for node in nodes:
        idx = int(node["idx"])
        for parent, _port, _ni in node.get("parents") or ():
            if int(parent) in indeg:
                indeg[idx] += 1
    ready = [idx for idx, d in indeg.items() if d == 0]
    topo: List[int] = []
    while ready:
        idx = ready.pop()
        topo.append(idx)
        for child, _port, _ni in by_idx[idx].get("children") or ():
            child = int(child)
            if child in indeg:
                indeg[child] -= 1
                if indeg[child] == 0:
                    ready.append(child)
    if len(topo) != len(by_idx):
        # 有环：退化为 asap 启发式（asap 小的先钉）
        return sorted(by_idx, key=lambda i: (int(by_idx[i].get("asap") or 0), i))

    lat = {idx: float(by_idx[idx].get("lat") or 1) for idx in by_idx}
    est: Dict[int, float] = {}
    for idx in topo:
        best = 0.0
        for parent, _port, ni in by_idx[idx].get("parents") or ():
            parent = int(parent)
            if parent not in est:
                continue
            best = max(best, est[parent] + lat[parent] + int(ni) * ii)
        est[idx] = best
    horizon = max(est[idx] + lat[idx] for idx in topo)

    late: Dict[int, float] = {}
    for idx in reversed(topo):
        children = [int(c) for c, _p, _n in (by_idx[idx].get("children") or ()) if int(c) in by_idx]
        if not children:
            late[idx] = horizon - lat[idx]
        else:
            late[idx] = min(late[c] - lat[idx] for c in children)
    slack = {idx: late.get(idx, 0.0) - est[idx] for idx in topo}
    critical = [idx for idx in topo if abs(slack[idx]) < 1e-9]
    # 关键节点按“结构重要性”排：入度+出度降序
    critical.sort(
        key=lambda i: (-(len(by_idx[i].get("parents") or ()) + len(by_idx[i].get("children") or ())), i)
    )
    return critical


def _bank_order(facts: Dict[str, Any]) -> List[int]:
    nodes = facts["nodes"]
    by_idx = {int(n["idx"]): n for n in nodes}
    memory = [
        int(n["idx"])
        for n in nodes
        if n.get("op") in ("LOAD", "LOADB", "LOADH", "LOADCL", "STORE", "STOREB", "STOREH")
    ]
    picked: List[int] = []
    seen = set()
    for idx in memory:
        neighbours = [idx]
        neighbours += [int(c) for c, _p, _n in (by_idx[idx].get("children") or ())]
        neighbours += [int(p) for p, _p2, _n in (by_idx[idx].get("parents") or ())]
        for cand in neighbours:
            if cand in by_idx and cand not in seen:
                seen.add(cand)
                picked.append(cand)
    for idx in _degree_order(facts):
        if idx not in seen:
            seen.add(idx)
            picked.append(idx)
    return picked


_ORDERS = {
    "degree": _degree_order,
    "critical": _critical_order,
    "bank": _bank_order,
}


def select(facts: Dict[str, Any], ratio: float, rule: str = "degree") -> List[int]:
    """按规则返回要钉的节点 idx 列表（数量 = round(ratio * 节点数)）。"""
    if rule not in _ORDERS:
        raise ValueError("unknown pin rule %r (expected one of %s)" % (rule, ", ".join(RULES)))
    all_nodes = [int(node["idx"]) for node in facts["nodes"]]
    if ratio >= 1.0:
        return sorted(all_nodes)
    if ratio <= 0.0:
        return []
    k = max(1, int(round(ratio * len(all_nodes))))
    # 保留规则给出的优先级顺序（第一条 = 最该钉的节点）。
    return _ORDERS[rule](facts)[:k]
