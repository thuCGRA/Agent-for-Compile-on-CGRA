"""Placer 槽位工厂。

pipeline 只依赖 ``interfaces.Placer`` 协议（``place(facts, arch, ii, budget,
feedback=..., previous=...) -> Placement``）。每个臂放自己的实现：

    A 臂   placers/llm.py       （LLM 产布局；本仓库的臂实现）
    A 臂   placers/greedy.py    （参考布局，只用于评测/消融，不是臂）
    A 臂   placers/rule.py      （廉价规则基线：无搜索，毫秒级）
    A 臂   placers/fixed.py     （回放 pins.json）
    B 臂   placers/sa.py        （合并时放在这里，注册进 ``_PLACERS``）

合并时在 ``_PLACERS`` 里加一行即可，pipeline 不改。
"""

from __future__ import annotations

from typing import Any, Optional

from .fixed import FixedPlacer
from .greedy import GreedyPlacer
from .llm import LLMPlacer

# kind -> 说明；真正构造在 make_placer 里，避免 import 期构建。
_PLACERS = {
    "llm": "llm",        # A 臂
    "greedy": "greedy",  # 参考布局
    "rule": "rule",      # 廉价规则基线（无搜索）
    "fixed": "fixed",    # 回放 pins.json
    "sa": "morpher -m 1 (模拟退火)",   # B 臂：借用 Morpher 自带放置器
    "lisa": "morpher -m 2 (LISA)",     # B 臂：同上，当前 vendor 版本会 abort
}


def make_placer(
    kind: str,
    *,
    client: Optional[Any] = None,
    pins_file: Optional[str] = None,
    seed: int = 0,
    max_tokens: int = 2048,
    temperature: float = 0.0,
) -> Any:
    kind = (kind or "llm").lower()
    if kind not in _PLACERS:
        raise ValueError("unknown placer %r (known: %s)" % (kind, ", ".join(sorted(_PLACERS))))
    if kind == "greedy":
        return GreedyPlacer(seed=seed)
    if kind == "rule":
        from .rule import RulePlacer

        return RulePlacer()
    if kind in ("sa", "lisa"):
        from .morpher import MorpherPlacer

        return MorpherPlacer(1 if kind == "sa" else 2, name=kind)
    if kind == "fixed":
        if not pins_file:
            raise ValueError("placer=fixed needs a pins file")
        return FixedPlacer.from_file(pins_file)
    if client is None:
        raise ValueError("placer=llm needs an LLM client")
    return LLMPlacer(client, max_tokens=max_tokens, temperature=temperature, seed=seed)
