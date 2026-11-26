#!/usr/bin/env python3
"""Replace A* with a deterministic path builder when --route-path supplies one.

The ILP router decides (PE, phase) per step; the port sequence inside a PE is
not a search problem once you look at what the baseline emits:

  hop     FU0.DP0.T -> FU0.DP0_T -> <dir>_O -> <opposite>_I -> <opposite>_XBARI -> ...
  reg     FU0.DP0.T -> FU0.DP0_T -> TREG_RO -> TREG_RI -> ...
  arrive  ... -> <dir>_XBARI -> FU0.DP0_<op> -> FU0.DP0.<op>

So each step is a short sequence of name lookups over getNextPorts(). A* keeps
handling every edge that has no path entry.
"""
import os
import re
import sys

ROOT = os.path.expanduser("~/acc-thu-ilp")
F_PF = os.path.join(ROOT, "src/mapper/src/mapper/PathFinderMapper.cpp")

s = open(F_PF, encoding="utf-8", errors="replace").read()
if "BUILDPATH: deterministic replay" in s:
    print("already patched")
    sys.exit(0)

# ---- 1. insert the builder lambda just before routeEdge
anchor = "\t// helper: route one edge for real; returns false and names the edge on failure\n\tauto routeEdge = [&](DFGNode *parent, DFGNode *node) -> bool {"
if s.count(anchor) != 1:
    sys.exit("routeEdge anchor found %d times" % s.count(anchor))

builder = r'''	// BUILDPATH: deterministic replay of an ILP (PE, phase) sequence.
	// Returns true and fills `out` when every step could be walked.
	auto stepByName = [&](LatPort from, const std::string &suffix, int latDelta,
	                     LatPort &to) -> bool {
		std::vector<LatPort> nexts = from.second->getMod()->getNextPorts(from, this);
		for (LatPort cand : nexts)
		{
			if (cand.first != from.first + latDelta)
				continue;
			const std::string &nm = cand.second->getName();
			if (nm.size() < suffix.size())
				continue;
			if (nm.compare(nm.size() - suffix.size(), suffix.size(), suffix) == 0)
			{
				to = cand;
				return true;
			}
		}
		return false;
	};

	auto dirOut = [&](int dx, int dy) -> std::string {
		if (dx > 0) return "EAST_O";
		if (dx < 0) return "WEST_O";
		if (dy > 0) return "SOUTH_O";
		return "NORTH_O";
	};
	auto dirIn = [&](int dx, int dy) -> std::string {
		if (dx > 0) return "WEST_I";
		if (dx < 0) return "EAST_I";
		if (dy > 0) return "NORTH_I";
		return "SOUTH_I";
	};
	auto dirXB = [&](int dx, int dy) -> std::string {
		if (dx > 0) return "WEST_XBARI";
		if (dx < 0) return "EAST_XBARI";
		if (dy > 0) return "NORTH_XBARI";
		return "SOUTH_XBARI";
	};

	auto buildPath = [&](const EdgePath *ep, const std::string &optype, LatPort start,
	                     std::vector<LatPort> &out) -> bool {
		out.clear();
		if (ep == NULL || ep->steps.size() < 2)
			return false;
		LatPort cur = start;
		out.push_back(cur);
		// leave the producer FU: DP0.T -> DP0_T
		LatPort nxt;
		if (!stepByName(cur, "DP0_T", 0, nxt))
			return false;
		cur = nxt;
		out.push_back(cur);

		for (size_t k = 1; k < ep->steps.size(); ++k)
		{
			const std::pair<int, int> &prevPE = ep->steps[k - 1].first;
			const std::pair<int, int> &thisPE = ep->steps[k].first;
			int iim = cgra->get_t_max();
				int dPhase = (ep->steps[k].second - ep->steps[k - 1].second + iim) % iim;
			bool hop = (prevPE != thisPE);
			if (hop)
			{
				int dx = thisPE.first - prevPE.first;
				int dy = thisPE.second - prevPE.second;
				// walk to the output facing the neighbour
				if (!stepByName(cur, dirOut(dx, dy), 0, nxt))
					return false;
				cur = nxt;
				out.push_back(cur);
				// step into the neighbour's input
				if (!stepByName(cur, dirIn(dx, dy), 0, nxt))
					return false;
				cur = nxt;
				out.push_back(cur);
				// through its crossbar
				if (!stepByName(cur, dirXB(dx, dy), 0, nxt))
					return false;
				cur = nxt;
				out.push_back(cur);
			}
			else
			{
				if (dPhase == 0)
					return false;
				// register hop: DP0_T -> TREG_RO -> TREG_RI, once per cycle
				if (cur.second->getName().find("TREG_RI") == std::string::npos)
				{
					if (!stepByName(cur, "TREG_RO", 0, nxt))
						return false;
					cur = nxt;
					out.push_back(cur);
					if (!stepByName(cur, "TREG_RI", 1, nxt))
						return false;
					cur = nxt;
					out.push_back(cur);
				}
				for (int extra = 1; extra < dPhase; ++extra)
				{
					if (!stepByName(cur, "TREG_RO", 0, nxt))
						return false;
					cur = nxt;
					out.push_back(cur);
					if (!stepByName(cur, "TREG_RI", 1, nxt))
						return false;
					cur = nxt;
					out.push_back(cur);
				}
			}
		}
		// arrive at the consumer operand port
		if (!stepByName(cur, "DP0_" + optype, 0, nxt))
			return false;
		cur = nxt;
		out.push_back(cur);
		if (!stepByName(cur, "DP0." + optype, 0, nxt))
			return false;
		cur = nxt;
		out.push_back(cur);
		return true;
	};

'''
s = s.replace(anchor, builder + anchor)

# ---- 2. use the builder before falling back to A*
m = re.search(r'^(\s*)if \(!LeastCostPathAstar\(start, end, node->rootDP, path, cost, parent, mutexPaths, node\)\)$', s, re.M)
if not m:
    sys.exit("A* call line not found")
ind = m.group(1)
block = (
    ind + "// BUILDPATH: deterministic replay when the ILP supplied this edge\n"
    + ind + "{\n"
    + ind + "\tstd::map<std::pair<int, int>, EdgePath>::const_iterator bpIt =\n"
    + ind + "\t\tedgePaths.find(std::make_pair(parent->idx, node->idx));\n"
    + ind + "\tif (bpIt != edgePaths.end())\n"
    + ind + "\t{\n"
    + ind + "\t\tif (!buildPath(&bpIt->second, parent->getOPtype(node), start, path))\n"
    + ind + "\t\t{\n"
    + ind + "\t\t\tstd::cout << \"INJECT: could not build the supplied path for edge \"\n"
    + ind + "\t\t\t          << parent->idx << \" -> \" << node->idx << \"\\n\";\n"
    + ind + "\t\t\treturn false;\n"
    + ind + "\t\t}\n"
    + ind + "\t\tassignPath(parent, node, path);\n"
    + ind + "\t\t++edgesRouted;\n"
    + ind + "\t\treturn true;\n"
    + ind + "\t}\n"
    + ind + "}\n"
)
s = s[: m.start()] + block + s[m.start():]

open(F_PF, "w", encoding="utf-8").write(s)
print("patched buildPath")
