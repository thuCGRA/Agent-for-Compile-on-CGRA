"""M1.5：给 SA 的两个邻域算子加策略开关（patch 0006）。

上游的邻域算子是：
  - selectDFGNodeToUnmap(): sortedNodeList[uniform(0,n-1)]  —— 纯随机挑节点
  - getRandomDPCandidate(node).front(): 洗牌后取第一个        —— 等于随机挑位置

M0 实测：真退火跑满 14900 次移动（149 个温度台阶），new best cost 只出现 2 次
（167 → 165）。瓶颈不是"跑得不够"，是这两处随机。

这里各加一个可选策略（默认 0 = 上游行为，保证已有结论不变）：
  --sa_select 1: 按"blame"挑最该被挪的节点（同 PE 拥挤 + 已布线边的绕行），
                 从前 4 名里随机取一个——保留随机性，否则退化成贪心。
  --sa_pick   1: 按"到已放置邻居的曼哈顿距离 + 该 PE 占用数"给候选排序，
                 取代随机洗牌。
"""
import pathlib

ROOT = pathlib.Path("/home/sutong/acc-thu-agent/src/mapper")


def insert_after_line(text, needle, new_lines, tag):
    """在包含 needle 的那一行之后插入 new_lines（沿用该行缩进）。"""
    lines = text.split("\n")
    hits = [i for i, l in enumerate(lines) if needle in l]
    assert len(hits) == 1, "%s: 期望 1 处，实际 %d 处" % (tag, len(hits))
    i = hits[0]
    indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
    lines[i + 1:i + 1] = [indent + nl for nl in new_lines]
    return "\n".join(lines)


# ---------------- util.h：CLI ----------------
util = ROOT / "include/morpher/util/util.h"
t = util.read_text()

t = insert_after_line(t, "\tbool sa_optimize = false;",
                      ["int sa_select = 0;   // M1.5: 0=上游随机 1=按 blame 挑节点",
                       "int sa_pick = 0;     // M1.5: 0=上游洗牌取首个 1=按距离排序"],
                      "util.h 成员")
t = insert_after_line(t, '.add_option<bool>("", "--sa_optimize"',
                      ['.add_option<int>("", "--sa_select", "SA: node selection 0=random(upstream) 1=blame-ranked", 0)',
                       '.add_option<int>("", "--sa_pick", "SA: candidate pick 0=shuffle(upstream) 1=distance-ranked", 0)'],
                      "util.h 选项")
t = insert_after_line(t, 'ret.sa_optimize = args.get_option<bool>("--sa_optimize");',
                      ['ret.sa_select = args.get_option<int>("--sa_select");',
                       'ret.sa_pick = args.get_option<int>("--sa_pick");'],
                      "util.h 读取")
util.write_text(t)
print("util.h OK")

# ---------------- header：成员 + 两个辅助函数 ----------------
h = ROOT / "include/morpher/mapper/SimulatedAnnealingMapper.h"
t = h.read_text()

old = '\tbool forceAnneal = false;'
assert t.count(old) == 1, "header: forceAnneal 没找到"
t = t.replace(old, old + '''

	// M1.5: 两个邻域算子的策略。0 = 上游行为（纯随机 / 洗牌取首个），
	// 1 = 有依据的挑选。默认 0，保证 M0/M1 的结论不被悄悄改掉。
	int selectStrategy = 0;
	int pickStrategy = 0;

	// 一个节点"有多该被挪"：所在 PE 上还挤了几个节点 + 已布线边上多绕了几跳。
	// 只用现在就能算的信息，不假装知道布线能不能通（那要 SARoute 才知道）。
	int nodeBlame(DFGNode *node);

	// 一个候选 DP 有多好：到已放置邻居的曼哈顿距离之和 * 2 + 该 PE 的占用数。
	// 便宜的代理指标，用来在 .front() 之前把明显更远的候选排到后面。
	int candidateScore(DataPath *dp, DFGNode *node);''')
h.write_text(t)
print("header OK")

# ---------------- cpp：两个策略 + 两个辅助函数 ----------------
cpp = ROOT / "src/mapper/SimulatedAnnealingMapper.cpp"
t = cpp.read_text()

old = '''	// M1.5: 用成员 rng（可 --sa_seed），不再每次调用都重新播种真随机源
	std::mt19937 &e1 = rng;
	std::uniform_int_distribution<int> uniform_dist(0, sortedNodeList.size() - 1);
	return sortedNodeList[std::abs(std::round(uniform_dist(e1)))];'''
assert t.count(old) == 1, "cpp: selectDFGNodeToUnmap 首段没找到"
new = '''	// M1.5: 用成员 rng（可 --sa_seed），不再每次调用都重新播种真随机源
	std::mt19937 &e1 = rng;
	std::uniform_int_distribution<int> uniform_dist(0, sortedNodeList.size() - 1);

	if (selectStrategy == 1 && !sortedNodeList.empty())
	{
		// 按 blame 从大到小取前 K 名，再在其中随机选一个。
		// 保留随机性是故意的：SA 需要能走"看起来不划算"的移动，
		// 否则邻域退化成贪心，温度这个参数就没意义了。
		const int K = 4;
		std::vector<std::pair<int, DFGNode *>> ranked;
		ranked.reserve(sortedNodeList.size());
		for (DFGNode *n : sortedNodeList)
			ranked.push_back(std::make_pair(nodeBlame(n), n));
		std::stable_sort(ranked.begin(), ranked.end(),
			[](const std::pair<int, DFGNode *> &a, const std::pair<int, DFGNode *> &b) {
				return a.first > b.first;
			});
		int k = (int)ranked.size() < K ? (int)ranked.size() : K;
		std::uniform_int_distribution<int> pick(0, k - 1);
		DFGNode *chosen = ranked[pick(rng)].second;
		LOG(SA) << "selectStrategy=1: top blame=" << ranked[0].first << " pick node " << chosen->idx << "\\n";
		return chosen;
	}

	return sortedNodeList[std::abs(std::round(uniform_dist(e1)))];'''
