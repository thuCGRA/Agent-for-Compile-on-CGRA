"""生成 patch 0007：--sa_cost_mode（把 SA 的代价函数换成线长）。

背景：M1.5 证明上游 getCost() = unmapped*100 + congestion*10 + conflict*10 + port_usage
里没有任何延迟/绕行项，热启动可行之后就没有下降空间，退火等于空转。
mode 1 把 port_usage 换成「已布线边的总跳数」（data_routing_path 里每条路径的 hops 累加）。

流程（与 0005/0006 一致，且必须从 0002 开始手工施加，0001 已在 git 历史里）：
  git checkout -- src/mapper
  rm -f src/mapper/include/morpher/mapper/Constraints.h
  清 .rej/.orig/.applied
  按序 patch -p1 < 0002/0003/0004/0005/0006
  cp -a src/mapper /tmp/snap6/mapper
  改代码
  cp -a src/mapper /tmp/snap7/mapper
  diff -ruN snap6/mapper snap7/mapper | sed ... > toolchain/patches/0007-....patch
"""
import os
import shutil
import subprocess
import sys

ROOT = "/home/sutong/acc-thu-agent"
MAPPER = os.path.join(ROOT, "src/mapper")
PATCHDIR = os.path.join(ROOT, "toolchain/patches")
SNAP6 = "/tmp/snap6/mapper"
SNAP7 = "/tmp/snap7/mapper"
OUT = os.path.join(PATCHDIR, "0007-sa-cost-mode.patch")


