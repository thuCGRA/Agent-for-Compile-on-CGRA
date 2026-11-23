"""把 acc-thu 的 DFG 与硬件描述解析成演化程序需要的最小问题视图。

只依赖标准库。DFG 的属性偶有畸形（`ALAP="0"BB=""CONST="4094"` 缺空格），
所以用正则按属性名抓取，不用 XML parser。
"""
import json
import os
import re

# 必须放在 PE_MEM（x=0 那一列）上的算子。
#
# 这里必须照抄 mapper 的判定，不能凭字面猜：`PathFinderMapper.cpp:3228`
#   checkMEMOp(op):
#       if op 含 "OLOAD" 或 "OSTORE": return false      <- 这两个不算访存
#       if op 含 "LOAD"  或 "STORE":  return true
#       return false
# OLOAD/OSTORE 是外层循环的访存节点，mapper 只把它们当"带基址常量的普通算子"，
# 允许落在任意 PE 上（实测 mac 的 OSTORE 就被放在了 PE_MEM_X0_Y1）。
# 早期版本用硬编码 {LOAD, LOADB, ...} 把 OSTORE 判成算术算子，导致
# 一份 mapper 自己产出、能过仿真的放置被我们判成"非法"——这正是
# CAC 那次"verify 零漏报但高误杀"的重演，所以改成照抄工具链。
_MEM_SUBSTR = ("LOAD", "STORE")
_MEM_EXCLUDE = ("OLOAD", "OSTORE")


def is_mem_op(op):
    """该算子是否必须落在 PE_MEM 上（与 mapper 的 checkMEMOp 一致）。"""
    op = op or ""
    if any(x in op for x in _MEM_EXCLUDE):
        return False
    return any(x in op for x in _MEM_SUBSTR)

_NODE_RE = re.compile(r"<Node\b(.*?)</Node>", re.S)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_OP_RE = re.compile(r"<OP>\s*([^<\s]+)\s*</OP>")
_BP_RE = re.compile(r"<BasePointerName[^>]*>([^<]*)</BasePointerName>")
_IN_RE = re.compile(r'<Input\s+idx="(\d+)"')
_OUT_RE = re.compile(r"<Output\s+([^/>]*)/?>")


class CGRAError(Exception):
    pass


def parse_dfg(path):
    """解析 dfg.xml，返回 (nodes, edges)。

    nodes: {idx: {"idx","op","const","var","parents","children","nextiter"}}
    edges: [(parent_idx, child_idx, nextiter)]  已去重
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    nodes = {}
    edges = set()
    for m in _NODE_RE.finditer(text):
        block = m.group(1)
        head, _, rest = block.partition(">")          # 属性在第一个 '>' 之前
        attrs = dict(_ATTR_RE.findall(head))
        idx = int(attrs["idx"])
        op_m = _OP_RE.search(rest)
        if not op_m:
            raise CGRAError("node %d has no <OP>" % idx)
        bp_m = _BP_RE.search(rest)
        const = attrs.get("CONST", "")
        node = {
            "idx": idx,
            "op": op_m.group(1),
            "const": int(const) if const.lstrip("-").isdigit() else None,
            "var": bp_m.group(1).strip() if bp_m else None,
            "parents": [],
            "children": [],
            "nextiter": False,
        }
        for im in _IN_RE.finditer(rest):
            p = int(im.group(1))
            node["parents"].append(p)
            edges.add((p, idx, 0))
        for om in _OUT_RE.finditer(rest):
            oattrs = dict(_ATTR_RE.findall(om.group(1)))
            if "idx" not in oattrs:
                continue
            c = int(oattrs["idx"])
            ni = int(oattrs.get("nextiter", "0") or 0)
            node["children"].append(c)
            if ni:
                node["nextiter"] = True
            edges.add((idx, c, ni))
        nodes[idx] = node

    if not nodes:
        raise CGRAError("no <Node> found in %s" % path)

    for p, c, _ in edges:
        if p in nodes and c in nodes and c not in nodes[p]["children"]:
            nodes[p]["children"].append(c)
        if p in nodes and c in nodes and p not in nodes[c]["parents"]:
            nodes[c]["parents"].append(p)

    for n in nodes.values():
        n["parents"] = sorted(set(n["parents"]))
        n["children"] = sorted(set(n["children"]))

    return nodes, sorted(edges)


def parse_arch(path):
    """解析注入 bank 之后的 arch.json。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    A = raw.get("ARCH", raw)

    grid = None
    for sub in A.get("CGRA", {}).get("SUBMODS", []) or []:
        if str(sub.get("PATTERN", "")).upper() == "GRID":
            grid = sub
            break
    if grid is None:
        raise CGRAError("no PATTERN=GRID submodule in %s" % path)

    dims = grid.get("DIMS", {})
    xdim = int(dims.get("X", 0))
    ydim = int(dims.get("Y", 0))
    if xdim <= 0 or ydim <= 0:
        raise CGRAError("bad DIMS in %s: %r" % (path, dims))

    kind = {}
    for mod in grid.get("MODS", []) or []:
        x, y = int(mod["X"]), int(mod["Y"])
        kind[(x, y)] = str(mod.get("MOD", "PE"))

    pe_mem = sorted(k for k, v in kind.items() if v.upper().startswith("PE_MEM"))
    pe_alu = sorted(k for k, v in kind.items() if not v.upper().startswith("PE_MEM"))
    if not pe_mem or not pe_alu:
        raise CGRAError("need both PE_MEM and PE units, got %d/%d" % (len(pe_mem), len(pe_alu)))

    alu_ops = {k: int(v) for k, v in (A.get("FU", {}).get("OPS", {}) or {}).items()}
    mem_ops = {k: int(v) for k, v in (A.get("FU_MEM", {}).get("OPS", {}) or {}).items()}

    # SPM_B<i>_WRAPPER → bank i 里有哪些变量
    var_bank = {}
    bank_vars = {}
    for name, mod in A.items():
        m = re.match(r"^SPM_B(\d+)_WRAPPER$", name)
        if not m:
            continue
        bid = int(m.group(1))
        layout = dict(mod.get("DATA_LAYOUT", {}) or {})
        for spm in (mod.get("SUBMODS", {}) or {}).get("SPM", []) or []:
            layout.update(spm.get("DATA_LAYOUT", {}) or {})
        bank_vars[bid] = sorted(layout)
        for v in layout:
            var_bank.setdefault(v, bid)

    return {
        "file": os.path.basename(path),
        "xdim": xdim,
        "ydim": ydim,
        "kind": kind,
        "pe_mem": pe_mem,
        "pe_alu": pe_alu,
        "alu_ops": alu_ops,
        "mem_ops": mem_ops,
        "alu_lat": min(alu_ops.values()) if alu_ops else 1,
        "mem_lat": min(mem_ops.values()) if mem_ops else 2,
        "var_bank": var_bank,
        "bank_vars": bank_vars,
    }


