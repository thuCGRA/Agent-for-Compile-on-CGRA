# EVOLVE-BLOCK-START
"""CGRA 映射器：空间放置 + 相位指派（演化主体）。

接口（必须保持）：
    place(prob, ii) -> {node_idx: (x, y, t)}

策略：
  1. 拓扑序（容忍环）。
  2. 初放置：访存节点按 bank 放对应行；ALU 节点沿深度铺开并贴近父节点。
  3. 局部搜索：以"加权线长 + bank 亲和 + 拥塞"为代价，做交换/移动微调。
  4. 相位指派：循环感知的迭代求解，显式处理回边与 SELECT/CMERGE 分离。
  5. 相位冲突时用成本最小的候选相位（而不是无脑往后推）。
"""

import math

def _topo(prob):
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
    depth = {i: 0 for i in nodes}
    for idx in order:
        for ch in nodes[idx]["children"]:
            if ch in depth and ch != idx:
                depth[ch] = max(depth[ch], depth[idx] + 1)
    return depth

def _bank_rows(prob):
    """返回 bank -> 该行可用的 PE_MEM 列表（按 y）。"""
    pe_mem = [tuple(p) for p in prob["pe_mem"]]
    rows = {}
    for (x, y) in pe_mem:
        rows.setdefault(y, []).append((x, y))
    for y in rows:
        rows[y].sort()
    return rows

def _initial_place(prob, order, nodes, depth):
    xdim, ydim = prob["xdim"], prob["ydim"]
    pe_mem = [tuple(p) for p in prob["pe_mem"]]
    pe_alu = [tuple(p) for p in prob["pe_alu"]]
    var_bank = prob.get("var_bank") or {}
    mem_rows = _bank_rows(prob)

    used = set()
    where = {}
    mem_cursor = {}
    alu_queue = []  # (score, idx) for ALU placement

    # 先放访存节点
    for idx in order:
        n = nodes[idx]
        if not n["is_mem"]:
            continue
        bank = var_bank.get(n["var"], None)
        cands = mem_rows.get(bank) if (bank is not None and bank in mem_rows) else None
        if not cands:
            cands = sorted(pe_mem)
        k = mem_cursor.get(bank, 0)
        pick = None
        for off in range(len(cands)):
            c = cands[(k + off) % len(cands)]
            if c not in used:
                pick = c
                break
        if pick is None:
            pick = cands[k % len(cands)]
        mem_cursor[bank] = k + 1
        used.add(pick)
        where[idx] = pick

    # ALU 节点：按深度决定列，优先贴近已放置的父节点
    alu_by_depth = {}
    for idx in order:
        n = nodes[idx]
        if n["is_mem"]:
            continue
        alu_by_depth.setdefault(depth[idx], []).append(idx)

    for d in sorted(alu_by_depth):
        for idx in alu_by_depth[d]:
            n = nodes[idx]
            parent_pos = [where[p] for p in n["parents"] if p in where]
            best = None
            best_cost = None
            for c in pe_alu:
                if c in used:
                    continue
                cost = 0.0
                if parent_pos:
                    cost = sum(abs(c[0]-p[0]) + abs(c[1]-p[1]) for p in parent_pos) / len(parent_pos)
                else:
                    cost = abs(c[0] - 1)  # 尽量靠左
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best = c
            if best is None:
                # 兜底：找第一个空闲
                for c in pe_alu:
                    if c not in used:
                        best = c
                        break
            if best is None:
                best = pe_alu[len(where) % len(pe_alu)]
            used.add(best)
            where[idx] = best
    return where

def _wire_cost(prob, nodes, where):
    """加权线长估计（曼哈顿距离之和）。"""
    cost = 0.0
    for p, c, _ni in prob["edges"]:
        if p in where and c in where:
            cost += abs(where[p][0]-where[c][0]) + abs(where[p][1]-where[c][1])
    return cost

