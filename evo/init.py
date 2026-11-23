# EVOLVE-BLOCK-START
"""CGRA 映射器的空间放置 + 相位指派（演化主体）。

接口（必须保持）：
    place(prob, ii) -> {node_idx: (x, y, t)}

约束（check.py 会逐条检查，违反即 0 分）：
  * 每个节点恰好一个位置；同一 (x, y, t) 不能放两个节点；
  * 访存算子（LOAD/STORE 等）只能落在 PE_MEM 上，算术算子不能落在 PE_MEM 上；
  * 0 <= x < xdim, 0 <= y < ydim, 0 <= t < ii；
  * SELECT/CMERGE 的两个父节点不能在同一拍到达。

分数：合法之后按"绕路跳数"打折。绕路 = 实际跳数 - 曼哈顿距离。
所以目标很直接：让有依赖的节点在阵列上彼此靠近，并且尽量少撞相位槽位。
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


def _assign_units(prob, order, nodes, depth):
    """给每个节点挑一个 (x, y)。访存算子固定在 x=0 那一列。"""
    xdim, ydim = prob["xdim"], prob["ydim"]
    pe_mem = [tuple(p) for p in prob["pe_mem"]]
    pe_alu = [tuple(p) for p in prob["pe_alu"]]
    var_bank = prob.get("var_bank") or {}

    # 每个 bank 占一行（acc-thu 的约定：SPM_B<i> 由第 i 行的 PE_MEM 独占）
    mem_rows = {}
    for (x, y) in sorted(pe_mem):
        mem_rows.setdefault(y, []).append((x, y))

    used = set()
    where = {}
    mem_cursor = {}
    alu_cursor = 0
    for idx in order:
        n = nodes[idx]
        if n["is_mem"]:
            bank = var_bank.get(n["var"], None)
            row = bank if (bank is not None and bank in mem_rows) else None
            cands = mem_rows.get(row) if row is not None else None
            if not cands:
                cands = sorted(pe_mem)
            k = mem_cursor.get(row, 0)
            pick = None
            for off in range(len(cands)):
                c = cands[(k + off) % len(cands)]
                if c not in used:
                    pick = c
                    break
            if pick is None:
                pick = cands[k % len(cands)]
            mem_cursor[row] = k + 1
        else:
            # 沿"深度"决定列，让依赖链沿行方向铺开
            col = 1 + (depth[idx] % max(1, xdim - 1))
            row = alu_cursor % max(1, ydim)
            pick = None
            for off in range(len(pe_alu)):
                c = (col, (row + off) % ydim)
                if c in pe_alu and c not in used:
                    pick = c
                    break
            if pick is None:
                pick = pe_alu[alu_cursor % len(pe_alu)]
            alu_cursor += 1
        used.add(pick)
        where[idx] = pick
    return where


def _assign_phases(prob, order, nodes, where, ii):
    """按拓扑序推相位：子节点尽量紧跟父节点，撞槽位就往后推一拍。"""
    t = {}
    occupied = set()
    for idx in order:
        n = nodes[idx]
        base = 0
        for p in n["parents"]:
            if p not in t:
                continue
            lat_p = nodes[p]["lat"] or 1
            d = abs(where[p][0] - where[idx][0]) + abs(where[p][1] - where[idx][1])
            base = max(base, t[p] + lat_p + d)
        slot = base
        limit = base + ii * 4
        while (where[idx][0], where[idx][1], slot % ii) in occupied and slot < limit:
            slot += 1
        occupied.add((where[idx][0], where[idx][1], slot % ii))
        t[idx] = slot
    return {i: (where[i][0], where[i][1], t[i] % ii) for i in where}


def place(prob, ii):
    order, nodes = _topo(prob)
    depth = _depth(prob, order, nodes)
    where = _assign_units(prob, order, nodes, depth)
    return _assign_phases(prob, order, nodes, where, ii)


# EVOLVE-BLOCK-END
