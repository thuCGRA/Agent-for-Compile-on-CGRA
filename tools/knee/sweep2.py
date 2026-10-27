"""Congestion knee sweep: kernel size x array size, pure A* (-m 0), II free.

Instances: work/armB/dfggen/<k>/*_PartPredDFG.xml (generated with the dfggen pass from
morpher_benchmarks C sources) plus the spike array_add. Arch jsons: the known-good 4x4
(spike) and the NxN/NxM versions derived from it with make_arch.py.

Outputs work/armB/sweep2/sweep2.csv + sweep2.md
"""
import glob
import os
import re
import subprocess
import time

REPO = "/home/steve/cac-armB"
MAPPER = os.path.join(REPO, "toolchain/out/cgra_xml_mapper")
OUT = os.path.join(REPO, "work/armB/sweep2")
SIZES = [(4, 4), (3, 3), (3, 2), (2, 2)]
ARCH4 = os.path.join(REPO, "work/armB/spike/array_add_mem.json")
TIMEOUT = 300
os.makedirs(OUT, exist_ok=True)


def arch_of(x, y):
    if (x, y) == (4, 4):
        return ARCH4
    return os.path.join(REPO, "work/armB/archs/hy_%dx%d.json" % (x, y))


def instances():
    out = []
    for p in sorted(glob.glob(os.path.join(REPO, "work/armB/dfggen/*/*_PartPredDFG.xml"))):
        k = p.split("/")[-2]
        out.append((k, p))
    out.append(("spike_array_add", os.path.join(REPO, "work/armB/spike/array_add_PartPredDFG.xml")))
    return out


def run(kernel, dfg, x, y):
    tag = "%s_%dx%d" % (kernel, x, y)
    wd = os.path.join(OUT, tag)
    os.makedirs(wd, exist_ok=True)
    cmd = [MAPPER, "-m", "0", "-d", dfg, "-x", str(x), "-y", str(y),
           "-t", "HyCUBE_4REG", "-j", arch_of(x, y)]
    t0 = time.time()
    to = False
    with open(os.path.join(wd, "map.log"), "w") as f:
        try:
            subprocess.run(cmd, cwd=wd, stdout=f, stderr=subprocess.STDOUT, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            to = True
    wall = time.time() - t0
    txt = open(os.path.join(wd, "map.log"), errors="replace").read()
    r = {"kernel": kernel, "x": x, "y": y, "nodes": txt.count("Node added"), "wall_s": round(wall, 1)}
    m = re.search(r"Initial target II = (\d+) \(ResMin II = (\d+) , RecMin II = (\d+)", txt)
    if m:
        r["resmin"], r["recmin"] = m.group(2), m.group(3)
    s = re.search(r"Map Success with II = (\d+)\s+\(lat = (\d+)\)", txt)
    r["mappable"] = bool(s)
    r["ii"] = s.group(1) if s else ""
    r["lat"] = s.group(2) if s else ""
    inc = re.findall(r"Increasing II to (\d+)", txt)
    if not s:
        r["stage"] = "timeout" if to else ("ii_upperbound" if "upperbound II reached" in txt else "fail")
        r["last_ii"] = inc[-1] if inc else ""
    else:
        r["stage"] = "success"
        r["last_ii"] = ""
    return r


rows = []
for kernel, dfg in instances():
    for (x, y) in SIZES:
        r = run(kernel, dfg, x, y)
        rows.append(r)
        print("%-16s %dx%d ok=%-5s II=%-3s res/rec=%-7s lat=%-4s stage=%-13s %s %.1fs"
              % (kernel, x, y, r["mappable"], r["ii"], "%s/%s" % (r.get("resmin", "-"), r.get("recmin", "-")),
                 r["lat"], r["stage"], ("II>" + str(r["last_ii"])) if r["last_ii"] else "", r["wall_s"]),
              flush=True)

keys = ["kernel", "x", "y", "mappable", "ii", "lat", "resmin", "recmin", "stage", "last_ii", "wall_s"]
with open(os.path.join(OUT, "sweep2.csv"), "w") as f:
    f.write(",".join(keys) + "\n")
    for r in rows:
        f.write(",".join(str(r.get(k, "")) for k in keys) + "\n")

lines = ["# 拥塞膝点：kernel x array size，k=0（纯 A*）", "",
         "口径：`-m 0`、II 自由、超时 %ds。实例 = morpher_benchmarks 经 dfggen pass 生成的 "
         "PartPred DFG；架构 json 由可用的 4x4 版本按 NxN 子集派生（make_arch.py，去掉显式 "
         "CGRA CONNECTIONS 后由 GRID 模式自行推导）。" % TIMEOUT, "",
         "| kernel | 4x4 | 3x3 | 3x2 | 2x2 |", "|---|---|---|---|---|"]
for kernel, _ in instances():
    cells = []
    for (x, y) in SIZES:
        r = next((r for r in rows if r["kernel"] == kernel and (r["x"], r["y"]) == (x, y)), None)
        if not r:
            cells.append("-")
        elif r["mappable"]:
            cells.append("ok II=%s" % r["ii"])
        else:
            cells.append("FAIL%s" % (("(II>%s)" % r["last_ii"]) if r["last_ii"] else ""))
    lines.append("| %s | %s |" % (kernel, " | ".join(cells)))

lines += ["", "| size | mappable | 成功率 |", "|---|---|---|"]
for (x, y) in SIZES:
    rs = [r for r in rows if (r["x"], r["y"]) == (x, y)]
    ok = sum(1 for r in rs if r["mappable"])
    lines.append("| %dx%d | %d/%d | %d%% |" % (x, y, ok, len(rs), round(100.0 * ok / len(rs))))

open(os.path.join(OUT, "sweep2.md"), "w").write("\n".join(lines) + "\n")
print("\n" + "\n".join(lines))
