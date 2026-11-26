#!/usr/bin/env python3
"""Add --route-path: constrain A* to an exact (PE, phase) sequence per DFG edge.

The ILP router (tools/ilp/ilp_router.py) decides which PEs a value traverses and
in which cycle. A* keeps ownership of the intra-PE port choice, which is the part
that depends on crossbar occupancy and conflict ports. Idempotent: every edit is
guarded by a marker.
"""
import os
import re
import sys

ROOT = os.path.expanduser("~/acc-thu-ilp")
M = "src/mapper"
F_UTIL = os.path.join(M, "include/morpher/util/util.h")
F_HH = os.path.join(M, "include/morpher/mapper/HeuristicMapper.h")
F_HM = os.path.join(M, "src/mapper/HeuristicMapper.cpp")
F_PF = os.path.join(M, "src/mapper/PathFinderMapper.cpp")
F_CC = os.path.join(M, "src/CGRA_xml_compiler.cpp")


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8", errors="replace") as f:
        return f.read()


def write(p, s):
    with open(os.path.join(ROOT, p), "w", encoding="utf-8") as f:
        f.write(s)


def sub1(s, anchor, repl, sig, path, regex=False):
    """Replace exactly one occurrence of anchor; idempotent on sig."""
    if sig in s:
        print("  already patched:", sig)
        return s
    if regex:
        m = re.search(anchor, s, re.M)
        if not m:
            sys.exit("ERROR: anchor not found in %s: %s" % (path, anchor))
        return s[: m.start()] + repl + s[m.end():]
    n = s.count(anchor)
    if n != 1:
        sys.exit("ERROR: anchor found %d times in %s: %r" % (n, path, anchor[:70]))
    return s.replace(anchor, repl)


# ---------------------------------------------------------------- util.h
s = read(F_UTIL)
s = sub1(
    s,
    "\tstd::string routes_file = \"\";",
    "\tstd::string routes_file = \"\";\n"
    "\tstd::string route_path_file = \"\"; // ROUTEPATH: exact (PE, phase) sequence per edge",
    "route_path_file", F_UTIL,
)
m = re.search(r'^(\s*)\.add_option<std::string>\("", "--routes".*$', s, re.M)
if m and "--route-path" not in s:
    ins = (
        m.group(0)
        + "\n"
        + m.group(1)
        + '.add_option<std::string>("", "--route-path", "exact per-edge (PE, phase) sequence (see tools/ilp/routes_to_path.py)", "")'
    )
    s = s[: m.start()] + ins + s[m.end():]
    print("  added --route-path option")
elif "--route-path" in s:
    print("  already patched: --route-path")
else:
    sys.exit("ERROR: --routes option line not found")

m = re.search(r'^(\s*)ret\.routes_file = .*$', s, re.M)
if m and "route_path_file = args" not in s:
    ins = (
        m.group(0)
        + "\n"
        + m.group(1)
        + 'ret.route_path_file = args.get_option<std::string>("--route-path");'
    )
    s = s[: m.start()] + ins + s[m.end():]
    print("  read --route-path")
write(F_UTIL, s)

# ------------------------------------------------- HeuristicMapper.h
s = read(F_HH)
if "struct EdgePath" not in s:
    anchor = "\tbool loadRoutes(const std::string &path);"
    repl = (
        anchor
        + "\n\n"
        "\t// ROUTEPATH: the exact (PE, phase) sequence the ILP router chose for one\n"
        "\t// DFG edge. A* still picks the ports inside a PE -- that part depends on\n"
        "\t// crossbar occupancy -- but it may not choose its own PE sequence.\n"
        "\tstruct EdgePath\n"
        "\t{\n"
        "\t\tstd::vector<std::pair<std::pair<int, int>, int> > steps; // ((x,y), phase)\n"
        "\t};\n"
        "\tstd::map<std::pair<int, int>, EdgePath> edgePaths; // (producer, consumer)\n"
        "\tint edgePathsTotal = 0;\n"
        "\tbool loadRoutePaths(const std::string &path);"
    )
    s = sub1(s, anchor, repl, "struct EdgePath", F_HH)
else:
    print("  already patched: struct EdgePath")
write(F_HH, s)

# --------------------------------------- HeuristicMapper.cpp: loader
s = read(F_HM)
if "HeuristicMapper::loadRoutePaths" not in s:
    anchor = "// ===== end PINHOOK"
    if anchor not in s:
        sys.exit("ERROR: PINHOOK end marker missing in HeuristicMapper.cpp")
    impl = """// ROUTEPATH: parse "src dst nsteps x0 y0 t0 x1 y1 t1 ..." per line.
bool HeuristicMapper::loadRoutePaths(const std::string &path)
{
	std::ifstream in(path.c_str());
	if (!in)
	{
		std::cout << "ROUTEPATH: cannot open " << path << "\\n";
		return false;
	}
	std::string line;
	while (std::getline(in, line))
	{
		std::string::size_type h = line.find('#');
		if (h != std::string::npos)
			line = line.substr(0, h);
		std::istringstream ss(line);
		int src, dst, n;
		if (!(ss >> src >> dst >> n) || n <= 0)
			continue;
		EdgePath ep;
		bool ok = true;
		for (int i = 0; i < n; ++i)
		{
			int x, y, t;
			if (!(ss >> x >> y >> t))
			{
				ok = false;
				break;
			}
			ep.steps.push_back(std::make_pair(std::make_pair(x, y), t));
		}
		if (!ok)
		{
			std::cout << "ROUTEPATH: truncated line for edge " << src << " -> " << dst << "\\n";
			return false;
		}
		edgePaths[std::make_pair(src, dst)] = ep;
		++edgePathsTotal;
	}
	std::cout << "ROUTEPATH: loaded " << edgePathsTotal << " edge path(s)\\n";
	return edgePathsTotal > 0;
}

"""
    s = sub1(s, anchor, impl + anchor, "HeuristicMapper::loadRoutePaths", F_HM)
