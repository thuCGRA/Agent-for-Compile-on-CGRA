#!/usr/bin/env python3
"""Upgrade `--routes` from an unordered corridor to an ordered waypoint sequence.

Why
---
The first version only checked "is the next PE inside this set". That removes
options but never steers: `estimateRouting` still explores and still gives up on
candidates whose per-edge A* wanders.  The route file already lists the PEs in
path order, so the stronger (and cheaper) guide is "the k-th inter-PE hop must
land on `pes[k+1]`, exactly".

With that constraint the A* over an edge collapses from a search over 63
placements x 4 phases to a mostly linear walk over the given PE sequence, with
only the port choice at each step left open -- and that is precisely the freedom
the mapper needs to satisfy its own port-conflict bookkeeping.

Implementation
--------------
* `RouteGuide::pes` becomes an ordered `std::vector` (it was a `std::set`, which
  threw the order away) plus `pesSet` for the membership test we still want when
  a guide line covers only part of an edge.
* `LeastCostPathAstar` keeps `guide_idx_to_port`: how far along the waypoint
  list a given (lat, port) state has got.  A PE change is only allowed when it
  lands on `pes[idx + 1]`; on arrival the index must be at the end of the list.
"""
import io
import sys

H = "src/mapper/include/morpher/mapper/HeuristicMapper.h"
CPP = "src/mapper/src/mapper/PathFinderMapper.cpp"
HM = "src/mapper/src/mapper/HeuristicMapper.cpp"

H_OLD = """	struct RouteGuide
	{
		std::set<std::pair<int, int> > pes; // PEs the value may traverse
		int maxHops;
	};
"""
H_NEW = """	struct RouteGuide
	{
		// ORDERED: the k-th inter-PE hop must land on pes[k + 1].  A set would
		// lose the order and degrade the guide to a plain corridor.
		std::vector<std::pair<int, int> > pes;
		std::set<std::pair<int, int> > pesSet;
		int maxHops;
	};
"""

# 1) 记录 guide 进度
CPP_MAP_OLD = """	std::unordered_map<LatPort, int, hash_LatPort> curr_hops_to_port;
"""
CPP_MAP_NEW = """	std::unordered_map<LatPort, int, hash_LatPort> curr_hops_to_port;
	// ORDERED: how far along routeGuide->pes each (lat, port) state has travelled
	std::unordered_map<LatPort, int, hash_LatPort> guide_idx_to_port;
"""

# 2) start 的进度 = 0
CPP_START_OLD = """	cost_to_port[start] = 0;
	curr_hops_to_port[start] = 0;
"""
CPP_START_NEW = """	cost_to_port[start] = 0;
	curr_hops_to_port[start] = 0;
	guide_idx_to_port[start] = 0;
"""

# 3) 过滤：必须按序前进
CPP_FILTER_OLD = """			// ROUTEGUIDE: keep the search inside the supplied corridor
			if (routeGuide != NULL)
			{
				PE *guideCurrPE = currPort.second->findParentPE();
				PE *guideNextPE = nextPort->findParentPE();
				if (guideNextPE != NULL && guideCurrPE != guideNextPE)
				{
					if (routeGuide->pes.find(std::make_pair(guideNextPE->getPosition_X(),
					                                        guideNextPE->getPosition_Y())) == routeGuide->pes.end())
						continue;
					if (curr_hops_to_port[currPort] + 1 > routeGuide->maxHops)
						continue;
				}
			}
"""
CPP_FILTER_NEW = """			// ROUTEGUIDE: walk the supplied waypoint sequence in order.
			// A PE change is only legal when it lands on pes[idx + 1]; staying in
			// the current PE (port moves, register hops) is always allowed.
			if (routeGuide != NULL)
			{
				PE *guideCurrPE = currPort.second->findParentPE();
				PE *guideNextPE = nextPort->findParentPE();
				if (guideNextPE != NULL && guideCurrPE != NULL && guideCurrPE != guideNextPE)
				{
					int idx = 0;
					std::unordered_map<LatPort, int, hash_LatPort>::const_iterator git =
						guide_idx_to_port.find(currPort);
					if (git != guide_idx_to_port.end())
						idx = git->second;
					if ((size_t)(idx + 1) >= routeGuide->pes.size())
						continue; // no waypoints left: the value must not leave this PE
					if (routeGuide->pes[idx + 1].first != guideNextPE->getPosition_X() ||
						routeGuide->pes[idx + 1].second != guideNextPE->getPosition_Y())
						continue;
				}
			}
"""

