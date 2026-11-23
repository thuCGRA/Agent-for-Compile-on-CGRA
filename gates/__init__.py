"""四门级联：一次尝试要依次通过 G1–G4 才算数。

    G1 functional  仿真器 Matches>0 且 Mismatches==0       <- 唯一的功能真值
    G2 resource    core.check 的资源/依赖/相位判据
    G3 timing      mapper 接受了这份放置（而不是它自己另找一份）
    G4 quality     II / lat / cost / quality，只记录不判失败

顺序不能反。历史上我们用 mapper 自己的 "Map Success" 当成功判据，
实测 88% 是假阳性——所以 G1 必须排在最前面并且真跑仿真器。
"""
from dataclasses import dataclass, field


@dataclass
class GateResult:
    gate: str = ""
    passed: bool = False
    reason: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        return {"gate": self.gate, "passed": self.passed,
                "reason": self.reason, "detail": self.detail}


@dataclass
class Attempt:
    """一次完整的尝试：放置 + 后端产物 + 四门结果。"""
    kernel: str = ""
    backend: str = ""
    ii: int = 0
    placement: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)        # {"G1": GateResult, ...}
    cost: float = 0.0
    quality: float = 0.0
    lat: int = 0
    elapsed: float = 0.0
    artifacts: dict = field(default_factory=dict)
    hints: dict = field(default_factory=dict)

    @property
    def ok(self):
        return bool(self.gates.get("G1") and self.gates["G1"].passed
                    and self.gates.get("G2") and self.gates["G2"].passed)

    def first_failure(self):
        for name in ("G1", "G2", "G3", "G4"):
            g = self.gates.get(name)
            if g and not g.passed:
                return g
        return None

    def as_dict(self):
        return {
            "kernel": self.kernel, "backend": self.backend, "ii": self.ii,
            "ok": self.ok, "cost": self.cost, "quality": self.quality,
            "lat": self.lat, "elapsed": self.elapsed,
            "n_placed": len(self.placement),
            "gates": {k: v.as_dict() for k, v in self.gates.items()},
            "artifacts": self.artifacts, "hints": self.hints,
        }


from .functional import gate_functional          # noqa: E402
from .resource import gate_resource              # noqa: E402
from .timing import gate_timing                  # noqa: E402
from .quality import gate_quality                # noqa: E402

__all__ = ["Attempt", "GateResult", "gate_functional", "gate_resource",
           "gate_timing", "gate_quality"]