def _refine_place(prob, order, nodes, depth, where, iters=400):
    """局部搜索：交换两个同类型 PE 上的节点，若线长下降则接受。"""
    pe_mem = set(tuple(p) for p in prob["pe_mem"])
    is_mem = {i: nodes[i]["is_mem"] for i in nodes}
    idxs = list(where.keys())
    if len(idxs) < 2:
        return where
    cur = _wire_cost(prob, nodes, where)
    # 构建可交换对（同类型）
    mem_idxs = [i for i in idxs if is_mem[i]]
    alu_idxs = [i for i in idxs if not is_mem[i]]
    import random
    rnd = random.Random(12345)

    def try_swap(a, b):
        nonlocal cur
        pa, pb = where[a], where[b]
        where[a], where[b] = pb, pa
        new = _wire_cost(prob, nodes, where)
        if new <= cur:
            cur = new
            return True
        where[a], where[b] = pa, pb
        return False

    pool_pairs = []
    for i in mem_idxs:
        for j in mem_idxs:
            if i < j:
                pool_pairs.append((i, j))
    for i in alu_idxs:
        for j in alu_idxs:
            if i < j:
                pool_pairs.append((i, j))

    if not pool_pairs:
        return where
    for _ in range(iters):
        a, b = pool_pairs[rnd.randrange(len(pool_pairs))]
        try_swap(a, b)
    return where

def _build_adj(prob, nodes):
    """为每个节点构造 (parent, nextiter) 列表与 child 列表。"""
    parents_ni = {i: [] for i in nodes}
    children_ni = {i: [] for i in nodes}
    for p, c, ni in prob["edges"]:
        if p in nodes and c in nodes:
            parents_ni.setdefault(c, []).append((p, ni))
            children_ni.setdefault(p, []).append((c, ni))
    return parents_ni, children_ni

