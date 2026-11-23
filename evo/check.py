"""资源与依赖检查：判断一份放置 (x, y, t) 在这块 CGRA 上是否自洽。

这里**不判断数据流语义是否正确**（那要靠仿真器）。只判断三件事：
  1. 每个算子有没有落在支持它的 PE 上、有没有撞车（硬约束，违反即非法）；
  2. 每条边的相位关系能不能用某条长度 >= 曼哈顿距离的路由实现（总能实现，
     但要付绕路代价）；
  3. 由 1、2 推出的路由代价有多大，作为"好不好"的度量。

相位模型（与 HyCUBE 的 DP 输入寄存器一致）：
  * 普通节点 c 的所有父边，必须在第 t_c 拍到达它的 PE；
  * SELECT / CMERGE 有两个操作数，两个父边必须落在**不同**的拍上
    （一个在 t_c，另一个在 t_c - 1 或 t_c + 1），否则 FU 拿不到两个操作数。
  给定"希望第 a 拍到达"，跳数 h 取满足
      h ≡ (a - t_p - lat_p)  (mod II),  h >= manhattan(p, c)
  的最小值；h 超出曼哈顿距离的部分记为绕路。
"""

SEL_LIKE = frozenset(("SELECT", "CMERGE"))


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _hops(d, t_p, lat_p, a, ii):
    """满足 a 拍到达的最小跳数（>= d）。"""
    return d + ((a - t_p - lat_p - d) % ii)


