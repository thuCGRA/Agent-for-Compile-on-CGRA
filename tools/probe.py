"""把失败压缩成 LLM 看得懂的几行字。

设计原则（来自 CAC 那次实验的教训）：
  * 只给"有问题的部分"，不要把整份放置塞进 prompt；
  * 每条问题都要带"当时空闲且能干这活的候选"，否则模型只会说"我改一下"；
  * 点名节点时要给出该节点在 DFG 里的邻居和度数，让它知道动的代价。
"""
from collections import Counter


def summarize_attempt(attempt, prob=None, max_items=8):
    """attempt 是 gates.Attempt。返回一段短文本（中文，面向模型）。"""
    lines = ["内核 %s｜后端 %s｜II=%d｜放置 %d/%d 个节点"
             % (attempt.kernel, attempt.backend, attempt.ii,
                len(attempt.placement), prob["n_nodes"] if prob else -1)]
    fail = attempt.first_failure()
    if fail is None:
        lines.append("四门全过：cost=%.1f quality=%.3f" % (attempt.cost, attempt.quality))
        return "\n".join(lines)

    lines.append("第一个没过的门：%s — %s" % (fail.gate, fail.reason))
    errs = (fail.detail or {}).get("errors") or []
    if errs:
        lines.append("具体问题：")
        for e in errs[:max_items]:
            lines.append("  - " + str(e))
        if len(errs) > max_items:
            lines.append("  - …… 还有 %d 条" % (len(errs) - max_items))
    return "\n".join(lines)


def node_brief(prob, idx):
    """单个节点的画像：算子、度数、邻居、能不能放哪类 PE。"""
    nodes = {n["idx"]: n for n in prob["nodes"]}
    n = nodes.get(idx)
    if n is None:
        return "节点 %s 不存在" % idx
    return ("节点 %d：op=%s 父=%s 子=%s%s%s"
            % (idx, n["op"], n["parents"], n["children"],
               " 常量" if n.get("const") is not None else "",
               " 访存" if n.get("is_mem") else ""))


def hot_nodes(prob, placement, topk=6):
    """出度大 + 相位拥挤的节点，是"动它最划算"的候选。"""
    nodes = {n["idx"]: n for n in prob["nodes"]}
    slot_count = Counter((x, y, t) for (x, y, t) in placement.values())
    scored = []
    for idx, (x, y, t) in placement.items():
        n = nodes.get(idx)
        if n is None:
            continue
        degree = len(n["parents"]) + len(n["children"])
        crowd = slot_count[(x, y, t)]
        scored.append((degree * 2 + crowd * 3, idx))
    scored.sort(reverse=True)
    return [idx for _s, idx in scored[:topk]]


def op_hist_text(prob):
    return "、".join("%s×%d" % (k, v) for k, v in sorted(prob["op_hist"].items()))
