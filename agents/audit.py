"""失败归因：把四门的失败翻译成"下一步能做什么"。

这一层不做决策，只做"说清楚哪里错了"。说清楚的标准是：
1. 指出是哪一门（G1 功能 / G2 资源 / G3 时序 / G4 质量）；
2. 能给节点的就给节点（G2 的 errors 里有 idx）；
3. 能给建议的就给建议，但不假装知道——不确定就写 unknown。

历史上我们吃过两次亏，都写在这里当护栏：
- 用过 mapper 自己的 "Map Success" 当成功判据 → 88% 假阳性；
- 把我们以为的约束当硬约束（OSTORE 被判非法、同一 PE 多节点被判非法）
  → 把 mapper 自己产出的合法解判成非法。
所以归因只引用四门的结论，不自己发明判据。
"""
from __future__ import annotations

import re

# 已知的仿真器断言 = 已知的硬件时序缺陷，不是"放置不好"
_SIM_PATTERNS = [
    (r"CGRATile\.cpp:168", "select_same_cycle",
     "SELECT 的两个操作数落在同一拍（I1isvalid && I2isvalid）。"
     "上游 SA 有时会把两个父节点的相位排成一样；II 抬高或换后端通常能避开。"),
    (r"CGRATile\.cpp:175", "select_same_cycle",
     "同上：SELECT 双操作数同拍。" ),
    (r"CGRATile\.cpp:352|CGRATile\.cpp:358|CGRATile\.cpp:364", "input_reg_overwrite",
     "输入寄存器在一个周期内被覆盖，通常是路由共用输入端口。"),
    (r"CGRATile\.cpp:285", "unknown_opcode",
     "出现了仿真器不认识的 opcode，映射产物与架构不匹配。"),
    (r"CGRA\.cpp:214|CGRA\.cpp:215", "missing_trace",
     "找不到 trace/mem_alloc，属工作目录问题，不是映射问题。"),
]


def audit(att, prob=None) -> dict:
    """返回 {gate, cls, reason, suspects, advice, stop}。"""
    out = {"gate": "", "cls": "", "reason": "", "suspects": [],
           "advice": [], "stop": False}
    first = att.first_failure()
    if first is None:
        out.update(gate="", cls="ok", reason="四门全过",
                   advice=["记录这个配置；如果还有预算，可以试着压低 cost"])
        return out

    out["gate"] = first.gate
    out["reason"] = (first.reason or "")[:300]
    text = " ".join([out["reason"], str(att.artifacts.get("backend_error", ""))])

    if first.gate == "G1":
        for pat, cls, why in _SIM_PATTERNS:
            if re.search(pat, text):
                out.update(cls=cls, advice=[
                    "换后端（pathfinder 常常不触发这个断言）",
                    "或把 II 抬高一级：避开同拍冲突",
                ])
                return out
        if "无法仿真" in out["reason"] or "没有产出二进制" in out["reason"]:
            out.update(cls="no_binary", advice=[
                "后端没产出二进制：看 backend_error；先试 pathfinder" ])
            return out
        out.update(cls="sim_mismatch", advice=[
            "仿真结果与预期不符：这是唯一的功能真值，必须换方案",
            "先确认同一份 placement 用 pathfinder 复跑是否一致（排除工具链问题）",
        ])
        return out

    if first.gate == "G2":
        errs = (first.detail or {}).get("errors") or []
        kinds = {}
        for e in errs:
            kinds[e.get("code", "?")] = kinds.get(e.get("code", "?"), 0) + 1
            if len(out["suspects"]) < 8 and e.get("node") is not None:
                out["suspects"].append(int(e["node"]))
        out["cls"] = "resource:" + ",".join("%s×%d" % (k, v)
                                            for k, v in sorted(kinds.items())) \
            if kinds else "resource"
        if "slot_conflict" in kinds:
            out["advice"].append("槽位冲突：把这些节点钉到别的 (x,y)，或让后端自己换")
        if "op_unsupported" in kinds or "mem_unreachable" in kinds:
            out["advice"].append("算子/访存不匹配：约束钉错了列，去掉对应 pin")
        if not out["advice"]:
            out["advice"].append("资源冲突：先清掉引脚让后端重排，再逐步加约束")
        return out

    if first.gate == "G3":
        out.update(cls="mapper_rejected", advice=[
            "后端没接受这份放置（它自己另找了或直接失败）",
            "多半是引脚太紧：减少 pin 的数量，或换 II",
        ])
        return out

    out.update(cls="quality", advice=["四门已过，只是质量指标不好：可继续搜索"])
    return out