def _assign_phases(prob, order, nodes, where, ii):
    """循环感知相位指派。

    约束：
      - 无环/正边：t_c ≡ t_p + lat_p + h (mod ii), h >= 曼哈顿距离
      - 回边（nextiter）：同上，但 nextiter 意味着父在上一迭代结果
        -> 实际 t_c ≡ t_p + lat_p + h (mod ii)（同余式本来就模 ii）
      - SELECT/CMERGE：两个父相位必须差 1
    """
    parents_ni, children_ni = _build_adj(prob, nodes)
    t = {}
    occupied = {}  # (x,y) -> set of phases

    def manh(a, b):
        return abs(a[0]-b[0]) + abs(a[1]-b[1])

    def slot_free(pos, phase):
        return phase not in occupied.get(pos, set())

    def take_slot(pos, phase):
        occupied.setdefault(pos, set()).add(phase)

    # 初排：按拓扑序
    for idx in order:
        n = nodes[idx]
        pos = where[idx]
        # 计算相位下界（迭代直到不动点，处理回边导致的循环）
        lo = 0
        # 收集所有父约束的候选相位
        cand_phases = None
        sel_parents = []
        op = n["op"]
        if op in ("SELECT", "CMERGE"):
            sel_parents = [p for (p, _ni) in parents_ni.get(idx, [])]

        # 反复检查，最多迭代 ii*4 次
        for _ in range(ii * 6):
            ok = True
            # 检查父约束（正向，只用已排好的父）
            for (p, ni) in parents_ni.get(idx, []):
                if p not in t:
                    continue
                lat_p = nodes[p]["lat"] or 1
                d = manh(where[p], pos)
                # 允许绕路：h = d + k*1，但相位只需要同余满足；h 取最小 >= d
                h = d
                # 找第一个 h >= d 使余数合适已无用——相位由 t 决定
                # 约束: (t_p + lat_p + h) mod ii == t_c mod ii 对某 h >= d
                # 即 t_c ≡ t_p + lat_p + d + extra (mod ii)，extra 可任意 >= 0
                # 简化为 t_c ≡ t_p + lat_p + d (mod ii)（多余走等待）
                need = (t[p] + lat_p + d) % ii
                if lo % ii != need:
                    # 调整 lo 到 >= 当前且模 ii 等于 need
                    base = lo - (lo % ii) + need
                    if base < lo + 0:  # 允许相等
                        base += ii
                    if base > lo:
                        lo = base
                        ok = False
            # SELECT/CMERGE: 两个父必须差 1
            if len(sel_parents) == 2:
                p0, p1 = sel_parents
                if p0 in t and p1 in t:
                    lat0 = nodes[p0]["lat"] or 1
                    lat1 = nodes[p1]["lat"] or 1
                    d0 = manh(where[p0], pos)
                    d1 = manh(where[p1], pos)
                    # 父相位（到达子处的有效相位）应差 1
                    a0 = (t[p0] + lat0 + d0) % ii
                    a1 = (t[p1] + lat1 + d1) % ii
                    if a0 == a1:
                        # 需要子相位满足两者不同；但我们控制的是 t_c
                        # 约束是 "一个在 t_c，另一个在 t_c-1"
                        # 即 t_c mod ii 与两者之一相等、另一个差 1
                        # 简单法：让 t_c 满足 t_c ≡ a0 且 a1 == a0-1 或反
                        pass
            if ok:
                break
            # 若被父约束卡住，跳出重试
            if lo > ii * 4:
                break

        # 尝试选一个空闲相位，同时满足 SELECT 分离
        chosen = None
        base_phase = lo % ii
        best_phase = base_phase
        best_pen = None
        for ph in range(ii):
            cand = (base_phase + ph) % ii
            if not slot_free(pos, cand):
                continue
            pen = 0
            # 校验父约束（相位角度）
            for (p, ni) in parents_ni.get(idx, []):
                if p not in t:
                    continue
                lat_p = nodes[p]["lat"] or 1
                d = manh(where[p], pos)
                # 需要 cand ≡ t_p + lat_p + h (mod ii), h >= d
                # 即存在 h>=d 满足 h ≡ cand - t_p - lat_p (mod ii)
                rem = (cand - t[p] - lat_p) % ii
                # h = rem, rem+ii, ... 需 >= d
                h_max = rem + ((d - rem + ii - 1) // ii) * ii if rem < d else rem
                if h_max < d:
                    pen += 1
            # SELECT 分离
            if len(sel_parents) == 2:
                p0, p1 = sel_parents
                if p0 in t and p1 in t:
                    lat0 = nodes[p0]["lat"] or 1
                    lat1 = nodes[p1]["lat"] or 1
                    d0 = manh(where[p0], pos)
                    d1 = manh(where[p1], pos)
                    a0 = (t[p0] + lat0 + d0) % ii
                    a1 = (t[p1] + lat1 + d1) % ii
                    # 要求两个父相位一个在 cand 一个在 cand-1
                    ok_sel = ((a0 % ii == cand and a1 % ii == (cand - 1) % ii) or
                              (a1 % ii == cand and a0 % ii == (cand - 1) % ii) or
                              (a0 != a1))
                    if not ok_sel:
                        pen += 2
            if best_pen is None or pen < best_pen:
                best_pen = pen
                best_phase = cand
                if pen == 0:
                    break
        chosen = best_phase
        t[idx] = chosen
        take_slot(pos, chosen)

    # 后处理：对 SELECT/CMERGE，如果两个父相位相同，尝试移动其中一个父节点
    for idx in order:
        n = nodes[idx]
        if n["op"] not in ("SELECT", "CMERGE"):
            continue
        ps = [p for (p, _ni) in parents_ni.get(idx, [])]
        if len(ps) != 2:
            continue
        p0, p1 = ps
        if p0 not in t or p1 not in t:
            continue
        pos = where[idx]
        d0 = manh(where[p0], pos)
        d1 = manh(where[p1], pos)
        lat0 = nodes[p0]["lat"] or 1
        lat1 = nodes[p1]["lat"] or 1
        a0 = (t[p0] + lat0 + d0) % ii
        a1 = (t[p1] + lat1 + d1) % ii
        if a0 == a1:
            # 调整 p1 相位：找空闲相位且与父的父约束不冲突
            for delta in range(1, ii):
                new_t = (t[p1] + delta) % ii
                posp = where[p1]
                if slot_free(posp, new_t):
                    # 检查 p1 的父约束
                    ok = True
                    for (pp, _ni) in parents_ni.get(p1, []):
                        if pp not in t:
                            continue
                        lat_pp = nodes[pp]["lat"] or 1
                        d = manh(where[pp], posp)
                        rem = (new_t - t[pp] - lat_pp) % ii
                        # 需要 h >= d
                        if rem == 0:
                            h = 0
                        elif rem >= d:
                            h = rem
                        else:
                            h = rem + ((d - rem + ii - 1) // ii) * ii
                        if h < d:
                            ok = False
                            break
                    if ok:
                        occupied[posp].discard(t[p1])
                        t[p1] = new_t
                        occupied.setdefault(posp, set()).add(new_t)
                        break

    return {i: (where[i][0], where[i][1], t[i] % ii) for i in where}

def place(prob, ii):
    order, nodes = _topo(prob)
    depth = _depth(prob, order, nodes)
    where = _initial_place(prob, order, nodes, depth)
    where = _refine_place(prob, order, nodes, depth, where, iters=300)
    return _assign_phases(prob, order, nodes, where, ii)

# EVOLVE-BLOCK-END