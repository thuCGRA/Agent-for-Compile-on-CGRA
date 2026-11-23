"""把 acc-thu 的 DFG 与硬件描述解析成演化程序需要的最小问题视图。

只依赖标准库。DFG 的属性偶有畸形（`ALAP="0"BB=""CONST="4094"` 缺空格），
所以用正则按属性名抓取，不用 XML parser。
"""
import json
import os
import re

# 必须放在 PE_MEM（x=0 那一列）上的算子
MEMORY_OPS = frozenset((
    "LOAD", "LOADB", "LOADH", "LOADCL",
    "STORE", "STOREB", "STOREH",
))

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
            "asap": int(attrs.get("ASAP", "0") or 0),
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


def _rec_min_ii(nodes, edges):
    """循环携带依赖给出的 II 下界，定义与 morpher 完全一致。

    抄自 `src/mapper/src/mapper/HeuristicMapper.cpp:2002-2039`：
    对每条边 (p -> c)，若 `asap[c] <= asap[p]` 就当作反向边，
    取 `asap[p] - asap[c] + 1` 作为它的 II 下界，全部取最大。
    这个值不参与调度，只用来给 II 搜索定起点
    （`CGRA_xml_compiler.cpp:103` 的 `II = max(ResMin, RecMin, UserII)`）。

    我们复现它，是为了让 evo 的适应度知道"II 不可能小于几"——
    实测三个 kernel 都是 4，之前把 II=3 当目标是在追一个不存在的解。
    """
    asap = {n["idx"]: int(n.get("asap") or 0) for n in nodes}
    rec = 0
    for p, c, _npb in edges:
        if p in asap and c in asap and asap[c] <= asap[p]:
            rec = max(rec, asap[p] - asap[c] + 1)
    return rec


def build_problem(kernel, dfg_path, arch_path, ii):
    """组装一个可直接喂给演化程序的 dict（全部是内建类型）。"""
    nodes, edges = parse_dfg(dfg_path)
    arch = parse_arch(arch_path)

    def lat(op, is_mem):
        # 延迟取决于放在哪类 PE 上：访存算子走 FU_MEM，其余走 FU。
        if is_mem:
            return arch["mem_ops"].get(op, arch["mem_lat"])
        if op in arch["alu_ops"]:
            return arch["alu_ops"][op]
        return arch["mem_ops"].get(op)

    node_list = []
    for idx in sorted(nodes):
        n = nodes[idx]
        node_list.append({
            "idx": idx,
            "op": n["op"],
            "parents": n["parents"],
            "children": n["children"],
            "const": n["const"],
            "var": n["var"],
            "is_mem": n["op"] in MEMORY_OPS,
            "lat": lat(n["op"], n["op"] in MEMORY_OPS),
            "asap": n.get("asap") or 0,
        })

    hist = {}
    for n in node_list:
        hist[n["op"]] = hist.get(n["op"], 0) + 1

    # 资源下界：算子种类在各 PE 上的配额
    res_ii = 1
    for op, cnt in hist.items():
        if op in arch["mem_ops"]:
            cap = len(arch["pe_mem"])
        elif op in arch["alu_ops"]:
            cap = len(arch["pe_alu"])
        else:
            cap = 1
        res_ii = max(res_ii, -(-cnt // max(1, cap)))
    res_ii = max(res_ii, -(-len(node_list) // max(1, len(arch["pe_alu"]))))
    rec_ii = _rec_min_ii(node_list, edges)

    return {
        "kernel": kernel,
        "ii": int(ii),
        "xdim": arch["xdim"],
        "ydim": arch["ydim"],
        "pe_mem": [list(p) for p in arch["pe_mem"]],
        "pe_alu": [list(p) for p in arch["pe_alu"]],
        "alu_ops": sorted(arch["alu_ops"]),
        "mem_ops": sorted(arch["mem_ops"]),
        "alu_lat": arch["alu_lat"],
        "mem_lat": arch["mem_lat"],
        "alu_lat_map": dict(arch["alu_ops"]),
        "mem_lat_map": dict(arch["mem_ops"]),
        "var_bank": arch["var_bank"],
        "bank_vars": {str(k): v for k, v in arch["bank_vars"].items()},
        "res_min_ii": res_ii,
        "rec_min_ii": rec_ii,
        "n_nodes": len(node_list),
        "nodes": node_list,
        "edges": [[p, c, ni] for p, c, ni in edges],
        "op_hist": hist,
    }
