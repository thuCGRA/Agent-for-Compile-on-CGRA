"""对 acc-thu 三个二进制的薄封装。这一层不做任何决策。

    dfg.py      调 scripts/gen_dfg.sh（LLVM 段，参数固定）
    mapper.py   调 cgra_xml_mapper，读回放置
    sim.py      调 hycube_simulator，读回 Matches/Mismatches
    probe.py    把一次尝试的四门结果压缩成给 LLM 看的证据
"""
from . import mapper, probe, sim

__all__ = ["mapper", "sim", "probe"]
