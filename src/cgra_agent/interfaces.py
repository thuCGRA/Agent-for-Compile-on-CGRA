"""两条实验臂共用的数据契约与协议。

设计原则：agent 与工具之间只通过文件（JSON）交换数据，不靠对话记忆。
本模块只放类型，不放实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, List, Optional, Protocol, Sequence


# ---------------------------------------------------------------- 布局

@dataclass
class Pin:
    node: int          # DFG 节点 idx
    x: int
    y: int
    t: int             # modulo time（相位）


@dataclass
class Placement:
    """布局结果。pins 可以为空（全交给布线器），也可以只给关键节点。"""
    pins: List[Pin] = field(default_factory=list)
    ii: int = 0
    note: str = ""
    source: str = ""   # "llm" | "sa" | "manual"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Placement":
        return cls(pins=[Pin(**p) for p in d.get("pins", [])],
                   ii=d.get("ii", 0), note=d.get("note", ""),
                   source=d.get("source", ""))


# ---------------------------------------------------------------- 布线

@dataclass
class RouteEdge:
    src: int                       # 源节点 idx
    dst: int                       # 目的节点 idx
    path: List[List[int]] = field(default_factory=list)  # [[x, y], ...]，含起点与终点


@dataclass
class Routes:
    edges: List[RouteEdge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- 证据

@dataclass
class MapReport:
    ok: bool
    stage: str = ""        # done | placement | route | verify
    failure_class: str = ""  # static | runtime | semantic | toolchain ("" = ok/unknown)
    ii: int = 0
    lat: int = 0
    pins_total: int = 0
    pins_applied: int = 0
    pins_rejected: int = 0
    error: str = ""
    conflicts: List[dict] = field(default_factory=list)  # {pe, cycle, port, edges}
    placement: List[dict] = field(default_factory=list)
    elapsed_s: float = 0.0
    # 产物位置：router 负责写 bitstream，pipeline 只按这两个字段取文件/校验。
    binary: str = ""
    binary_sha256: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "MapReport":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SimResult:
    ok: bool
    matches: int = 0
    mismatches: int = 0
    cycles: int = 0
    evidence: str = ""     # 失败时的定位串，例如 "PE (1,2), cycle 7: ADD got both operands"

    @classmethod
    def from_dict(cls, d: dict) -> "SimResult":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class CDD:
    """Causal Defect Diagnosis：一次失败的归因结论。"""
    layer: str             # node | edge | failure
    root_cause: str
    action: str            # repin | reroute | ii+1 | abort
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.0
    failure_class: str = ""  # toolchain 失败不计入任何一臂的错误率


# ---------------------------------------------------------------- 协议

class Placer(Protocol):
    name: str

    def place(self, facts: dict, arch: dict, ii: int, budget: dict) -> Placement:
        ...


class Router(Protocol):
    name: str

    def route(self, placement: Placement, facts: dict, arch: dict,
              ii: int, budget: dict) -> MapReport:
        ...


# ---------------------------------------------------------------- 装配

ARMS = {
    "A": {"placer": "llm", "router": "pathfinder"},
    "B": {"placer": "sa", "router": "llm"},
    "C": {"placer": "sa", "router": "pathfinder"},
    "D": {"placer": "llm", "router": "llm"},
}


def arm_spec(arm: str) -> dict:
    key = arm.upper()
    if key not in ARMS:
        raise ValueError(f"unknown arm {arm!r}, expected one of {sorted(ARMS)}")
    return ARMS[key]
