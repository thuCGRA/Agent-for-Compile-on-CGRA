"""把一份放置折算成可比较的数字。

沿用 acc-thu/evo/harness.py 的适应度（这样 agent 分支与演化分支的分数可直接对照）：

    quality = 1 / (1 + cost / DETOUR_WEIGHT)      cost 来自 core.check
    score   = 0.6 * quality(II=4) + 0.4 * quality(II=3)

不合法记 0，并保留 errors 供 Auditor 使用。
"""
from .check import check

II_BASE = 4
II_TARGET = 3
DETOUR_WEIGHT = 20.0


def score_placement(prob, place, ii=None, weight=DETOUR_WEIGHT):
    """返回 {"ok", "cost", "quality", "errors", "stats"}。"""
    ii = int(ii or prob["ii"])
    verdict = check(prob, place, ii)
    out = {
        "ii": ii,
        "ok": bool(verdict["ok"]),
        "cost": float(verdict.get("cost", 0.0)),
        "errors": list(verdict.get("errors", [])),
        "stats": dict(verdict.get("stats", {})),
        "quality": 0.0,
    }
    if out["ok"]:
        out["quality"] = 1.0 / (1.0 + out["cost"] / float(weight))
    return out


def kernel_score(results):
    """results: {ii: score_placement 的返回}。缺 II=3 时只算 II=4 的部分。"""
    total = 0.0
    if results.get(II_BASE, {}).get("ok"):
        total += 0.6 * results[II_BASE]["quality"]
    if results.get(II_TARGET, {}).get("ok"):
        total += 0.4 * results[II_TARGET]["quality"]
    return round(total, 6)
