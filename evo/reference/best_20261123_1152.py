# EVOLVE-BLOCK-START
"""CGRA 空间放置：短边 + 归约环收紧 + 关键路径感知 + 多起点。

相位交给 mapper，这里只决定 (x, y)。

相对上一版的关键改动
--------------------
* **修正 crit_path 的时延模型**：一条依赖边 p->c 的传输时延不是 `hop`，而是
  `max(1, hop)`——即使 p、c 落在同一块 PE 上，数据也要过一拍寄存器；1 跳同样
  是一拍。旧版用裸 `hop`（同 PE=0）会低估同 PE 的时延，让搜索误以为把关键链
  全塞进一块 PE 是"免费"的。改成 `max(1, hop)` 后 crit_path 才真正是
  ASAP 调度长度的下界，更能压低仿真 lat。
* **更狠地惩罚长边**：同迭代边 d>=2 的代价略调高，把关键路径上的边压到 <=1 跳，
  因为 hop>=2 会让时延直接变成 2+ 拍。
* **更多起点 + 更多抛光**：R=8（第 0 个=确定性，等价旧版，作为安全兜底），
  抛光 6 轮。place() 本身很快（eval 时间被 mapper+仿真主导），多跑不吃亏。
* **bank 兜底硬化**：多变量交集为空时取覆盖变量最多的单个 bank 集，而不是
  退回全部 PE_MEM（后者可能违反可达性，check.py 直接判 0）。
* **布通性护栏不变**：确定性基线 r=0 一定在候选集里，且所有被选中的放置都满足
  `J_local <= 基线 + 余量`，所以绝不会因为乱摊而导致 mapper 超时。
"""

