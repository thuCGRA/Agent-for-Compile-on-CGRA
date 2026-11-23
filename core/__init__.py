"""M0 地基：问题视图、合法性判据、评分。

三块都从 acc-thu/evo/ 提升而来（那是伐谋分支准备好的接口），
本分支只做 agent 调度，因此这里不放演化专用的 harness/init/evaluator。

约定：
  prob  = core.cgra.build_problem(kernel, dfg, arch, ii)
  place = {node_idx: (x, y, t)}          t 是模 II 的相位
  check(prob, place, ii) -> {ok, errors, cost, stats}
"""
from .cgra import CGRAError, build_problem, parse_arch, parse_dfg
from .check import check
from .score import score_placement

__all__ = [
    "CGRAError",
    "build_problem",
    "parse_arch",
    "parse_dfg",
    "check",
    "score_placement",
]
