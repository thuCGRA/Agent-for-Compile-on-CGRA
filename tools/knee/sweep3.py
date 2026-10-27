"""Density ladder: duplicate a PartPred DFG k times (textual, structure-preserving) and
map with pure A* (-m 0).

The generated morpher kernels are small (11-25 nodes), so array size alone does not
gate mappability: the mapper's II search absorbs density. Duplicating a real DFG keeps
the op mix while raising nodes/PE, which is the density knob that can actually make
routing fail at the natural II.

Copy 0 keeps the original BB names (so k=1 is byte-equivalent to the source DFG);
copy c>0 gets every BB name suffixed with _cN, and its own MutexBB block.

Outputs work/armB/sweep3/sweep3.csv + sweep3.md
"""
import os
import re
import subprocess
import time

REPO = "/home/steve/cac-armB"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
OUT = os.path.join(REPO, "work/armB/sweep3")
DFGD = os.path.join(OUT, "dfgs")
BASES = [
    ("array_add", "work/armB/dfggen/array_add/array_add_PartPredDFG.xml", [1, 2, 3, 4]),
    ("array_cond", "work/armB/dfggen/array_cond/array_cond_PartPredDFG.xml", [1, 2, 3]),
    ("trmm", "work/armB/dfggen/trmm/trmm_PartPredDFG.xml", [2, 3, 5]),
]
SIZES = [(4, 4), (3, 3), (2, 2)]
ARCH4 = os.path.join(REPO, "work/armB/spike/array_add_mem.json")
TIMEOUT = 420
os.makedirs(DFGD, exist_ok=True)


def load(xml):
    txt = open(xml).read()
    mb = re.search(r"<MutexBB>.*?</MutexBB>", txt, re.S).group(0)
    nodes = re.findall(r"<Node .*?</Node>", txt, re.S)
    return mb, [n for n in nodes if "<Node " in n]


def offset_ids(block, off):
    block = re.sub(r'idx="(\d+)"', lambda m: 'idx="%d"' % (int(m.group(1)) + off), block)
    return block


def suffix_bbs(block, tag):
    block = re.sub(r'(<BB[12] name=")([^"]*)"', lambda m: m.group(1) + m.group(2) + tag + '"', block)
    block = re.sub(r'BB="([^"]*)"', lambda m: 'BB="' + m.group(1) + tag + '"', block)
    return block


def build(base, xml, k):
    full = os.path.join(REPO, xml)
    mb, nodes = load(full)
    n = max(int(m) for m in re.findall(r'<Node idx="(\d+)"', "\n".join(nodes))) + 1
    mutex, body = [], []
    for c in range(k):
        off = c * n
        tag = "" if c == 0 else "_c%d" % c
        mutex.append(suffix_bbs(mb, tag) if tag else mb)
        for nd in nodes:
            nd = offset_ids(nd, off)
            body.append(suffix_bbs(nd, tag) if tag else nd)
    p = os.path.join(DFGD, "%s_x%d.xml" % (base, k))
    open(p, "w").write("\n".join(mutex) + '\n<DFG count="%d">\n' % (k * len(nodes))
                       + "\n".join(body) + "\n</DFG>\n")
    return p, k * len(nodes)


def arch_of(x, y):
    return ARCH4 if (x, y) == (4, 4) else os.path.join(REPO, "work/armB/archs/hy_%dx%d.json" % (x, y))


def run(tag, dfg, x, y):
    wd = os.path.join(OUT, "%s_%dx%d" % (tag, x, y))
    os.makedirs(wd, exist_ok=True)
    cmd = [MAPPER, "-m", "0", "-d", dfg, "-x", str(x), "-y", str(y), "-t", "HyCUBE_4REG", "-j", arch_of(x, y)]
    t0 = time.time()
    to = False
    with open(os.path.join(wd, "map.log"), "w") as f:
        try:
            subprocess.run(cmd, cwd=wd, stdout=f, stderr=subprocess.STDOUT, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            to = True
    wall = time.time() - t0
    txt = open(os.path.join(wd, "map.log"), errors="replace").read()
    r = {"tag": tag, "x": x, "y": y, "wall_s": round(wall, 1)}
    m = re.search(r"Initial target II = (\d+) \(ResMin II = (\d+) , RecMin II = (\d+)", txt)
    if m:
        r["resmin"], r["recmin"] = m.group(2), m.group(3)
    s = re.search(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)", txt)
    r["mappable"] = bool(s)
    r["ii"] = s.group(1) if s else ""
    r["lat"] = s.group(2) if s else ""
    inc = re.findall(r"Increasing II to (\d+)", txt)
    r["stage"] = ("success" if s else
                  ("timeout" if to else ("ii_upperbound" if "upperbound II reached" in txt else "fail")))
    r["last_ii"] = "" if s else (inc[-1] if inc else "")
    return r


rows = []
for base, xml, ks in BASES:
    for k in ks:
        tag = "%s_x%d" % (base, k)
        dfg, nn = build(base, xml, k)
        for (x, y) in SIZES:
            r = run(tag, dfg, x, y)
            r["nodes"] = nn
            rows.append(r)
            print("%-18s n=%-3d %dx%d ok=%-5s II=%-3s res/rec=%-7s lat=%-4s %-13s %5.1fs"
                  % (tag, nn, x, y, r["mappable"], r["ii"],
                     "%s/%s" % (r.get("resmin", "-"), r.get("recmin", "-")), r["lat"], r["stage"], r["wall_s"]),
                  flush=True)

keys = ["tag", "nodes", "x", "y", "mappable", "ii", "lat", "resmin", "recmin", "stage", "last_ii", "wall_s"]
with open(os.path.join(OUT, "sweep3.csv"), "w") as f:
    f.write(",".join(keys) + "\n")
    for r in rows:
        f.write(",".join(str(r.get(k, "")) for k in keys) + "\n")

lines = ["# 密度阶梯：同 kernel 复制 k 份，k=0（纯 A*）", "",
         "| tag | nodes | 4x4 | 3x3 | 2x2 |", "|---|---|---|---|---|"]
tags = []
for r in rows:
    if r["tag"] not in tags:
        tags.append(r["tag"])
for tag in tags:
    nodes = next(r["nodes"] for r in rows if r["tag"] == tag)
    cells = []
    for (x, y) in SIZES:
        r = next((r for r in rows if r["tag"] == tag and (r["x"], r["y"]) == (x, y)), None)
        if not r:
            cells.append("-")
        elif r["mappable"]:
            cells.append("II=%s (%.1fs)" % (r["ii"], r["wall_s"]))
        else:
            cells.append("%s%s" % (r["stage"], ("(II>%s)" % r["last_ii"]) if r["last_ii"] else ""))
    lines.append("| %s | %d | %s |" % (tag, nodes, " | ".join(cells)))

lines += ["", "| size | mappable | 成功率 |", "|---|---|---|"]
for (x, y) in SIZES:
    rs = [r for r in rows if (r["x"], r["y"]) == (x, y)]
    ok = sum(1 for r in rs if r["mappable"])
    lines.append("| %dx%d | %d/%d | %d%% |" % (x, y, ok, len(rs), round(100.0 * ok / len(rs))))

open(os.path.join(OUT, "sweep3.md"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
