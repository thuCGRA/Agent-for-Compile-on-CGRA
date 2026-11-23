"""G3：时序可行。判据是"mapper 接受了这份放置"。

为什么单独一门：G2 只证明相位差能用"某条长度 >= 曼哈顿距离的路由"实现，
但真实互连是有限的（4 个输入端口、每条链路每拍 1 个值、同一 PE 每拍最多
4 条边进来）。G2 说"理论上走得通"，G3 说"这块硬件上走得通"。

注意 G3 不能替代 G1。mapper 接受 ≠ 功能正确。
"""
from . import GateResult


def gate_timing(mapper_run, n_expected=None):
    """mapper_run 是 tools.mapper.MapRun（后端跑完的结果）。

    n_expected = DFG 里真实节点数；给定时会检查有没有漏放。
    """
    if mapper_run is None:
        return GateResult(gate="G3", passed=False, reason="没有 mapper 结果")
    if not mapper_run.ok:
        return GateResult(gate="G3", passed=False,
                          reason="mapper 拒绝：%s" % (mapper_run.result_txt or "无 result.txt"),
                          detail={"stdout_tail": (mapper_run.stdout or "")[-800:]})
    if n_expected is not None and len(mapper_run.placement) < n_expected:
        return GateResult(
            gate="G3", passed=False,
            reason="mapper 只放下 %d/%d 个节点"
                   % (len(mapper_run.placement), n_expected),
            detail={"mapped": len(mapper_run.placement)})
    return GateResult(gate="G3", passed=True,
                      reason="mapper 接受（II=%d, lat=%d）"
                             % (mapper_run.ii, mapper_run.lat),
                      detail={"ii": mapper_run.ii, "lat": mapper_run.lat,
                              "elapsed": mapper_run.elapsed,
                              "source": getattr(mapper_run, "source", "")})