t = t.replace(old, new)

old = '''	// M1.5: 候选表洗牌也走 rng（上游用 system_clock 当种子，无法复现）
	shuffle(candidateDests.begin(), candidateDests.end(), rng);

	return candidateDests;'''
assert t.count(old) == 1, "cpp: getRandomDPCandidate 尾部没找到"
new = '''	if (pickStrategy == 1 && !candidateDests.empty())
	{
		// 上游是"洗牌后由调用方取 .front()"——等价于随机挑一个候选。
		// 这里改成按 candidateScore 升序；同分之间再打散，
		// 否则永远取同一个方向的 PE，等于把随机性彻底去掉。
		std::vector<std::pair<int, DataPath *>> scored;
		scored.reserve(candidateDests.size());
		for (DataPath *dp : candidateDests)
			scored.push_back(std::make_pair(candidateScore(dp, node), dp));
		std::stable_sort(scored.begin(), scored.end(),
			[](const std::pair<int, DataPath *> &a, const std::pair<int, DataPath *> &b) {
				return a.first < b.first;
			});
		for (size_t i = 0; i < scored.size();)
		{
			size_t j = i;
			while (j + 1 < scored.size() && scored[j + 1].first == scored[i].first)
				j++;
			std::shuffle(scored.begin() + i, scored.begin() + j + 1, rng);
			i = j + 1;
		}
		candidateDests.clear();
		for (auto &p : scored)
			candidateDests.push_back(p.second);
		LOG(SA) << "pickStrategy=1: best candidate score=" << scored.front().first
		        << " for node " << node->idx << "\\n";
	}
	else
	{
		// M1.5: 候选表洗牌也走 rng（上游用 system_clock 当种子，无法复现）
		shuffle(candidateDests.begin(), candidateDests.end(), rng);
	}

	return candidateDests;'''
t = t.replace(old, new)

anchor = 'CGRAXMLCompile::DFGNode *CGRAXMLCompile::SAMapper::selectDFGNodeToUnmap()'
assert t.count(anchor) == 1, "cpp: selectDFGNodeToUnmap 定义没找到"
impl = '''int CGRAXMLCompile::SAMapper::nodeBlame(DFGNode *node)
{
	int blame = 0;

	// 1) 时分复用下的槽位压力：这个节点所在 PE 上还挤了几个别的节点
	auto it = dfg_node_placement.find(node);
	if (it != dfg_node_placement.end() && it->second.first != NULL)
	{
		PE *myPE = it->second.first->getPE();
		int same = 0;
		for (auto &kv : dfg_node_placement)
			if (kv.first != node && kv.second.first != NULL && kv.second.first->getPE() == myPE)
				same++;
		blame += 3 * same;
	}

	// 2) 绕行：与它相连、已经布线的边上多走了几跳（1 跳是相邻，多出来的算绕）
	for (auto &kv : data_routing_path)
	{
		if (kv.first.first != node && kv.first.second != node)
			continue;
		int hops = (int)kv.second.size();
		if (hops > 1)
			blame += hops - 1;
	}

	return blame;
}

int CGRAXMLCompile::SAMapper::candidateScore(DataPath *dp, DFGNode *node)
{
	PE *pe = dp->getPE();
	if (pe == NULL)
		return 1 << 20;

	int dist = 0;
	auto addNeighbour = [&](DFGNode *other) {
		auto it = dfg_node_placement.find(other);
		if (it == dfg_node_placement.end() || it->second.first == NULL)
			return; // 邻居还没放，这一项不参与
		PE *o = it->second.first->getPE();
		dist += std::abs(pe->getPosition_X() - o->getPosition_X())
		      + std::abs(pe->getPosition_Y() - o->getPosition_Y());
	};
	for (DFGNode *p : node->parents)
		addNeighbour(p);
	for (DFGNode *c : node->children)
		addNeighbour(c);

	int occ = 0;
	for (auto &kv : dfg_node_placement)
		if (kv.second.first != NULL && kv.second.first->getPE() == pe)
			occ++;

	return 2 * dist + occ;
}

'''
t = t.replace(anchor, impl + anchor)
cpp.write_text(t)
print("cpp OK")

# ---------------- CGRA_xml_compiler.cpp：接线 ----------------
main_cpp = ROOT / "src/CGRA_xml_compiler.cpp"
t = main_cpp.read_text()
t = insert_after_line(t, "->optimizeAfterFeasible = args.sa_optimize;",
                      ["static_cast<SAMapper *>(mapper)->selectStrategy = args.sa_select;",
                       "static_cast<SAMapper *>(mapper)->pickStrategy = args.sa_pick;"],
                      "main 接线")
main_cpp.write_text(t)
print("main OK")
