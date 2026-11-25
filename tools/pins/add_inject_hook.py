#!/usr/bin/env python3
"""Add a `--pins_mode 6` injection mode to the mapper.

Mode 6 = "inject": the whole solution (placement + per-edge routing) comes from an
external solver.  `Map()` skips estimateRouting/Route() entirely -- that greedy pair
is what rejects routable placements -- and instead assigns each node to its pinned
datapath, then routes every edge once with the existing LeastCostPathAstar (which
honours the ordered --routes corridor).  If A* fails on an edge we report that edge
and give up: there is nothing left to search.

Idempotent: every edit carries an INJECT marker and is skipped if already present.
"""
import io
import os
import re
import sys

ROOT = os.path.expanduser("~/acc-thu-ilp")
HDR = os.path.join(ROOT, "src/mapper/include/morpher/mapper/PathFinderMapper.h")
CPP = os.path.join(ROOT, "src/mapper/src/mapper/PathFinderMapper.cpp")

HDR_DECL = """	bool MapInjected(CGRA *cgra, DFG *dfg); // INJECT: replay an external placement + routing
"""

MAP_HOOK = """	// INJECT: mode 6 replays a complete external solution instead of searching
	if (pinsMode == 6)
		return MapInjected(cgra, dfg);

"""

METHOD = r'''
// ===== INJECT: replay an external placement + routing solution ==========
// The external solver (tools/ilp/cegra.py) produces, for every DFG node, a concrete
// (x, y, t) and, for every DFG edge, a PE sequence.  estimateRouting()/Route() are a
// greedy one-node-at-a-time heuristic: they speculatively require every parent edge
// *and* every already-mapped child edge to be routable for a candidate before they
// will even consider it, and they never backtrack.  That is why they reject placements
// which a global model proves routable.  Mode 6 sidesteps the search completely.
bool CGRAXMLCompile::PathFinderMapper::MapInjected(CGRA *cgra, DFG *dfg)
{
	std::string logBase = fNameLog1 + cgra->getCGRAName() + "_MTP=" + std::to_string(enableMutexPaths);
	std::string mapLogName = logBase + "_Iter=0.mapping.csv";
	std::string mapLatName = logBase + "_II=" + std::to_string(cgra->get_t_max()) + "_Iter=0.mappingwithlatency.txt";
	std::ofstream injLog(mapLogName.c_str());
	std::ofstream injLat(mapLatName.c_str());

	const int ii = cgra->get_t_max();
	int edgesRouted = 0;

	// helper: the single DataPath inside a PE
	auto firstDP = [](PE *pe) -> DataPath * {
		if (pe == NULL)
			return NULL;
		for (Module *sm : pe->subModules)
		{
			if (FU *fu = dynamic_cast<FU *>(sm))
			{
				for (Module *sm2 : fu->subModules)
				{
					if (DataPath *d = dynamic_cast<DataPath *>(sm2))
						return d;
				}
			}
		}
		return NULL;
	};

	// Mutex-path exclusion has to persist across edges, exactly as it does inside
	// Route(): a fresh map per edge lets two values share a port in the same cycle,
	// which corrupts the bitstream while keeping the value count right.
	std::map<Port *, std::set<DFGNode *> > mutexPaths;

	// helper: route one edge for real; returns false and names the edge on failure
	auto routeEdge = [&](DFGNode *parent, DFGNode *node) -> bool {
		if (parent->rootDP == NULL || node->rootDP == NULL)
			return true;
		if (parent->getOPtype(node) == "PS")
			return true;
		Port *startPort = parent->rootDP->getOutputDP()->getOutPort("T");
		// Target the consumer's INPUT port. Targeting its output port was tried and made
		// every kernel fail at the first LOAD edge: DP0_P/DP0_T are not connected in
		// the port graph, so A* cannot reach the output side.
		Port *destPort = node->rootDP->getInPort(parent->getOPtype(node));
		if (startPort == NULL || destPort == NULL)
		{
			std::cout << "INJECT: edge " << parent->idx << " -> " << node->idx
					  << " has no port (" << (startPort == NULL ? "src" : "dst") << ")\n";
			return false;
		}
		// A* wants an ABSOLUTE latency, not a phase: the smallest value that is
		// >= the source port latency and congruent to the pinned phase modulo II.
		// Equal is allowed -- HyCUBE routes combinationally through crossbars, so a
		// value can reach a neighbour in the same cycle it is produced.
		LatPort start = std::make_pair(startPort->getLat(), startPort);
		// A recurrence edge (childNextIter=1) carries a value from the PREVIOUS
		// iteration, so it must land one full II later than the consumer's phase.
		int destLat = node->rootDP->getLat() + parent->childNextIter[node] * ii;
		while (destLat < start.first)
			destLat += ii;
		LatPort end = std::make_pair(destLat, destPort);
		std::vector<LatPort> path;
		int cost = 0;
		if (!LeastCostPathAstar(start, end, node->rootDP, path, cost, parent, mutexPaths, node))
		{
			std::cout << "INJECT: routing failed for edge " << parent->idx << " -> " << node->idx
					  << " (" << parent->rootDP->getPE()->getName() << " t=" << parent->rootDP->getLat()
					  << " -> " << node->rootDP->getPE()->getName() << " t=" << node->rootDP->getLat() << ")\n";
			return false;
		}
		assignPath(parent, node, path);
		++edgesRouted;
		return true;
	};

	// pass 1: place every node on the datapath named by its pin
	for (DFGNode &nodeRef : dfg->nodeList)
	{
		DFGNode *node = &nodeRef;
		std::map<int, PinEntry>::const_iterator it = pins.find(node->idx);
		if (it == pins.end())
		{
			std::cout << "INJECT: node " << node->idx << " (" << node->op << ") has no pin\n";
			return false;
		}
		const PinEntry p = it->second;
		if (p.t < 0)
		{
			std::cout << "INJECT: node " << node->idx << " (" << node->op
					  << ") needs a concrete phase, got t=-1\n";
			return false;
		}
		// the JSON arch path fills subModArr, not PEArr: getPE() asserts on PEArr
		PE *pe = NULL;
		for (PE *cand : cgra->getSpatialPEList(p.t))
		{
			if (cand->getPosition_X() == p.x && cand->getPosition_Y() == p.y)
			{
				pe = cand;
				break;
			}
		}
		if (pe == NULL)
		{
			std::cout << "INJECT: node " << node->idx << " (" << node->op << ") pin ("
					  << p.x << "," << p.y << "," << p.t << ") names no PE\n";
			return false;
		}
		DataPath *dp = firstDP(pe);
		if (dp == NULL)
		{
			std::cout << "INJECT: node " << node->idx << " (" << node->op << ") pin ("
					  << p.x << "," << p.y << "," << p.t << ") has no datapath\n";
			return false;
		}
		if (dp->getMappedNode() != NULL)
		{
			std::cout << "INJECT: node " << node->idx << " (" << node->op << ") pin ("
					  << p.x << "," << p.y << "," << p.t << ") already taken by node "
					  << dp->getMappedNode()->idx << "\n";
			return false;
		}
		dp->assignNode(node, p.t, dfg);
		node->rootDP = dp;
		injLat << node->idx << "," << p.x << "," << p.y << "," << p.t << "\n";
		injLog << "current node = " << node->idx << ",op = " << node->op
			   << ",II = " << ii << ",Iter = 0\n";
	}

	// pass 2: route every DFG edge exactly once, in dataflow order.
	// Order matters: a port that is already taken by an earlier edge is only
	// visible through getCongCost(), so routing in XML order lets later edges
	// silently steal a port from an earlier one and corrupt its operand.
	std::vector<std::pair<int, std::pair<DFGNode *, DFGNode *> > > edges;
	std::set<std::pair<int, int> > seen;
	for (DFGNode &nodeRef : dfg->nodeList)
	{
		DFGNode *node = &nodeRef;
		for (DFGNode *parent : node->parents)
		{
			if (!seen.insert(std::make_pair(parent->idx, node->idx)).second)
				continue;
			int key = (parent->rootDP != NULL) ? parent->rootDP->getLat() : 0;
			edges.push_back(std::make_pair(key, std::make_pair(parent, node)));
		}
	}
	// NOTE: sorting by producer latency was tried and made every kernel fail at
	// its first LOAD edge, so the XML order is kept. See docs/09 for the numbers.
	for (std::pair<int, std::pair<DFGNode *, DFGNode *> > &e : edges)
	{
		if (!routeEdge(e.second.first, e.second.second))
		{
			injLog << "Map Failed!.\n";
			return false;
		}
	}

	std::cout << "INJECT: placed " << dfg->nodeList.size() << " node(s), routed "
			  << edgesRouted << " edge(s), II = " << ii << "\n";
	injLog << "INJECT: placed " << dfg->nodeList.size() << " node(s), routed "
		   << edgesRouted << " edge(s)\n";
	return true;
}
// ===== end INJECT =======================================================
'''


