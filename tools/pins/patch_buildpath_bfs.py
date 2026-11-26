#!/usr/bin/env python3
"""Replace A* with a deterministic builder driven by the ILP (PE, phase) sequence.

The ILP router owns the two things that matter -- which PE each step is in and
which cycle it happens -- while the port chain inside one PE is not a search
problem once you know the shape:

  * inside one PE at one latency the value can reach any port it is wired to,
    so a depth-limited BFS over the module's own next-ports is exact and cheap;
  * a *hop* is the only move that changes PE (same cycle, 0-1 cost);
  * a *register* is the only move that burns a cycle, and which register
    (TREG vs NR/ER/WR/SR) depends on where the value entered the PE.

A* keeps handling every edge that has no ILP entry, so nothing regresses.
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

anchor = "\t// helper: route one edge for real; returns false and names the edge on failure\n\tauto routeEdge = [&](DFGNode *parent, DFGNode *node) -> bool {"
if s.count(anchor) != 1:
    sys.exit("routeEdge anchor found %d times" % s.count(anchor))

builder = r'''	// BUILDPATH: deterministic replay of an ILP (PE, phase) sequence.
	auto endsWith = [&](const std::string &nm, const std::string &suffix) -> bool {
		return nm.size() >= suffix.size() &&
		       nm.compare(nm.size() - suffix.size(), suffix.size(), suffix) == 0;
	};

	// Shortest chain of same-cycle, same-module moves from `from` to any port
	// whose name ends with `suffix`. Bounded: a PE has ~25 ports.
	auto intraPE = [&](LatPort from, const std::string &suffix, std::vector<LatPort> &out) -> bool {
		out.clear();
		std::map<Port *, Port *> prev;
		std::vector<LatPort> frontier;
		frontier.push_back(from);
		std::set<Port *> seen;
		seen.insert(from.second);
		for (int depth = 0; depth < 6 && !frontier.empty(); ++depth)
		{
			std::vector<LatPort> next;
			for (LatPort p : frontier)
			{
				std::vector<LatPort> cands = p.second->getMod()->getNextPorts(p, this);
				for (LatPort c : cands)
				{
					if (c.first != from.first) // same cycle only
						continue;
					if (c.second->findParentPE() != from.second->findParentPE()) // stay in the PE
						continue;
					if (seen.count(c.second))
						continue;
					seen.insert(c.second);
					prev[c.second] = p.second;
					if (endsWith(c.second->getName(), suffix))
					{
						std::vector<Port *> rev;
						Port *cur = c.second;
						while (cur != from.second)
						{
							rev.push_back(cur);
							cur = prev[cur];
						}
						for (std::vector<Port *>::reverse_iterator it = rev.rbegin();
						     it != rev.rend(); ++it)
							out.push_back(std::make_pair(from.first, *it));
						return true;
					}
					next.push_back(c);
				}
			}
			frontier = next;
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
	auto dirReg = [&](int dx, int dy) -> std::string {
		if (dx > 0) return "WR_RO"; // entered from the east, parks in the west reg
		if (dx < 0) return "ER_RO";
		if (dy > 0) return "NR_RO";
		return "SR_RO";
	};

	auto buildPath = [&](const EdgePath *ep, const std::string &optype, LatPort start,
	                     LatPort end, std::vector<LatPort> &out) -> bool {
		out.clear();
		if (ep == NULL || ep->steps.empty())
			return false;
		// A single step means the value never leaves the PE and never waits:
		// the mapper itself records only the consumer operand port for such an
		// edge (see the one-port blocks in routeInfo.log), so mirror that
		// instead of looking for a path that does not exist.
		if (ep->steps.size() == 1)
		{
			out.push_back(end);
			if (end.second->getName() == optype) // `end` already is the operand port
				return true;
			std::vector<LatPort> cands = end.second->getMod()->getNextPorts(end, this);
			for (LatPort c : cands)
			{
				if (c.first != end.first)
					continue;
				if (c.second->getName() == optype)
				{
					out.push_back(c);
					return true;
				}
			}
			if (getenv("ROUTEPATH_DEBUG"))
			{
				std::cout << "BUILDPATH: single-step end=" << end.second->getFullName()
				          << " optype=" << optype << " cands:";
				std::vector<LatPort> cc = end.second->getMod()->getNextPorts(end, this);
				for (LatPort c : cc)
					std::cout << " " << c.second->getName() << "@" << c.first;
				std::cout << "\n";
			}
			return false;
		}
		std::vector<LatPort> seg;
		LatPort cur = start;
		out.push_back(cur);
		if (!intraPE(cur, "DP0_T", seg)) // leave the producer FU
			return false;
		out.insert(out.end(), seg.begin(), seg.end());
		cur = out.back();

		const int iim = cgra->get_t_max();
		// Which register the value parks in: TREG while it is still at a FU
		// output, otherwise the direction register of the side it entered the
		// current PE from. Crossing a PE resets it, because "entered from" is
		// a property of the PE we are in.
		std::string lastReg;
		int entryDx = 0, entryDy = 0;
		for (size_t k = 1; k < ep->steps.size(); ++k)
		{
			const std::pair<int, int> &prevPE = ep->steps[k - 1].first;
			const std::pair<int, int> &thisPE = ep->steps[k].first;
			int dPhase = (ep->steps[k].second - ep->steps[k - 1].second + iim) % iim;

			if (prevPE != thisPE)
			{
				int dx = thisPE.first - prevPE.first;
				int dy = thisPE.second - prevPE.second;
				if (!intraPE(cur, dirOut(dx, dy), seg)) // walk to the facing output
					return false;
				out.insert(out.end(), seg.begin(), seg.end());
				cur = out.back();
				// step across the wire into the neighbour
				LatPort nxt;
				bool crossed = false;
				std::vector<LatPort> cands = cur.second->getMod()->getNextPorts(cur, this);
				for (LatPort c : cands)
				{
					if (c.first != cur.first)
						continue;
					if (!endsWith(c.second->getName(), dirIn(dx, dy)))
						continue;
					nxt = c;
					crossed = true;
					break;
				}
				if (!crossed)
					return false;
				cur = nxt;
				out.push_back(cur);
				lastReg.clear(); // a new PE: the entry side decides the register
				entryDx = dx;
				entryDy = dy;
			}
			// burn dPhase cycles in a register, if any
			for (int extra = 0; extra < dPhase; ++extra)
			{
				if (endsWith(cur.second->getName(), "DP0_T"))
					lastReg = "TREG";
				else if (lastReg.empty())
					lastReg = dirReg(entryDx, entryDy).substr(0, 2);
				std::string reg = lastReg + "_RO";
				if (!intraPE(cur, reg, seg))
					return false;
				out.insert(out.end(), seg.begin(), seg.end());
				cur = out.back();
				// the read-in side is one cycle later
				LatPort rin;
				bool got = false;
				std::vector<LatPort> cands = cur.second->getMod()->getNextPorts(cur, this);
				for (LatPort c : cands)
				{
					if (c.first != cur.first + 1)
						continue;
					std::string nm = c.second->getName();
					if (endsWith(nm, "_RI"))
					{
						rin = c;
						got = true;
						break;
					}
				}
				if (!got)
					return false;
				cur = rin;
				out.push_back(cur);
			}
		}
		// arrive at the consumer operand port
		if (!intraPE(cur, "DP0_" + optype, seg))
			return false;
		out.insert(out.end(), seg.begin(), seg.end());
		cur = out.back();
		LatPort fin;
		bool got = false;
		std::vector<LatPort> cands = cur.second->getMod()->getNextPorts(cur, this);
		for (LatPort c : cands)
		{
			if (c.first != cur.first)
				continue;
			if (c.second->getName() == optype)
			{
				fin = c;
				got = true;
				break;
			}
		}
		if (!got)
			return false;
		out.push_back(fin);
		return true;
	};

	// A route is only usable if every port on it is free (or already carries
	// this same producer, which is a legal broadcast).  Without this check a
	// conflicting route silently overwrites another value, and the bitstream
	// still comes out -- with wrong data.
	auto pathIsFree = [&](const std::vector<LatPort> &p, DFGNode *prod) -> bool {
		for (LatPort q : p)
		{
			DFGNode *occ = q.second->getNode();
			if (occ != NULL && occ != prod)
			{
				if (getenv("ROUTEPATH_DEBUG"))
					std::cout << "BUILDPATH: port busy " << q.second->getFullName()
					          << " by node " << occ->idx << "\\n";
				return false;
			}
		}
		return true;
	};

'''
s = s.replace(anchor, builder + anchor)

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
    + ind + "\t\tif (!buildPath(&bpIt->second, parent->getOPtype(node), start, end, path) ||\n"
    + ind + "\t\t    !pathIsFree(path, parent))\n"
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
print("patched buildPath (BFS version)")
