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
            else:
                x, y, t = val[0], val[1], val[2]
            pins[int(idx)] = (int(x), int(y), int(t))
    except Exception as e:                                      # noqa: BLE001
        return {"ok": False, "errors": ["malformed placement: %r" % (e,)],
                "cost": 0.0, "stats": {}}

    # 允许传绝对调度时刻（morpher 的 mappingwithlatency.txt 就是绝对拍），
    # 统一折算到 [0, ii) 的相位。负值保留给"未指定相位"。
    pins = {i: (x, y, (t % ii) if t >= 0 else t) for i, (x, y, t) in pins.items()}

    nodes = {n["idx"]: n for n in prob["nodes"]}
    xdim, ydim = prob["xdim"], prob["ydim"]
    alu_ops = set(prob["alu_ops"])
    mem_ops = set(prob["mem_ops"])
    pe_mem = {tuple(p) for p in prob["pe_mem"]}

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
        if not (0 <= t < ii):
            errors.append("node %d phase %d outside [0,%d)" % (idx, t, ii))
            continue
        is_mem_pe = (x, y) in pe_mem
        # 算子能不能落在这一类 PE 上，由架构表决定，不能硬编码。
        #
        # HyCUBE 的 FU_MEM 是 FU 的超集（实测 35 项 vs 28 项，差集正好是
        # LOAD/LOADB/LOADH/LOADCL/STORE/STOREB/STOREH），所以 PE_MEM 也能跑
        # 算术算子，而算术 PE 不能跑访存算子。早期版本写死"算术算子不许上
        # PE_MEM"，把 mapper 自己产出、能过仿真的放置判成非法（mac 的 OSTORE
        # 就落在 PE_MEM_X0_Y1），这是 CAC 那次"verify 高误杀"的同一个坑。
        supported = mem_ops if is_mem_pe else alu_ops
        if n.get("is_mem") and not is_mem_pe:
            errors.append("node %d (%s) 是访存算子，必须放在 PE_MEM，实际 (%d,%d)"
                          % (idx, op, x, y))
        elif op not in supported:
            errors.append("node %d op %s 在 %s 上不支持"
                          % (idx, op, "PE_MEM(FU_MEM)" if is_mem_pe else "PE(FU)"))
        key = (x, y, t)
        if key in slots:
            errors.append("node %d and %d share (x=%d,y=%d,t=%d)" % (slots[key], idx, x, y, t))
        else:
            slots[key] = idx

    if errors:
        return {"ok": False, "errors": errors[:12], "cost": 0.0, "stats": {}}

    # 2. 相位与跳数
    parents_of = {}
    for p, c, _ni in prob["edges"]:
        if p in pins and c in pins:
            parents_of.setdefault(c, []).append(p)

    # 延迟要按"父节点实际落在哪类 PE 上"取：FU 的算子延迟 1，FU_MEM 的延迟 2，
    # 同一个算子（如 OSTORE）放在两边延迟不同。用单一标量会让相位判据与
    # 仿真器差一拍。prob 里没有逐算子表时退回 node["lat"]。
    alu_lat_of = prob.get("alu_lat_of") or {}
    mem_lat_of = prob.get("mem_lat_of") or {}

    def lat_of(p):
        op = nodes[p]["op"]
        if (pins[p][0], pins[p][1]) in pe_mem:
            return mem_lat_of.get(op, prob.get("mem_lat", 2))
        return alu_lat_of.get(op, prob.get("alu_lat", 1))

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
                    h = _hops(d, pins[p][2], lat_of(p) or 1, a, ii)
                    total += h - d
                    plan.append((p, a, h, d))
                if best is None or total < best[0]:
                    best = (total, plan)
            sel_split += 1
            plan = best[1]
            # 第 3 个及以后的父节点（谓词等）按普通边处理
            for p in plist[2:]:
                d = _manhattan(pins[p], pins[c])
                h = _hops(d, pins[p][2], lat_of(p) or 1, tc, ii)
                plan.append((p, tc, h, d))
        else:
            plan = []
            for p in plist:
                d = _manhattan(pins[p], pins[c])
                h = _hops(d, pins[p][2], lat_of(p) or 1, tc, ii)
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