def check(prob, place, ii=None):
    """place: {node_idx: (x, y, t)}，返回 {"ok", "errors", "cost", "stats"}。"""
    ii = int(ii or prob["ii"])
    if ii <= 0:
        return {"ok": False, "errors": ["II must be positive"], "cost": 0.0, "stats": {}}
    # 循环携带依赖决定的下界：比它小的 II 不存在可行调度，不必再往下算。
    rec_ii = int(prob.get("rec_min_ii") or 1)
    if ii < rec_ii:
        return {"ok": False,
                "errors": ["II=%d below RecMinII=%d (loop-carried dependence)" % (ii, rec_ii)],
                "cost": 0.0, "stats": {}}

    pins = {}
    try:
        for item in (place.items() if hasattr(place, "items") else place):
            if isinstance(item, tuple) and len(item) == 2:
                idx, val = item
            else:
                idx, val = item[0], item[1:]
                if len(val) == 1:
                    val = val[0]
            if isinstance(val, dict):
                x, y, t = val["x"], val["y"], val["t"]
            elif len(val) == 2:
                x, y, t = val[0], val[1], -1
            else:
                x, y, t = val[0], val[1], val[2]
            pins[int(idx)] = (int(x), int(y), int(t))
    except Exception as e:                                      # noqa: BLE001
        return {"ok": False, "errors": ["malformed placement: %r" % (e,)],
                "cost": 0.0, "stats": {}}

    # 允许传绝对调度时刻（morpher 的 mappingwithlatency.txt 就是绝对拍），
    # 统一折算到 [0, ii) 的相位。负值保留给"未指定相位"。
    pins = {i: (x, y, (t % ii) if t >= 0 else t) for i, (x, y, t) in pins.items()}
    # No phase anywhere means the caller let the mapper schedule; then only
    # spatial facts are checkable here.
    spatial_only = all(t < 0 for (_x, _y, t) in pins.values())

    nodes = {n["idx"]: n for n in prob["nodes"]}
    xdim, ydim = prob["xdim"], prob["ydim"]
    alu_ops = set(prob["alu_ops"])
    mem_ops = set(prob["mem_ops"])
    pe_mem = {tuple(p) for p in prob["pe_mem"]}

    alu_lat = prob.get("alu_lat_map") or {}
    mem_lat = prob.get("mem_lat_map") or {}
    alu_lat_default = prob.get("alu_lat") or 1
    mem_lat_default = prob.get("mem_lat") or 1

    def _lat_of(idx):
        """父节点自己的延迟：落在 PE_MEM 上取 FU_MEM 的表，否则取 FU 的表。

        同一个算子在两张表里延迟不同（FU=1、FU_MEM=2），所以延迟取决于父节点
        实际落在哪类 PE，不能按算子名一刀切。
        """
        px, py, _pt = pins[idx]
        op = nodes[idx]["op"]
        if (px, py) in pe_mem:
            return mem_lat.get(op) or alu_lat.get(op) or mem_lat_default
        return alu_lat.get(op) or mem_lat.get(op) or alu_lat_default

    errors = []
    if len(pins) != len(nodes):
        missing = sorted(set(nodes) - set(pins))
        extra = sorted(set(pins) - set(nodes))
        if missing:
            errors.append("unplaced nodes: %s" % missing[:8])
        if extra:
            errors.append("unknown nodes: %s" % extra[:8])
        return {"ok": False, "errors": errors, "cost": 0.0, "stats": {}}

    # 1. 边界 / 算子与 PE 类型 / 相位槽位
    slots = {}
    for idx, (x, y, t) in sorted(pins.items()):
        n = nodes[idx]
        op = n["op"]
        if not (0 <= x < xdim and 0 <= y < ydim):
            errors.append("node %d at (%d,%d) outside %dx%d" % (idx, x, y, xdim, ydim))
            continue
        if t >= 0 and not (0 <= t < ii):
            errors.append("node %d phase %d outside [0,%d)" % (idx, t, ii))
            continue
        is_mem_pe = (x, y) in pe_mem
        # 按坐标上的 FU 表判：PE_MEM 跑 FU_MEM，其余跑 FU。
        # LOAD/STORE 这类只出现在 FU_MEM 里，必须落 PE_MEM；
        # ADD/OSTORE/MOVC/SELECT 这类两边都有，放哪边都合法（延迟不同，见 _lat_of）。
        table = mem_ops if is_mem_pe else alu_ops
        if op not in table:
            if op in mem_ops:
                errors.append("node %d (%s) must sit on a PE_MEM, got (%d,%d)"
                              % (idx, op, x, y))
            else:
                errors.append("node %d op %s not in %s"
                              % (idx, op, "FU_MEM" if is_mem_pe else "FU"))
        # bank 可达性：访存算子带变量时，该变量所在的 bank 必须能连到这块 PE_MEM。
        # 这是 morpher 在 estimateRouting 里就拒掉的硬约束（候选集为空），
        # 静态模型之前完全不知道，于是进化出的解全在这一点上被真 mapper 驳回。
        if prob.get("bank_pes_known") and is_mem_pe:
            var = n.get("var")
            if var:
                bid = (prob.get("var_bank") or {}).get(var)
                if bid is not None:
                    reach = [tuple(r) for r in (prob.get("bank_pes") or {}).get(bid) or []]
                    if reach and (x, y) not in reach:
                        errors.append(
                            "node %d (%s) accesses %s in bank %d, which cannot reach "
                            "PE_MEM (%d,%d); reachable: %s"
                            % (idx, op, var, bid, x, y,
                               ", ".join("(%d,%d)" % (a, b) for a, b in reach)))
                        continue
        key = (x, y, t) if not spatial_only else (x, y, 0)
        if spatial_only:
            slots.setdefault(key, []).append(idx)
        elif key in slots:
            errors.append("node %d and %d share (x=%d,y=%d,t=%d)" % (slots[key], idx, x, y, t))
        else:
            slots[key] = idx

    if errors:
        return {"ok": False, "errors": errors[:12], "cost": 0.0, "stats": {}}

    if spatial_only:
        # Capacity is the only remaining hard fact: a PE runs one op per phase,
        # so it can host at most ii nodes. Overfilling it is legal only if the
        # mapper manages to spread the phases -- but we know it cannot, so it is
        # a hard error.
        over = 0
        for cell, ids in slots.items():
            if len(ids) > ii:
                over += len(ids) - ii
                errors.append("PE (%d,%d) holds %d nodes but has only %d phase slots: %s"
                              % (cell[0], cell[1], len(ids), ii, ids[:8]))
        if errors:
            return {"ok": False, "errors": errors[:12], "cost": 0.0, "stats": {}}
        occ = {cell: len(ids) for cell, ids in slots.items()}
        used = len(occ)
        peak = max(occ.values()) if occ else 0
        return {
            "ok": True,
            "errors": [],
            "cost": float(over * 10),
            "stats": {"spatial_only": True, "pes_used": used, "peak_occ": peak,
                      "n_nodes": len(pins)},
        }

    # 2. 相位与跳数
    parents_of = {}
    for p, c, _ni in prob["edges"]:
        if p in pins and c in pins:
            parents_of.setdefault(c, []).append(p)

    detour = 0
    hops_total = 0
    sel_split = 0
    port_in = {}                       # (x, y, phase) -> 进入该 PE 的边数
    for c, plist in parents_of.items():
        n = nodes[c]
        tc = pins[c][2]
        sel = n["op"] in SEL_LIKE and len(plist) >= 2
        # SELECT 类：给两个父节点分配不同的到达拍，选总跳数最小的那种分法
        best = None
        if sel:
            for shift in (0, 1):
                total = 0
                plan = []
                for k, p in enumerate(plist[:2]):
                    a = (tc - ((k + shift) % 2)) % ii
                    d = _manhattan(pins[p], pins[c])
                    h = _hops(d, pins[p][2], _lat_of(p), a, ii)
                    total += h - d
                    plan.append((p, a, h, d))
                if best is None or total < best[0]:
                    best = (total, plan)
            sel_split += 1
            plan = best[1]
            # 第 3 个及以后的父节点（谓词等）按普通边处理
            for p in plist[2:]:
                d = _manhattan(pins[p], pins[c])
                h = _hops(d, pins[p][2], _lat_of(p), tc, ii)
                plan.append((p, tc, h, d))
        else:
            plan = []
            for p in plist:
                d = _manhattan(pins[p], pins[c])
                h = _hops(d, pins[p][2], _lat_of(p), tc, ii)
                plan.append((p, tc, h, d))
        for (p, a, h, d) in plan:
            detour += h - d
            hops_total += h
            port_in[(pins[c][0], pins[c][1], a % ii)] = \
                port_in.get((pins[c][0], pins[c][1], a % ii), 0) + 1

    # 3. 拥塞：同一 PE 同一拍进入的边超过 4 条输入端口
    port_pressure = 0
    for _k, cnt in port_in.items():
        if cnt > 4:
            port_pressure += cnt - 4

    cost = float(detour + 3 * port_pressure)
    occ = {}
    for (x, y, _t) in slots:
        occ[(x, y)] = occ.get((x, y), 0) + 1
    return {
        "ok": True,
        "errors": [],
        "cost": cost,
        "stats": {
            "detour": detour,
            "hops": hops_total,
            "port_pressure": port_pressure,
            "sel_nodes": sel_split,
            "max_occ": max(occ.values()) if occ else 0,
        },
    }