def sh(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit("FAILED: %s" % cmd)
    return r


def insert_after_line(path, needle, new_lines, expect=1):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    hits = [i for i, ln in enumerate(lines) if needle in ln]
    assert len(hits) == expect, "%s: needle %r hit %d times (want %d)" % (
        os.path.basename(path), needle, len(hits), expect)
    i = hits[0]
    indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
    ins = [indent + l + "\n" for l in new_lines]
    lines[i + 1:i + 1] = ins
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def replace_block(path, old, new):
    with open(path, encoding="utf-8") as f:
        t = f.read()
    assert t.count(old) == 1, "%s: block not found exactly once (%d)" % (
        os.path.basename(path), t.count(old))
    with open(path, "w", encoding="utf-8") as f:
        f.write(t.replace(old, new))


def main():
    # 1) 回到干净状态
    sh("git checkout -- src/mapper", cwd=ROOT)
    for p in (
        os.path.join(MAPPER, "include/morpher/mapper/Constraints.h"),
    ):
        if os.path.exists(p):
            os.remove(p)
    for dirpath, _, files in os.walk(MAPPER):
        for fn in files:
            if fn.endswith((".rej", ".orig")):
                os.remove(os.path.join(dirpath, fn))

    # 2) 按序施加 0002..0006
    for name in ("0002-sa-dump-and-force-anneal.patch",
                 "0003-mapper-constraints.patch",
                 "0004-sa-seed.patch",
                 "0005-sa-empty-candidate-guard.patch",
                 "0006-sa-neighbour-strategies.patch"):
        p = os.path.join(PATCHDIR, name)
        if not os.path.exists(p):
            raise SystemExit("missing %s" % p)
        sh("patch -p1 -N --no-backup-if-mismatch < %s" % p, cwd=MAPPER)

    # 3) 快照
    if os.path.exists("/tmp/snap6"):
        shutil.rmtree("/tmp/snap6")
    os.makedirs("/tmp/snap6")
    sh("cp -a %s %s" % (MAPPER, SNAP6))

    # 4) 改动
    util_h = os.path.join(MAPPER, "include/morpher/util/util.h")
    sa_h = os.path.join(MAPPER, "include/morpher/mapper/SimulatedAnnealingMapper.h")
    sa_cpp = os.path.join(MAPPER, "src/mapper/SimulatedAnnealingMapper.cpp")
    main_cpp = os.path.join(MAPPER, "src/CGRA_xml_compiler.cpp")

    # util.h: 成员
    insert_after_line(util_h, "int sa_pick = 0;",
                      ["int sa_cost_mode = 0; // 0: upstream (port_usage)  1: wire length"])
    # util.h: 选项注册（这是链式调用，新行必须以 .add_option 开头、不带分号）
    insert_after_line(util_h, 'add_option<int>("", "--sa_pick"',
                      ['.add_option<int>("", "--sa_cost_mode", '
                       '"cost function: 0=upstream(port_usage), 1=total wire length", 0)'])
    # util.h: 读取
    insert_after_line(util_h, "sa_pick = args.get_option<int>",
                      ["ret.sa_cost_mode = args.get_option<int>(\"--sa_cost_mode\");"])

    # 头文件
    insert_after_line(sa_h, "int pickStrategy = 0;",
                      ["int costMode = 0;",
                       "int getWireLength();"])

    # cpp: getCost 换目标 + 新增 getWireLength
    old_getcost = """int CGRAXMLCompile::SAMapper::getCost()
{
\tint unmapped_node_number = getNumberOfUnmappedNodes();
\tint port_usage = getPortUsage();
\tint congestion_number = getCongestionNumber();
\tint conflict_number = getConflictNumber();
\tint total_cost = unmapped_node_number * 100 + congestion_number * 10 + conflict_number * 10 + port_usage;
\tLOG(COST) << "unmapped node:" << unmapped_node_number << " port usage:" << port_usage << " congestion:" << congestion_number << " total cost:" << total_cost << "\\n";
\treturn total_cost;
}"""
    new_getcost = """// total hop count over all currently routed edges.
// Each entry of data_routing_path is the A*-found path for one DFG edge;
// consecutive LatPort elements are the PE hops it takes.
int CGRAXMLCompile::SAMapper::getWireLength()
{
\tint wire = 0;
\tfor (auto &kv : data_routing_path)
\t{
\t\tconst std::vector<LatPort> &path = kv.second;
\t\tif (path.size() > 1)
\t\t\twire += (int)path.size() - 1;
\t}
\treturn wire;
}

int CGRAXMLCompile::SAMapper::getCost()
{
\tint unmapped_node_number = getNumberOfUnmappedNodes();
\tint port_usage = getPortUsage();
\tint congestion_number = getCongestionNumber();
\tint conflict_number = getConflictNumber();
\t// costMode 1: replace port_usage with wire length.
\t// The upstream cost has no delay/detour term at all, so once the warm start
\t// is feasible there is nothing left to minimise and the annealer spins.
\tint wire = getWireLength();
\tint shaping = (costMode == 1) ? wire : port_usage;
\tint total_cost = unmapped_node_number * 100 + congestion_number * 10 + conflict_number * 10 + shaping;
\tLOG(COST) << "unmapped node:" << unmapped_node_number << " port usage:" << port_usage
\t          << " wire:" << wire << " congestion:" << congestion_number
\t          << " total cost:" << total_cost << "\\n";
\treturn total_cost;
}"""
    replace_block(sa_cpp, old_getcost, new_getcost)

    # main: 传参
    insert_after_line(main_cpp, "->pickStrategy = args.sa_pick;",
                      ["static_cast<SAMapper *>(mapper)->costMode = args.sa_cost_mode;"])

    # 5) 新快照 + diff
    if os.path.exists("/tmp/snap7"):
        shutil.rmtree("/tmp/snap7")
    os.makedirs("/tmp/snap7")
    sh("cp -a %s %s" % (MAPPER, SNAP7))
    r = sh("diff -ruN %s %s | sed -e 's|^--- %s/|--- a/|' -e 's|^+++ %s/|+++ b/|'"
           % (SNAP6, SNAP7, SNAP6, SNAP7), check=False)
    diff = r.stdout
    if not diff.strip():
        raise SystemExit("diff is empty — changes did not apply?")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(diff)
    print("写入 %s（%d 行）" % (OUT, diff.count("\n")))
    print(diff[:1500])


if __name__ == "__main__":
    main()