# 4) 新状态写入时同步 index
CPP_IDX_OLD = """						if(nextLatPort.first == currPort.first && nextLatPort.second->getPE() != currPort.second->getPE()){
							//next latport is inter-PE connection and it is not increasing latency
							//therefore it should be a hop
							curr_hops_to_port[nextLatPort] = curr_hops_to_port[currPort] + 1;
						}
"""
CPP_IDX_NEW = """						if(nextLatPort.first == currPort.first && nextLatPort.second->getPE() != currPort.second->getPE()){
							//next latport is inter-PE connection and it is not increasing latency
							//therefore it should be a hop
							curr_hops_to_port[nextLatPort] = curr_hops_to_port[currPort] + 1;
							// ORDERED: one step further along the waypoint list
							guide_idx_to_port[nextLatPort] = guide_idx_to_port[currPort] + 1;
						}
"""

# 5) 到达时检查是否走完整条序列
CPP_ARRIVE_OLD = """		if (currPort == end)
		{
			if(cost_to_port[currPort] < curr_least_cost_to_end){
				curr_least_cost_to_end = cost_to_port[currPort];
			}
			continue;
		}
"""
CPP_ARRIVE_NEW = """		if (currPort == end)
		{
			// ORDERED: only accept an arrival that completed the whole sequence
			if (routeGuide != NULL)
			{
				int idxEnd = -1;
				std::unordered_map<LatPort, int, hash_LatPort>::const_iterator git =
					guide_idx_to_port.find(currPort);
				if (git != guide_idx_to_port.end())
					idxEnd = git->second;
				if ((size_t)(idxEnd + 1) != routeGuide->pes.size())
					continue;
			}
			if(cost_to_port[currPort] < curr_least_cost_to_end){
				curr_least_cost_to_end = cost_to_port[currPort];
			}
			continue;
		}
"""

# 6) loadRoutes 里填 pesSet
LOAD_OLD = """			g.pes.insert(std::make_pair(x, y));
"""
LOAD_NEW = """			g.pes.push_back(std::make_pair(x, y));
			g.pesSet.insert(std::make_pair(x, y));
"""


def main():
    h = io.open(H, encoding="utf-8").read()
    c = io.open(CPP, encoding="utf-8").read()
    hm = io.open(HM, encoding="utf-8").read()
    if "ORDERED" in c:
        print("already patched")
        return 0
    for old, new, name, target in (
        (H_OLD, H_NEW, "RouteGuide", "h"),
        (CPP_MAP_OLD, CPP_MAP_NEW, "map", "c"),
        (CPP_START_OLD, CPP_START_NEW, "start", "c"),
        (CPP_FILTER_OLD, CPP_FILTER_NEW, "filter", "c"),
        (CPP_IDX_OLD, CPP_IDX_NEW, "idx", "c"),
        (CPP_ARRIVE_OLD, CPP_ARRIVE_NEW, "arrive", "c"),
        (LOAD_OLD, LOAD_NEW, "load-regex", "regex"),
    ):
        if target == "regex":
            import re as _re
            pat = _re.compile(r"([ \t]*)g\.pes\.insert\(std::make_pair\(([^)]*)\)\);")
            if len(pat.findall(hm)) != 1:
                print("ERROR: load regex matched %d times" % len(pat.findall(hm)), file=sys.stderr)
                return 1
            hm = pat.sub(lambda m: m.group(1) + "g.pes.push_back(std::make_pair(" + m.group(2) + "));\n"
                        + m.group(1) + "g.pesSet.insert(std::make_pair(" + m.group(2) + "));", hm)
            continue
        src = h if target == "h" else c
        if src.count(old) != 1:
            print("ERROR: %s anchor found %d times" % (name, src.count(old)), file=sys.stderr)
            return 1
        if target == "h":
            h = h.replace(old, new)
        else:
            c = c.replace(old, new)
    io.open(H, "w", encoding="utf-8").write(h)
    io.open(CPP, "w", encoding="utf-8").write(c)
    io.open(HM, "w", encoding="utf-8").write(hm)
    print("patched ordered waypoints")
    return 0


if __name__ == "__main__":
    sys.exit(main())