else:
    print("  already patched: loadRoutePaths")
write(F_HM, s)

# ------------------------------------- PathFinderMapper.cpp: constrain
s = read(F_PF)
if "ROUTEPATH: exact (PE, phase) waypoints" not in s:
    anchor = """		if (rgit != guides.end())
			routeGuide = &rgit->second;
	}
"""
    repl = anchor + """
	// ROUTEPATH: exact (PE, phase) waypoints for this edge
	const EdgePath *edgePath = NULL;
	if (!edgePaths.empty())
	{
		std::map<std::pair<int, int>, EdgePath>::const_iterator epit =
			edgePaths.find(std::make_pair(node->idx, currNode->idx));
		if (epit != edgePaths.end())
			edgePath = &epit->second;
	}
	std::map<std::pair<PE *, int>, int> guideIdx;
	if (edgePath != NULL)
	{
		PE *startPE = start.second->findParentPE();
		if (startPE == NULL || start.first % II != edgePath->steps[0].second ||
		    std::make_pair(startPE->getPosition_X(), startPE->getPosition_Y()) !=
		        edgePath->steps[0].first)
		{
			std::cout << "ROUTEPATH: path for edge " << node->idx << " -> " << currNode->idx
			          << " does not start at the producer, ignoring it\\n";
			edgePath = NULL;
		}
		else
			guideIdx[std::make_pair(startPE, start.first)] = 0;
	}
"""
    s = sub1(s, anchor, repl, "ROUTEPATH: exact (PE, phase) waypoints", F_PF)
else:
    print("  already patched: waypoint lookup")

if "ROUTEPATH: follow the ILP sequence" not in s:
    anchor = """					if (curr_hops_to_port[currPort] + 1 > routeGuide->maxHops)
						continue;
				}
			}
"""
    repl = anchor + """
			// ROUTEPATH: follow the ILP sequence hop by hop, phase included
			if (edgePath != NULL)
			{
				PE *rpCurrPE = currPort.second->findParentPE();
				PE *rpNextPE = nextPort->findParentPE();
				std::map<std::pair<PE *, int>, int>::const_iterator rpIt =
					guideIdx.find(std::make_pair(rpCurrPE, currPort.first));
				int rpIdx = (rpIt == guideIdx.end()) ? 0 : rpIt->second;
				bool rpHop = (rpNextPE != NULL && rpCurrPE != NULL && rpNextPE != rpCurrPE);
				bool rpReg = (nextLatPort.first != currPort.first);
				if (rpHop || rpReg)
				{
					int rpNext = rpIdx + 1;
					if (rpNext >= (int)edgePath->steps.size())
						continue;
					if (rpNextPE == NULL ||
					    std::make_pair(rpNextPE->getPosition_X(), rpNextPE->getPosition_Y()) !=
					        edgePath->steps[rpNext].first)
						continue;
					if (nextLatPort.first % II != edgePath->steps[rpNext].second)
						continue;
					guideIdx[std::make_pair(rpNextPE, nextLatPort.first)] = rpNext;
				}
				else if (rpNextPE != NULL)
					guideIdx[std::make_pair(rpNextPE, nextLatPort.first)] = rpIdx;
			}
"""
    s = sub1(s, anchor, repl, "ROUTEPATH: follow the ILP sequence", F_PF)
else:
    print("  already patched: hop constraint")

if "ROUTEPATH: only accept the end once the whole sequence is consumed" not in s:
    anchor = """			if (currPort == end)
			{
				finalPath = *curr.pathVec;
			}"""
    repl = """			if (currPort == end)
			{
				// ROUTEPATH: only accept the end once the whole sequence is consumed
				bool rpDone = true;
				if (edgePath != NULL)
				{
					PE *endPE = end.second->findParentPE();
					std::map<std::pair<PE *, int>, int>::const_iterator rpEnd =
						guideIdx.find(std::make_pair(endPE, end.first));
					int rpEndIdx = (rpEnd == guideIdx.end()) ? -1 : rpEnd->second;
					rpDone = (rpEndIdx == (int)edgePath->steps.size() - 1);
				}
				if (rpDone)
					finalPath = *curr.pathVec;
			}"""
    s = sub1(s, anchor, repl, "ROUTEPATH: only accept the end once the whole sequence is consumed", F_PF)
else:
    print("  already patched: end check")
write(F_PF, s)

# ------------------------------------------ CGRA_xml_compiler.cpp: load
s = read(F_CC)
if "route_path_file" not in s:
    anchor = """			std::cout << "ROUTEGUIDE: could not load " << args.routes_file << "\\n";
			return 1;
		}
	}
"""
    repl = anchor + """
	// ROUTEPATH: exact per-edge (PE, phase) sequences from the ILP router
	if (!args.route_path_file.empty())
	{
		if (!mapper->loadRoutePaths(args.route_path_file))
		{
			std::cout << "ROUTEPATH: could not load " << args.route_path_file << "\\n";
			return 1;
		}
	}
"""
    s = sub1(s, anchor, repl, "route_path_file", F_CC)
else:
    print("  already patched: compiler load")
write(F_CC, s)

print("patched --route-path")
