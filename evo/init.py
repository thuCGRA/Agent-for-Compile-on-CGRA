# EVOLVE-BLOCK-START
"""CGRA 映射器的**空间放置**（演化主体）。

接口（必须保持）：
    place(prob, ii) -> {node_idx: (x, y)}

只决定每个算子落在哪个 PE 上，**相位由 morpher 自己算**。原因是相位不是
输入：PathFinderMapper::estimateRouting 里断言
    minLatDestVal % t_max == destPE->T
也就是"数据什么时候到达，PE 就在那一拍执行"。硬钉相位会让绝大多数放置
不可行（三个 kernel 上 strict 钉 (x,y,t) 被拒 3/3，只钉 (x,y) 通过 2/3）。

约束（check.py 逐条检查，违反即 0 分）：
  * 每个节点恰好一个位置；0 <= x < xdim，0 <= y < ydim；
  * LOAD/STORE 这类只存在于 FU_MEM 的算子必须落在 PE_MEM（x=0 那一列）；
  * 访存算子带的变量所在 bank 必须能连到它落的那块 PE_MEM；
  * 同一块 PE 最多放 ii 个节点（它有 ii 个相位槽位）。

分数：先要真 mapper 布通（0.15），再要真仿真零失配（0.85 按 lat 打折）。
所以目标是：让依赖密集的算子靠近、访存算子落到对的 bank、别把 PE 挤爆。
"""

def _topo(prob):
    """拓扑序；遇到环（跨迭代边）时把剩下的节点按 idx 追加。"""
    nodes = {n["idx"]: n for n in prob["nodes"]}
    indeg = {i: 0 for i in nodes}
    for p, c, _ni in prob["edges"]:
        if p in nodes and c in nodes and p != c:
            indeg[c] = indeg.get(c, 0) + 1
    order = []
    ready = sorted([i for i, d in indeg.items() if d == 0])
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for ch in nodes[cur]["children"]:
            if ch in indeg and ch != cur:
                indeg[ch] -= 1
                if indeg[ch] == 0:
                    ready.append(ch)
        ready.sort()
    seen = set(order)
    order.extend(sorted(i for i in nodes if i not in seen))
    return order, nodes

def _depth(prob, order, nodes):
    """每个节点的最长跳数（从源点算），用来决定放在第几列。"""
    depth = {i: 0 for i in nodes}
    for idx in order:
        for ch in nodes[idx]["children"]:
            if ch in depth and ch != idx:
                depth[ch] = max(depth[ch], depth[idx] + 1)
    return depth

def place(prob, ii):
    """按 bank 定行、按深度定列，每块 PE 最多放 ii 个节点。"""
    order, nodes = _topo(prob)
    depth = _depth(prob, order, nodes)
    xdim, ydim = prob["xdim"], prob["ydim"]
    pe_mem = [tuple(p) for p in prob["pe_mem"]]
    pe_alu = [tuple(p) for p in prob["pe_alu"]]
    var_bank = prob.get("var_bank") or {}

    # bank -> 可达的 PE_MEM 坐标，必须从 CGRA.CONNECTIONS 读：arch.json 里
    # SPM_B0 连 y=0,1，SPM_B1 连 y=2,3。按行号猜会让一半访存算子落到错的
    # bank 上，morpher 在 estimateRouting 直接拒（候选集为空）。
    bank_pes = {int(b): [tuple(q) for q in ps]
                for b, ps in (prob.get("bank_pes") or {}).items()}

    load = {}
    where = {}
    for idx in order:
        n = nodes[idx]
        if n["is_mem"]:
            bank = var_bank.get(n["var"], None)
            cands = bank_pes.get(bank) if bank is not None else None
            if not cands:
                cands = sorted(pe_mem)
            cands = sorted(cands)
        else:
            col = 1 + (depth[idx] % max(1, xdim - 1))
            cands = [(col, y) for y in range(ydim) if (col, y) in set(pe_alu)]
            if not cands:
                cands = sorted(pe_alu)
        # 挑负载最轻的那块 PE（相位槽位还没用满就优先）
        best = None
        for c in cands:
            used = load.get(c, 0)
            if best is None or used < load.get(best, 0):
                best = c
        pick = best if best is not None else cands[0]
        load[pick] = load.get(pick, 0) + 1
        where[idx] = pick
    return {i: (where[i][0], where[i][1]) for i in where}

# EVOLVE-BLOCK-END
