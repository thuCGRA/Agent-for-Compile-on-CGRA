import pathlib

root = pathlib.Path("/home/sutong/acc-thu-agent/src/mapper")
h = root / "include/morpher/mapper/SimulatedAnnealingMapper.h"
cpp = root / "src/mapper/SimulatedAnnealingMapper.cpp"
uh = root / "include/morpher/util/util.h"
main = root / "src/CGRA_xml_compiler.cpp"

def sub(text, old, new, n=1, tag=""):
    c = text.count(old)
    assert c == n, "%s: 期望 %d 处，实际 %d 处" % (tag or old[:40], n, c)
    return text.replace(old, new)

# ---------------- header ----------------
t = h.read_text()
assert "#include <random>" in t, "header 应已包含 <random>"
t = sub(
    t,
    "\tbool forceAnneal = false;",
    "\t// M1.5: SA 的随机性必须可复现。上游每个随机点都现场 new 一个\n"
    "\t// std::random_device（真随机源），导致同一输入两次跑出不同映射、\n"
    "\t// 甚至一次能映射一次不能。做 agent 与 SA 的对比实验前先钉住种子。\n"
    "\tstd::mt19937 rng{20136u};\n"
    "\tvoid seedRng(unsigned s) { rng.seed(s); }\n"
    "\tbool forceAnneal = false;",
    1, "rng member")
t = sub(
    t,
    "\t\t\treturn probability > ((float)rand() / (float)RAND_MAX);",
    "\t\t\t// 上游用全局 rand()（从不 srand，实际恒定），改走 rng 以便 --sa_seed 统一控制\n"
    "\t\t\tstd::uniform_real_distribution<double> u01(0.0, 1.0);\n"
    "\t\t\treturn probability > u01(rng);",
    1, "whetherAccept rand")
h.write_text(t)

# ---------------- cpp ----------------
t = cpp.read_text()
t = sub(
    t,
    "\tstd::random_device r;\n"
    "\tstd::default_random_engine e1(r());\n"
    "\tstd::uniform_int_distribution<int> uniform_dist(0, sortedNodeList.size() - 1);\n"
    "\treturn sortedNodeList[std::abs(std::round(uniform_dist(e1)))];",
    "\t// M1.5: 用成员 rng（可 --sa_seed），不再每次调用都重新播种真随机源\n"
    "\tstd::mt19937 &e1 = rng;\n"
    "\tstd::uniform_int_distribution<int> uniform_dist(0, sortedNodeList.size() - 1);\n"
    "\treturn sortedNodeList[std::abs(std::round(uniform_dist(e1)))];",
    1, "selectDFGNodeToUnmap rng")
t = sub(
    t,
    "\tstd::random_device r;\n"
    "\tstd::default_random_engine e1(r());\n"
    "\tstd::uniform_int_distribution<int> uniform_dist(0, candidates.size() - 1);",
    "\tstd::mt19937 &e1 = rng;\n"
    "\tstd::uniform_int_distribution<int> uniform_dist(0, candidates.size() - 1);",
    1, "selectAParentForDFGNode rng")
t = sub(
    t,
    "\tunsigned seed = std::chrono::system_clock::now().time_since_epoch().count();\n\n"
    "\tshuffle(candidateDests.begin(), candidateDests.end(), std::default_random_engine(seed));",
    "\t// M1.5: 候选表洗牌也走 rng（上游用 system_clock 当种子，无法复现）\n"
    "\tshuffle(candidateDests.begin(), candidateDests.end(), rng);",
    1, "shuffle seed")
cpp.write_text(t)

# ---------------- util.h ----------------
t = uh.read_text()
t = sub(
    t,
    "\tbool sa_force_anneal = false;",
    "\tbool sa_force_anneal = false;\n"
    "\tint sa_seed = 20136;   // M1.5: SA 的 RNG 种子，同种子必须得到同一映射",
    1, "sa_seed field")
t = sub(
    t,
    '.add_option<bool>("", "--sa_force_anneal"',
    '.add_option<int>("", "--sa_seed", "RNG seed for SA (same seed => same mapping)", 20136)\n'
    '\t\t\t\t\t.add_option<bool>("", "--sa_force_anneal"',
    1, "sa_seed option")
t = sub(
    t,
    'ret.sa_force_anneal = args.get_option<bool>("--sa_force_anneal");',
    'ret.sa_force_anneal = args.get_option<bool>("--sa_force_anneal");\n'
    '\tret.sa_seed = args.get_option<int>("--sa_seed");',
    1, "sa_seed get_option")
uh.write_text(t)

# ---------------- main ----------------
t = main.read_text()
t = sub(
    t,
    "static_cast<SAMapper *>(mapper)->forceAnneal = args.sa_force_anneal;",
    "static_cast<SAMapper *>(mapper)->seedRng((unsigned)args.sa_seed);\n"
    "\t\t\t\tstatic_cast<SAMapper *>(mapper)->forceAnneal = args.sa_force_anneal;",
    1, "seedRng call")
main.write_text(t)
print("OK: --sa_seed 已接入（header 2 处 / cpp 3 处 / util.h 3 处 / main 1 处）")