def place(prob, ii):
    import sys
    import random
    sys.setrecursionlimit(100000)

    nodes = {n["idx"]: n for n in (prob.get("nodes") or [])}
    idxs = sorted(nodes.keys())
    if not idxs:
        return {}

    edges = prob.get("edges") or []
    pe_mem = sorted({tuple(p) for p in (prob.get("pe_mem") or [])})
    pe_alu = sorted({tuple(p) for p in (prob.get("pe_alu") or [])})
    var_bank = prob.get("var_bank") or {}
    alu_ops = prob.get("alu_ops") or {}
    mem_ops = prob.get("mem_ops") or {}

    bank_pes = {}
    for b, ps in (prob.get("bank_pes") or {}).items():
        try:
            bank_pes[int(b)] = [tuple(q) for q in ps]
        except (TypeError, ValueError):
            continue

    # ---------- 节点类型 / 延迟 ----------
    def _is_mem(n):
        if n.get("is_mem"):
            return True
        op = n.get("op")
        return (op in mem_ops) and (op not in alu_ops)

    is_mem = {i: _is_mem(nodes[i]) for i in idxs}

    def _node_lat(i):
        n = nodes[i]
        v = n.get("lat")
        if isinstance(v, (int, float)):
            return max(1, int(v))
        op = n.get("op")
        table = mem_ops if is_mem[i] else alu_ops
        v = table.get(op)
        if isinstance(v, (int, float)):
            return max(1, int(v))
        return 1

    lat = {i: _node_lat(i) for i in idxs}

    # ---------- 合法位置（bank 硬约束）----------
    def _mem_cands(n):
        v = n.get("var")
        if isinstance(v, str):
            names = [v]
        elif isinstance(v, (list, tuple)):
            names = [x for x in v if isinstance(x, str)]
        else:
            names = []
        sets = []
        for nm in names:
            b = var_bank.get(nm)
            if b is None:
                continue
            ps = bank_pes.get(b)
            if ps:
                sets.append(set(ps))
        if not sets:
            return list(pe_mem)
        inter = set.intersection(*sets)
        if inter:
            return sorted(inter)
        # 交集为空：取覆盖变量最多的单个 bank 集（最可能满足可达性）
        return sorted(max(sets, key=len))

    allowed = {}
    for i in idxs:
        cands = _mem_cands(nodes[i]) if is_mem[i] else list(pe_alu)
        if not cands:
            cands = list(pe_mem) + list(pe_alu)
        allowed[i] = sorted(set(cands))

    # ---------- 边 / 邻接（保留重边计数，供目标与增量一致）----------
    parent_set = {i: set() for i in idxs}
    child_set = {i: set() for i in idxs}
    parent_edges = {i: [] for i in idxs}     # (p, is_next)
    child_edges = {i: [] for i in idxs}      # (c, is_next)
    adj_all = {i: set() for i in idxs}
    edge_list = []
    same_parents = {i: set() for i in idxs}

    for e in edges:
        p, c = e[0], e[1]
        if p not in nodes or c not in nodes or p == c:
            continue
        ni = bool(e[2]) if len(e) >= 3 else False
        edge_list.append((p, c, ni))
        adj_all[p].add(c)
        parent_set[c].add(p)
        child_set[p].add(c)
        parent_edges[c].append((p, ni))
        child_edges[p].append((c, ni))
        if not ni:
            same_parents[c].add(p)

    # ---------- SCC（Tarjan，图很小）----------
    index = {}
    low = {}
    onstack = {}
    tstack = []
    comps = []
    counter = [0]

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        tstack.append(v)
        onstack[v] = True
        for w in adj_all.get(v, ()):
            if w not in index:
                strong(w)
                if low[w] < low[v]:
                    low[v] = low[w]
            elif onstack.get(w):
                if index[w] < low[v]:
                    low[v] = index[w]
        if low[v] == index[v]:
            comp = []
            while True:
                w = tstack.pop()
                onstack[w] = False
                comp.append(w)
                if w == v:
                    break
            comps.append(comp)

    for v in idxs:
        if v not in index:
            strong(v)

    scc_partners = {i: [] for i in idxs}
    for comp in comps:
        if len(comp) > 1:
            for a in comp:
                scc_partners[a] = [b for b in comp if b != a]

    # ---------- 代价模型 ----------
    def manh(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    W_SCC = 4.0

    def e_cost(a, b, ni):
        d = manh(a, b)
        if ni:
            # 循环携带：必须紧，hop=0 最优（进入 II 预算）
            if d == 0:
                return 0.0
            if d == 1:
                return 2.0
            return 2.0 + 4.0 * (d - 1)
        # 同迭代：同 PE 争相位重罚，1 跳最优；>=2 跳时延直接涨，重罚
        if d == 0:
            return 4.0
        if d == 1:
            return 0.0
        if d == 2:
            return 0.9
        return 0.9 + 1.3 * (d - 2)

    def load_term(l):
        return 0.5 * l * l

    deg = {i: len(parent_set[i]) + len(child_set[i]) for i in idxs}

    # ---------- 放置顺序：贴已放置邻居优先 ----------
    def build_order():
        placed = set()
        remaining = set(idxs)
        order = []
        while remaining:
            ready = [i for i in remaining if same_parents[i] <= placed]
            if not ready:
                ready = sorted(remaining)
            best_i = None
            best_k = None
            for i in ready:
                k = 0
                for p in parent_set[i]:
                    if p in placed:
                        k += 1
                for ch in child_set[i]:
                    if ch in placed:
                        k += 1
                key = (-k, i)
                if best_k is None or key < best_k:
                    best_k = key
                    best_i = i
            order.append(best_i)
            placed.add(best_i)
            remaining.discard(best_i)
        return order

    def greedy(order, rng, jitter):
        where = {}
        load = {}
        for i in order:
            best = None
            bestv = None
            for c in allowed[i]:
                lc = load.get(c, 0)
                if lc >= ii:
                    continue
                s = load_term(lc + 1)
                for p, ni in parent_edges[i]:
                    if p in where:
                        s += e_cost(where[p], c, ni)
                for ch, ni in child_edges[i]:
                    if ch in where:
                        s += e_cost(c, where[ch], ni)
                for j in scc_partners[i]:
                    if j in where:
                        s += W_SCC * manh(where[j], c)
                if jitter > 0.0:
                    s += rng.uniform(0.0, jitter)
                if bestv is None or s < bestv:
                    bestv = s
                    best = c
            if best is None:
                cands = [c for c in allowed[i] if load.get(c, 0) < ii]
                best = min(cands, key=lambda c: load.get(c, 0)) if cands else allowed[i][0]
            where[i] = best
            load[best] = load.get(best, 0) + 1
        return where, load

    def delta_move(where, load, i, cpos, c):
        d = 0.0
        for p, ni in parent_edges[i]:
            d += e_cost(where[p], c, ni) - e_cost(where[p], cpos, ni)
        for ch, ni in child_edges[i]:
            d += e_cost(c, where[ch], ni) - e_cost(cpos, where[ch], ni)
        for j in scc_partners[i]:
            d += W_SCC * (manh(where[j], c) - manh(where[j], cpos))
        lp = load.get(cpos, 0)
        lc = load.get(c, 0)
        d += load_term(lp - 1) - load_term(lp) + load_term(lc + 1) - load_term(lc)
        return d

    def local_search(where, load, max_passes):
        order = sorted(idxs, key=lambda z: (-deg[z], z))
        for _ in range(max_passes):
            improved = False
            for i in order:
                cpos = where[i]
                best_c = cpos
                best_delta = -1e-9
                for c in allowed[i]:
                    if c == cpos:
                        continue
                    if load.get(c, 0) + 1 > ii:
                        continue
                    dd = delta_move(where, load, i, cpos, c)
                    if dd < best_delta:
                        best_delta = dd
                        best_c = c
                if best_c != cpos:
                    load[cpos] = load.get(cpos, 0) - 1
                    load[best_c] = load.get(best_c, 0) + 1
                    where[i] = best_c
                    improved = True
            if not improved:
                break
        return where, load

    def J_local(where):
        s = 0.0
        for p, c, ni in edge_list:
            s += e_cost(where[p], where[c], ni)
        for a in idxs:
            for b in scc_partners[a]:
                if a < b:
                    s += W_SCC * manh(where[a], where[b])
        lc = {}
        for a in idxs:
            lc[where[a]] = lc.get(where[a], 0) + 1
        for l in lc.values():
            s += load_term(l)
        return s

    def crit_path(where):
        """同迭代最长 (lat + 传输时延) 链，作为 makespan / lat 的代理。

        传输时延用 `max(1, hop)`：同 PE 也要过一拍寄存器，1 跳同样一拍。
        """
        t = {v: 0 for v in idxs}
        for _ in range(len(idxs) + 1):
            changed = False
            for p, c, ni in edge_list:
                if ni:
                    continue
                d = manh(where[p], where[c])
                delay = d if d >= 1 else 1
                val = t[p] + lat[p] + delay
                if val > t[c]:
                    t[c] = val
                    changed = True
            if not changed:
                break
        return max((t[v] + lat[v] for v in idxs), default=0)

    # ---------- 多起点搜索 ----------
    order = build_order()
    candidates = []
    baseline = None
    R = 8
    for r in range(R):
        rng = random.Random(1013904223 + r * 2654435761)
        jitter = 0.0 if r == 0 else 0.6
        w, ld = greedy(order, rng, jitter)
        w, ld = local_search(w, ld, 20)
        jl = J_local(w)
        cp = crit_path(w)
        cand = (w, ld, jl, cp)
        candidates.append(cand)
        if r == 0:
            baseline = cand

    bw, bld, bjl, bcp = baseline
    # 布通性上限：J_local 不允许比基线差太多（基线一定在候选里，故必有解）
    limit = bjl + max(2.0, 0.3 * abs(bjl))

    valid = [c for c in candidates if c[2] <= limit + 1e-9]
    if not valid:
        valid = [baseline]
    ALPHA = 0.7
    best = min(valid, key=lambda c: c[2] + ALPHA * c[3])
    best_w, best_ld, best_jl, best_cp = best

    # ---------- 关键路径抛光（带布通性护栏）----------
    def polish(where, load, max_passes):
        order2 = sorted(idxs, key=lambda z: (-deg[z], z))
        cur = J_local(where) + ALPHA * crit_path(where)
        for _ in range(max_passes):
            improved = False
            for i in order2:
                cpos = where[i]
                best_c = cpos
                best_val = cur
                for c in allowed[i]:
                    if c == cpos:
                        continue
                    if load.get(c, 0) + 1 > ii:
                        continue
                    load[cpos] = load.get(cpos, 0) - 1
                    load[c] = load.get(c, 0) + 1
                    where[i] = c
                    jl = J_local(where)
                    if jl <= limit + 1e-9:
                        val = jl + ALPHA * crit_path(where)
                    else:
                        val = 1e18
                    load[c] = load.get(c, 0) - 1
                    load[cpos] = load.get(cpos, 0) + 1
                    where[i] = cpos
                    if val < best_val - 1e-9:
                        best_val = val
                        best_c = c
                if best_c != cpos:
                    load[cpos] = load.get(cpos, 0) - 1
                    load[best_c] = load.get(best_c, 0) + 1
                    where[i] = best_c
                    cur = best_val
                    improved = True
            if not improved:
                break
        return where, load

    pw, pld = polish(best_w, best_ld, 6)
    pj = J_local(pw)
    pcp = crit_path(pw)
    if pj <= limit + 1e-9 and (pj + ALPHA * pcp) < (best_jl + ALPHA * best_cp) - 1e-9:
        best_w, best_ld = pw, pld

    # ---------- 容量修复（正常情况下不触发）----------
    lc = {}
    for i in idxs:
        lc[best_w[i]] = lc.get(best_w[i], 0) + 1
    for i in sorted(idxs, key=lambda z: -lc.get(best_w[z], 0)):
        pe = best_w[i]
        if lc[pe] <= ii:
            continue
        for c in allowed[i]:
            if lc.get(c, 0) < ii:
                lc[pe] -= 1
                best_w[i] = c
                lc[c] = lc.get(c, 0) + 1
                break

    return {i: (int(best_w[i][0]), int(best_w[i][1])) for i in idxs}

# EVOLVE-BLOCK-END