def build_problem(kernel, dfg_path, arch_path, ii):
    """组装一个可直接喂给演化程序的 dict（全部是内建类型）。"""
    nodes, edges = parse_dfg(dfg_path)
    arch = parse_arch(arch_path)

    def lat(op, mem):
        # 延迟取决于放在哪类 PE 上：访存算子走 FU_MEM，其余走 FU。
        if mem:
            return arch["mem_ops"].get(op, arch["mem_lat"])
        if op in arch["alu_ops"]:
            return arch["alu_ops"][op]
        return arch["mem_ops"].get(op)

    node_list = []
    for idx in sorted(nodes):
        n = nodes[idx]
        mem = is_mem_op(n["op"])
        node_list.append({
            "idx": idx,
            "op": n["op"],
            "parents": n["parents"],
            "children": n["children"],
            "const": n["const"],
            "var": n["var"],
            "is_mem": mem,
            "lat": lat(n["op"], mem),
        })

    hist = {}
    for n in node_list:
        hist[n["op"]] = hist.get(n["op"], 0) + 1

    # 资源下界。注意 mem_ops 是 FU_MEM 的表，而 FU_MEM ⊇ FU（35 vs 28，
    # 差集正好是 7 个 LOAD*/STORE*），所以"op 在 mem_ops 里"不等于"必须放 PE_MEM"，
    # 必须用 is_mem_op 判。早期版本用 `op in arch["mem_ops"]` 当判据，把所有
    # 算术算子都算成 4 个 PE_MEM 的配额，下界被高估。
    res_ii = 1
    n_units = max(1, len(arch["pe_mem"]) + len(arch["pe_alu"]))
    for op, cnt in hist.items():
        if is_mem_op(op):
            cap = len(arch["pe_mem"])
        elif op in arch["alu_ops"] or op in arch["mem_ops"]:
            cap = n_units          # 任何 PE 都能跑算术算子
        else:
            cap = 1
        res_ii = max(res_ii, -(-cnt // max(1, cap)))
    res_ii = max(res_ii, -(-len(node_list) // n_units))

    return {
        "kernel": kernel,
        "ii": int(ii),
        "xdim": arch["xdim"],
        "ydim": arch["ydim"],
        "pe_mem": [list(p) for p in arch["pe_mem"]],
        "pe_alu": [list(p) for p in arch["pe_alu"]],
        "alu_ops": sorted(arch["alu_ops"]),
        "mem_ops": sorted(arch["mem_ops"]),
        # 逐算子的延迟表。FU 与 FU_MEM 的同名算子延迟不同（1 vs 2），
        # 所以相位要按"节点实际落在哪类 PE 上"取，不能用单一标量。
        "alu_lat_of": dict(arch["alu_ops"]),
        "mem_lat_of": dict(arch["mem_ops"]),
        "alu_lat": arch["alu_lat"],
        "mem_lat": arch["mem_lat"],
        "var_bank": arch["var_bank"],
        "bank_vars": {str(k): v for k, v in arch["bank_vars"].items()},
        "res_min_ii": res_ii,
        "n_nodes": len(node_list),
        "nodes": node_list,
        "edges": [[p, c, ni] for p, c, ni in edges],
        "op_hist": hist,
    }
