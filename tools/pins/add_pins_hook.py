#!/usr/bin/env python3
"""Add an external placement-pin hook to the Morpher mapper.

The upstream mapper has no way to accept a placement from outside: it decides
both (x, y) and the phase t itself.  To evaluate an ILP/SA placement we need to
hand it over and let PathFinder do the routing.  This script adds:

  --pins <file>       one "idx x y t" per line, t < 0 means "spatial only"
  --pins_mode <n>     0 off / 1 strict(x,y,t) / 2 spatial(x,y) / 3 prefer / 4 repair

Idempotent: re-running is a no-op because each edit is guarded by a marker.
"""
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# tools/pins/add_pins_hook.py -> repo root -> src/mapper
ROOT = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "src", "mapper")
MARK = "PINHOOK"

EDITS = []


def edit(path, old, new, label):
    EDITS.append((path, old, new, label))


# ---------------------------------------------------------------- util.h
edit(
    "include/morpher/util/util.h",
    "\tstd::string arch_name = \"\";\n\tlisa_arguments lisa_arg;\n};",
    "\tstd::string arch_name = \"\";\n"
    "\tlisa_arguments lisa_arg;\n"
    "\t// %s: external placement pins (ILP / SA warm start)\n"
    "\tstd::string pins_file = \"\"; // \"idx x y t\" per line, t<0 = spatial only\n"
    "\tint pins_mode = 0;           // 0 off, 1 strict(x,y,t), 2 spatial(x,y), 3 prefer, 4 repair\n"
    "};" % MARK,
    "arguments struct: pins_file / pins_mode",
)

edit(
    "include/morpher/util/util.h",
    '\t\t.add_option<std::string>("", "--dfg_id", "the dfg id used to generate training data", "none")',
    '\t\t.add_option<std::string>("", "--dfg_id", "the dfg id used to generate training data", "none")\n'
    '\t\t.add_option<std::string>("", "--pins", "placement pins file (idx x y t per line)", "")\n'
    '\t\t.add_option<int>("", "--pins_mode", "0 off,1 strict,2 spatial,3 prefer,4 repair", 0)',
    "option registration",
)

edit(
    "include/morpher/util/util.h",
    "\tlisa_arg.dfg_id = args.get_option<std::string>(\"--dfg_id\");",
    "\tlisa_arg.dfg_id = args.get_option<std::string>(\"--dfg_id\");\n"
    "\tret.pins_file = args.get_option<std::string>(\"--pins\");\n"
    "\tret.pins_mode = args.get_option<int>(\"--pins_mode\");",
    "option readout",
)

# --------------------------------------------------- HeuristicMapper.h
edit(
    "include/morpher/mapper/HeuristicMapper.h",
    "\tbool sanityCheck();",
    "\tbool sanityCheck();\n"
    "\n"
    "\t// ---- %s: external placement pins -------------------------------\n"
    "\tstruct PinEntry\n"
    "\t{\n"
    "\t\tint x;\n"
    "\t\tint y;\n"
    "\t\tint t; // < 0 => spatial only, phase decided by the mapper\n"
    "\t};\n"
    "\tstd::map<int, PinEntry> pins;\n"
    "\tint pinsMode = 0; // 0 off, 1 strict, 2 spatial, 3 prefer, 4 repair\n"
    "\tint pinsTotal = 0;\n"
    "\tint pinsApplied = 0;\n"
    "\tint pinsRejected = 0;\n"
    "\tbool loadPins(const std::string &path);\n"
    "\tbool applyPins(DFGNode *node, std::priority_queue<dest_with_cost> &estimatedRoutes);" % MARK,
    "HeuristicMapper public API",
)

edit(
    "include/morpher/mapper/HeuristicMapper.h",
    "#include <queue>\n#include <assert.h>",
    "#include <queue>\n#include <map>\n#include <vector>\n#include <string>\n#include <assert.h>",
    "includes for map/vector",
)

