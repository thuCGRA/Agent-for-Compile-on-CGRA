"""G1：功能等价。跑 hycube_simulator，看 Matches / Mismatches。

这是唯一不可替代的判据。mapper 说 "Map Success"、我们的 check 说 "ok"，
都不能证明这份配置在硬件上算得对。
"""
from . import GateResult
from tools import sim as sim_mod


def gate_functional(kernel, binary, root=None, timeout=600.0, debug=False):
    if not binary:
        return GateResult(gate="G1", passed=False,
                          reason="没有 bitstream，无法做功能验证")
    run = sim_mod.run_sim(binary, kernel, timeout=timeout, root=root, debug=debug)
    if run.error:
        return GateResult(gate="G1", passed=False, reason=run.error,
                          detail=run.as_dict())
    if run.mismatches > 0:
        return GateResult(
            gate="G1", passed=False,
            reason="仿真失配 %d 处（Matches=%d）：这份配置算出来的结果和 C 参考不一致"
                   % (run.mismatches, run.matches),
            detail=run.as_dict())
    if run.matches == 0:
        return GateResult(gate="G1", passed=False,
                          reason="仿真器一条都没比对到（Matches=0），trace 或 mem_alloc 有问题",
                          detail=run.as_dict())
    return GateResult(gate="G1", passed=True,
                      reason="仿真通过：Matches=%d, Mismatches=0" % run.matches,
                      detail=run.as_dict())
