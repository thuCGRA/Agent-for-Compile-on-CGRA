"""G2：资源与相位合法。用 core.check（与演化分支同一份判据）。

它查：节点完整性、算子/PE 类型匹配、相位槽位唯一、SELECT 双操作数错拍、
以及由相位差推出的最小跳数是否 >= 曼哈顿距离。

它**不查**：布线是否真的存在（那是 G3 的事）、功能是否正确（G1）。
历史教训：我们曾有 verify 零漏报但高误杀的判据，所以这里把结论分成
"确定非法"（errors 非空）与"合法但风险高"（stats 里的绕路/端口压力）两档。
"""
from . import GateResult
from core import check


def gate_resource(prob, placement, ii=None):
    verdict = check(prob, placement, ii or prob["ii"])
    if not verdict["ok"]:
        return GateResult(gate="G2", passed=False,
                          reason="资源/相位不合法：%s" % "；".join(verdict["errors"][:4]),
                          detail={"errors": verdict["errors"], "stats": verdict.get("stats", {})})
    stats = verdict.get("stats", {})
    return GateResult(gate="G2", passed=True,
                      reason="合法（绕路 %s 跳，端口压力 %s）"
                             % (stats.get("detour"), stats.get("port_pressure")),
                      detail={"cost": verdict["cost"], "stats": stats})