# --------------------------------------------------- HeuristicMapper.cpp
IMPL = r'''
// ===== %(mark)s: external placement pins ==============================
// The mapper normally owns both placement and scheduling.  When a placement
// comes from outside (an ILP solve, an annealer, an agent) we only want to
// constrain *where* a node may land and let the existing A* routing and the
// existing latency bookkeeping decide the rest.  Filtering the candidate queue
// right after estimateRouting() is the narrowest place to do that: every
// candidate already carries a DataPath, i.e. a concrete (PE, phase) pair, and
// every candidate has already been proven routable from its parents.

bool HeuristicMapper::loadPins(const std::string &path)
{
	std::ifstream f(path.c_str());
	if (!f.is_open())
	{
		std::cout << "PIN: cannot open pins file " << path << "\n";
		return false;
	}
	std::string line;
	while (std::getline(f, line))
	{
		std::size_t hash = line.find('#');
		if (hash != std::string::npos)
			line = line.substr(0, hash);
		std::istringstream iss(line);
		int idx, x, y, t;
		if (!(iss >> idx >> x >> y >> t))
			continue;
		PinEntry e;
		e.x = x;
		e.y = y;
		e.t = t;
		pins[idx] = e;
	}
	pinsTotal = (int)pins.size();
	std::cout << "PIN: loaded " << pinsTotal << " pin(s) from " << path
			  << " (mode " << pinsMode << ")\n";
	return true;
}

bool HeuristicMapper::applyPins(DFGNode *node, std::priority_queue<dest_with_cost> &estimatedRoutes)
{
	if (pinsMode == 0 || pins.empty())
		return true;

	std::map<int, PinEntry>::const_iterator it = pins.find(node->idx);
	if (it == pins.end())
	{
		if (pinsMode == 1)
		{
			std::cout << "PIN: node " << node->idx << " (" << node->op
					  << ") has no pin and pins_mode=strict\n";
			return false;
		}
		return true;
	}

	const PinEntry &p = it->second;
	std::vector<dest_with_cost> all;
	while (!estimatedRoutes.empty())
	{
		all.push_back(estimatedRoutes.top());
		estimatedRoutes.pop();
	}

	std::vector<dest_with_cost> keep;
	for (std::size_t i = 0; i < all.size(); ++i)
	{
		DataPath *dp = all[i].dest;
		if (dp == NULL)
			continue;
		PE *pe = dp->getPE();
		if (pe == NULL)
			continue;
		if (pe->getPosition_X() != p.x || pe->getPosition_Y() != p.y)
			continue;
		// mode 2 is spatial-only: the phase stays the mapper's decision
		if (pinsMode != 2 && p.t >= 0 && pe->get_t() != p.t)
			continue;
		keep.push_back(all[i]);
	}

	if (keep.empty())
	{
		++pinsRejected;
		if (pinsMode == 3 || pinsMode == 4)
		{
			for (std::size_t i = 0; i < all.size(); ++i)
				estimatedRoutes.push(all[i]);
			std::cout << "PIN: node " << node->idx << " (" << node->op << ") pin ("
					  << p.x << "," << p.y << "," << p.t << ") not offered; "
					  << (pinsMode == 3 ? "prefer" : "repair") << " keeps "
					  << all.size() << " candidate(s)\n";
			return true;
		}
		std::cout << "PIN: node " << node->idx << " (" << node->op << ") pin ("
				  << p.x << "," << p.y << "," << p.t << ") not among " << all.size()
				  << " candidate(s); fail\n";
		return false;
	}

	for (std::size_t i = 0; i < keep.size(); ++i)
		estimatedRoutes.push(keep[i]);
	++pinsApplied;
	return true;
}
// ===== end %(mark)s ===================================================

''' % {"mark": MARK}

# --------------------------------------------------- PathFinderMapper.cpp
edit(
    "src/mapper/PathFinderMapper.cpp",
    "\t\t\t\tisEstRouteSucc = estimateRouting(node, estimatedRoutes, &failedNode);\n",
    "\t\t\t\tisEstRouteSucc = estimateRouting(node, estimatedRoutes, &failedNode);\n"
    "\t\t\t\t// %s: honour an externally supplied placement\n"
    "\t\t\t\tif (isEstRouteSucc && !applyPins(node, estimatedRoutes))\n"
    "\t\t\t\t{\n"
    "\t\t\t\t\tfailedNode = NULL;\n"
    "\t\t\t\t\tisEstRouteSucc = false;\n"
    "\t\t\t\t}\n" % MARK,
    "applyPins call in Map()",
)

# --------------------------------------------------- CGRA_xml_compiler.cpp
edit(
    "src/CGRA_xml_compiler.cpp",
    "\tmapper->enableBackTracking = true;\n\tmapper->backTrackLimit = args.backtracklimit;",
    "\tmapper->enableBackTracking = true;\n"
    "\tmapper->backTrackLimit = args.backtracklimit;\n"
    "\t// %s: external placement pins\n"
    "\tmapper->pinsMode = args.pins_mode;\n"
    "\tif (!args.pins_file.empty())\n"
    "\t{\n"
    "\t\tif (!mapper->loadPins(args.pins_file))\n"
    "\t\t{\n"
    "\t\t\tstd::cout << \"PIN: could not load \" << args.pins_file << \"\\n\";\n"
    "\t\t\treturn 1;\n"
    "\t\t}\n"
    "\t}\n"
    "\telse\n"
    "\t{\n"
    "\t\tmapper->pinsMode = 0;\n"
    "\t}" % MARK,
    "pins wiring in main",
)


def main():
    dry = "--dry" in sys.argv
    failed = 0
    for rel, old, new, label in EDITS:
        path = os.path.join(ROOT, rel)
        with io.open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        if new in src or (MARK in src and old not in src):
            print("skip  %-55s %s" % (label, rel))
            continue
        if src.count(old) != 1:
            print("FAIL  %-55s %s (anchor count=%d)" % (label, rel, src.count(old)))
            failed += 1
            continue
        if dry:
            print("would %-55s %s" % (label, rel))
            continue
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(src.replace(old, new, 1))
        print("ok    %-55s %s" % (label, rel))

    # append the implementation into HeuristicMapper.cpp, before the namespace close
    rel = "src/mapper/HeuristicMapper.cpp"
    path = os.path.join(ROOT, rel)
    with io.open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    if MARK in src:
        print("skip  %-55s %s" % ("applyPins implementation", rel))
    else:
        tail = "} /* namespace CGRAXMLCompile */"
        if src.count(tail) != 1:
            print("FAIL  %-55s %s (namespace close count=%d)" % ("applyPins implementation", rel, src.count(tail)))
            failed += 1
        elif dry:
            print("would %-55s %s" % ("applyPins implementation", rel))
        else:
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(src.replace(tail, IMPL + tail, 1))
            print("ok    %-55s %s" % ("applyPins implementation", rel))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
