#!/usr/bin/env python3
"""Add a `--routes` routing-guide hook to the morpher mapper.

Why
---
`tools/ilp/ilp_router.py` can prove that a placement is routable and hand back
concrete paths.  The mapper's own router, however, re-invents the path with a
greedy per-node A* (`PathFinderMapper::estimateRouting` + `LeastCostPathAstar`),
so an externally computed solution is thrown away and the mapper fails on
placements that are in fact routable.

This hook lets the mapper *follow* an external routing instead of guessing: the
guide file names, for every DFG edge, the set of PEs the value is allowed to
traverse and the hop budget.  Inside `LeastCostPathAstar` the search is then
restricted to that corridor.  Everything else -- register accounting, port
occupancy, conflict ports, `assignPath` -- stays untouched, so the resulting
mapping is still a legal HyCUBE mapping that the simulator can execute.

Guide file format (one line per edge, whitespace separated):

    # src dst max_hops n_pes  x0 y0 x1 y1 ...
    11 12 3 3 0 0 1 0 2 0

`tools/ilp/routes_to_guide.py` converts `ilp_router.py`'s `routes.json` into it.

Idempotent: every edit is wrapped in `ROUTEGUIDE` markers and skipped when the
marker is already present.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
M = os.path.join(ROOT, "src", "mapper")

UTIL_H = os.path.join(M, "include/morpher/util/util.h")
HM_H = os.path.join(M, "include/morpher/mapper/HeuristicMapper.h")
HM_CPP = os.path.join(M, "src/mapper/HeuristicMapper.cpp")
PF_CPP = os.path.join(M, "src/mapper/PathFinderMapper.cpp")
XML_CPP = os.path.join(M, "src/CGRA_xml_compiler.cpp")

MARK = "ROUTEGUIDE"


def read(p):
    with open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(s)


def sub_once(path, old, new, tag, sig=None):
    s = read(path)
    sig = sig if sig is not None else tag
    if sig in s:
        print("  skip (already applied): %s :: %s" % (os.path.basename(path), tag))
        return False
    if s.count(old) != 1:
        print("  !! anchor not unique (%d) in %s for %s" % (s.count(old), path, tag))
        sys.exit(1)
    write(path, s.replace(old, new, 1))
    print("  ok: %s :: %s" % (os.path.basename(path), tag))
    return True


def main():
    print("patching morpher mapper for --routes")

    # ---- 1. arguments: --routes -------------------------------------------
    sub_once(
        UTIL_H,
        "\tint pins_mode = 0;           // 0 off, 1 strict(x,y,t), 2 spatial(x,y), 3 prefer, 4 repair\n",
        "\tint pins_mode = 0;           // 0 off, 1 strict(x,y,t), 2 spatial(x,y), 3 prefer, 4 repair\n"
        "\t// ROUTEGUIDE: external routing corridors (see tools/ilp/routes_to_guide.py)\n"
        "\tstd::string routes_file = \"\";\n",
        "decl",
        sig="ROUTEGUIDE: external routing corridors",
    )
    sub_once(
        UTIL_H,
        '\t\t.add_option<int>("", "--pins_mode", "0 off,1 strict,2 spatial,3 prefer,4 repair", 0)\n',
        '\t\t.add_option<int>("", "--pins_mode", "0 off,1 strict,2 spatial,3 prefer,4 repair", 0)\n'
        '\t\t.add_option<std::string>("", "--routes", "routing guide file (src dst max_hops n x y ...)", "")\n',
        "option",
        sig="routing guide file",
    )
    sub_once(
        UTIL_H,
        '\tret.pins_mode = args.get_option<int>("--pins_mode");\n',
        '\tret.pins_mode = args.get_option<int>("--pins_mode");\n'
        '\tret.routes_file = args.get_option<std::string>("--routes");\n',
        "readback",
        sig="ret.routes_file = args.get_option",
    )

    # ---- 2. HeuristicMapper.h: RouteGuide + loader ------------------------
    hm = read(HM_H)
    if "struct RouteGuide" not in hm:
        if "#include <set>" not in hm:
            hm = hm.replace("#include <map>", "#include <map>\n#include <set>", 1)
        anchor = "\tstd::map<int, PinEntry> pins;\n"
        if hm.count(anchor) != 1:
            print("  !! cannot find pins map in HeuristicMapper.h")
            sys.exit(1)
        hm = hm.replace(
            anchor,
            anchor
            + "\n\t// ---- ROUTEGUIDE: externally supplied routing corridors ----------\n"
            "\tstruct RouteGuide\n"
            "\t{\n"
            "\t\tstd::set<std::pair<int, int> > pes; // PEs the value may traverse\n"
            "\t\tint maxHops;\n"
            "\t};\n"
            "\tstd::map<std::pair<int, int>, RouteGuide> guides; // (producer, consumer) -> corridor\n"
            "\tint routesTotal = 0;\n"
            "\tbool loadRoutes(const std::string &path);\n",
            1,
        )
        write(HM_H, hm)
        print("  ok: HeuristicMapper.h :: RouteGuide")
    else:
        print("  skip (already applied): HeuristicMapper.h :: RouteGuide")

    # ---- 3. HeuristicMapper.cpp: loadRoutes -------------------------------
    loader = '''
// ===== ROUTEGUIDE: external routing corridors =========================
bool HeuristicMapper::loadRoutes(const std::string &path)
{
	std::ifstream in(path.c_str());
	if (!in)
	{
		std::cout << "ROUTEGUIDE: cannot open " << path << "\\n";
		return false;
	}
	guides.clear();
	routesTotal = 0;
	std::string line;
	while (std::getline(in, line))
	{
		if (line.empty() || line[0] == '#')
			continue;
		std::istringstream ss(line);
		int src = 0, dst = 0, maxh = 0, n = 0;
		if (!(ss >> src >> dst >> maxh >> n) || n <= 0)
			continue;
		RouteGuide g;
		g.maxHops = maxh;
		for (int i = 0; i < n; ++i)
		{
			int x = 0, y = 0;
			if (!(ss >> x >> y))
				break;
			g.pes.insert(std::make_pair(x, y));
		}
		if (g.pes.empty())
			continue;
		guides[std::make_pair(src, dst)] = g;
		++routesTotal;
	}
	std::cout << "ROUTEGUIDE: loaded " << routesTotal << " edge corridor(s) from " << path << "\\n";
	return routesTotal > 0;
}
// ===== end ROUTEGUIDE =================================================
'''
    hmc = read(HM_CPP)
    if "HeuristicMapper::loadRoutes" not in hmc:
        anchor = "// ===== end PINHOOK ==================================================="
        if hmc.count(anchor) != 1:
            print("  !! cannot find PINHOOK end marker in HeuristicMapper.cpp")
            sys.exit(1)
        hmc = hmc.replace(anchor, loader + "\n" + anchor, 1)
        write(HM_CPP, hmc)
        print("  ok: HeuristicMapper.cpp :: loadRoutes")
    else:
        print("  skip (already applied): HeuristicMapper.cpp :: loadRoutes")

    # ---- 4. PathFinderMapper.cpp: corridor restriction inside A* ----------
    lookup = '''	// ROUTEGUIDE: pick the corridor for edge (producer -> consumer)
	const RouteGuide *routeGuide = NULL;
	{
		std::map<std::pair<int, int>, RouteGuide>::const_iterator rgit =
			guides.find(std::make_pair(node->idx, currNode->idx));
		if (rgit != guides.end())
			routeGuide = &rgit->second;
	}

'''
    sub_once(
        PF_CPP,
        "\tstd::priority_queue<port_heuristic> q;\n",
        lookup + "\tstd::priority_queue<port_heuristic> q;\n",
        "lookup",
        sig="const RouteGuide *routeGuide",
    )
    filt = '''			// ROUTEGUIDE: keep the search inside the supplied corridor
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

'''
    sub_once(
        PF_CPP,
        "\t\t\tPort *nextPort = nextLatPort.second;\n",
        "\t\t\tPort *nextPort = nextLatPort.second;\n\n" + filt,
        "filter",
        sig="keep the search inside the supplied corridor",
    )

    # ---- 5. CGRA_xml_compiler.cpp: load the guide ------------------------
    sub_once(
        XML_CPP,
        "\t//cout << \"json_file_name = \" << json_file_name << \"\\n\";\n",
        "\t// ROUTEGUIDE: external routing corridors\n"
        "\tif (!args.routes_file.empty())\n"
        "\t{\n"
        "\t\tif (!mapper->loadRoutes(args.routes_file))\n"
        "\t\t{\n"
        "\t\t\tstd::cout << \"ROUTEGUIDE: could not load \" << args.routes_file << \"\\n\";\n"
        "\t\t\treturn 1;\n"
        "\t\t}\n"
        "\t}\n\n"
        "\t//cout << \"json_file_name = \" << json_file_name << \"\\n\";\n",
        "load",
        sig="ROUTEGUIDE: external routing corridors",
    )

    print("done")


if __name__ == "__main__":
    main()