def sub_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        print("ERROR: %s matched %d times (expected 1)" % (what, n), file=sys.stderr)
        sys.exit(1)
    return text.replace(old, new, 1)


def main():
    h = io.open(HDR, encoding="utf-8").read()
    c = io.open(CPP, encoding="utf-8").read()

    if "INJECT" in h and "MapInjected" in h:
        print("already patched (header)")
    else:
        anchor = "\tbool Map(CGRA *cgra, DFG *dfg);\n"
        h = sub_once(h, anchor, anchor + HDR_DECL, "header Map decl")

    if "// INJECT: mode 6" in c:
        print("already patched (Map hook)")
    else:
        anchor = "\tthis->cgra = cgra;\n\tthis->dfg = dfg;\n"
        c = sub_once(c, anchor, anchor + "\n" + MAP_HOOK, "Map cgra assignment")

    if "MapInjected(CGRA *cgra, DFG *dfg)\n{" in c:
        print("already patched (method body)")
    else:
        anchor = "void CGRAXMLCompile::PathFinderMapper::assignPath(DFGNode *src, DFGNode *dest,"
        c = sub_once(c, anchor, METHOD.lstrip("\n") + "\n" + anchor, "assignPath decl")

    io.open(HDR, "w", encoding="utf-8").write(h)
    io.open(CPP, "w", encoding="utf-8").write(c)
    print("patched inject mode")


if __name__ == "__main__":
    main()
