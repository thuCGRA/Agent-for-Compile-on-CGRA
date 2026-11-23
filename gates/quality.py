"""G4：质量。只记录，不判失败。

指标选 cost / lat / quality，**不把 II 当主指标**：mapper 的 II 由结构下界
（RecMinII、ResMinII）决定，agent 基本改不动，把它当主指标会让所有方法打平。
"""
from . import GateResult
from core import score_placement


def gate_quality(prob, placement, ii=None):
    ii = ii or prob["ii"]
    s = score_placement(prob, placement, ii)
    if not s["ok"]:
        return GateResult(gate="G4", passed=False,
                          reason="无法评分（放置不合法）",
                          detail=s)
    return GateResult(gate="G4", passed=True,
                      reason="cost=%.1f quality=%.3f（绕路 %s，端口压力 %s）"
                             % (s["cost"], s["quality"],
                                s["stats"].get("detour"), s["stats"].get("port_pressure")),
                      detail=s)
