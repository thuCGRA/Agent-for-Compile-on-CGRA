/*
 * PinnedMapper.cpp
 *
 *  See PinnedMapper.h. The only behavioural change with respect to
 *  PathFinderMapper::Map() is the restrictDestinations() hook, which the base
 *  class calls right after estimateRouting() succeeded.
 */

#include <morpher/mapper/PinnedMapper.h>
#include <morpher/arch/json.hpp>

#include <fstream>
#include <iostream>
#include <vector>

namespace CGRAXMLCompile
{

static std::string strint(int v) { return std::to_string(v); }

static const char *modeName(int m)
{
	return (m == 1) ? "strict" : ((m == 2) ? "repair" : "off");
}

bool PinnedMapper::loadPins()
{
	if (pinsFileName.empty())
	{
		std::cerr << "[pins] no --pins given, running as a plain PathFinder\n";
		return true;
	}

	std::ifstream in(pinsFileName.c_str());
	if (!in.is_open())
	{
		lastRestrictError = "cannot open pins file: " + pinsFileName;
		std::cerr << "[pins] " << lastRestrictError << "\n";
		return false;
	}

	nlohmann::json j;
	try
	{
		in >> j;
	}
	catch (const std::exception &e)
	{
		lastRestrictError = std::string("pins json parse error: ") + e.what();
		std::cerr << "[pins] " << lastRestrictError << "\n";
		return false;
	}

	nlohmann::json::iterator it = j.find("pins");
	if (it == j.end() || !it->is_array())
	{
		lastRestrictError = "pins json has no 'pins' array";
		std::cerr << "[pins] " << lastRestrictError << "\n";
		return false;
	}

	for (nlohmann::json::iterator p = it->begin(); p != it->end(); ++p)
	{
		PlacementPin pin;
		pin.set = true;
		pin.node = p->value("node", -1);
		pin.x = p->value("x", -1);
		pin.y = p->value("y", -1);
		pin.t = p->value("t", -1); // absent t == phase free
		if (pin.node < 0 || pin.x < 0 || pin.y < 0)
		{
			std::cerr << "[pins] skipping malformed pin (need node/x/y >= 0)\n";
			continue;
		}
		if (pins.find(pin.node) != pins.end())
		{
			lastRestrictError = "duplicate pin for node " + strint(pin.node);
			std::cerr << "[pins] " << lastRestrictError << "\n";
			return false;
		}
		pins[pin.node] = pin;
	}

	std::cout << "[pins] loaded " << pins.size() << " pin(s) from " << pinsFileName
			  << " (mode=" << modeName(pinsMode) << ")\n";
	return true;
}

bool PinnedMapper::restrictDestinations(DFGNode *node, std::priority_queue<dest_with_cost> &estimatedRoutes)
{
	if (pinsMode == PINS_OFF)
	{
		return true;
	}

	std::map<int, PlacementPin>::const_iterator it = pins.find((int)node->idx);
	if (it == pins.end())
	{
		if (pinsMode == PINS_STRICT)
		{
			lastRestrictError = "no pin for node " + strint((int)node->idx) + " (op=" + node->op + ")";
			++pinsRejected;
			return false;
		}
		return true; // repair: leave this node to the compiler
	}

	const PlacementPin &pin = it->second;

	// drain the queue built by estimateRouting(), keep what the pin allows
	std::vector<dest_with_cost> all;
	while (!estimatedRoutes.empty())
	{
		all.push_back(estimatedRoutes.top());
		estimatedRoutes.pop();
	}

	int matched = 0;
	int blocked = 0;
	int xmin = 1 << 30, xmax = -1, ymin = 1 << 30, ymax = -1;

	for (size_t i = 0; i < all.size(); ++i)
	{
		dest_with_cost cand = all[i];
		PE *pe = cand.dest->getPE();
		if (pe->X < xmin) xmin = pe->X;
		if (pe->X > xmax) xmax = pe->X;
		if (pe->Y < ymin) ymin = pe->Y;
		if (pe->Y > ymax) ymax = pe->Y;

		bool hit = (pe->X == pin.x) && (pe->Y == pin.y) && (pin.t < 0 || pe->T == pin.t);
		if (!hit)
		{
			continue;
		}

		// Route() asserts that the destination is free, so never hand an
		// occupied datapath over to it.
		if (cand.dest->getMappedNode() != NULL)
		{
			++blocked;
			lastRestrictError = "pinned DP for node " + strint((int)node->idx) + " at (" +
								strint(pin.x) + "," + strint(pin.y) + ",t" +
								(pin.t < 0 ? std::string("*") : strint(pin.t)) +
								") is already occupied by node " +
								strint((int)cand.dest->getMappedNode()->idx);
			continue;
		}

		++matched;
		estimatedRoutes.push(cand);
	}

	if (matched == 0)
	{
		++pinsRejected;
		if (pinsMode == PINS_REPAIR)
		{
			for (size_t i = 0; i < all.size(); ++i)
			{
				estimatedRoutes.push(all[i]);
			}
			std::cerr << "[pins] repair: dropping pin for node " << strint((int)node->idx)
					  << " at (" << strint(pin.x) << "," << strint(pin.y) << ",t"
					  << (pin.t < 0 ? std::string("*") : strint(pin.t))
					  << "), the compiler will choose instead\n";
			return !estimatedRoutes.empty();
		}

		if (blocked == 0)
		{
			// No candidate at all for that (x,y,t). Usually the phase we asked
			// for is not what morpher's own latency model would produce there,
			// or the op cannot sit on that PE type.
			std::string offered;
			int shown = 0;
			for (size_t i = 0; i < all.size() && shown < 12; ++i, ++shown)
			{
				PE *ope = all[i].dest->getPE();
				offered += " (" + strint(ope->X) + "," + strint(ope->Y) + ",t" + strint(ope->T) + ")";
			}
			if ((int)all.size() > shown)
			{
				offered += " ...";
			}
			lastRestrictError = "no candidate DP matches pin (x=" + strint(pin.x) + ",y=" +
								strint(pin.y) + ",t=" +
								(pin.t < 0 ? std::string("*") : strint(pin.t)) + ") for node " +
								strint((int)node->idx) + " (op=" + node->op + "); estimateRouting offered " +
								strint((int)all.size()) + " candidate(s) over x[" + strint(xmin) + ".." +
								strint(xmax) + "] y[" + strint(ymin) + ".." + strint(ymax) + "]:" + offered;
		}
		std::cerr << "[pins] " << lastRestrictError << "\n";
		return false;
	}

	++pinsApplied;
	return true;
}

void PinnedMapper::writeReport(bool ok, CGRA *cgra)
{
	if (reportFileName.empty())
	{
		return;
	}

	nlohmann::json r;
	r["ok"] = ok;
	r["mapping_method"] = "pinned";
	r["pins_mode"] = modeName(pinsMode);
	r["ii"] = cgra ? cgra->get_t_max() : -1;
	r["stage"] = ok ? "done" : ((pinsRejected > 0) ? "placement" : "route");
	r["pins_total"] = (int)pins.size();
	r["pins_applied"] = pinsApplied;
	r["pins_rejected"] = pinsRejected;
	if (!lastRestrictError.empty())
	{
		r["error"] = lastRestrictError;
	}

	nlohmann::json placement = nlohmann::json::array();
	if (ok)
	{
		for (size_t i = 0; i < sortedNodeList.size(); ++i)
		{
			DFGNode *n = sortedNodeList[i];
			if (n->rootDP == NULL)
			{
				continue;
			}
			PE *pe = n->rootDP->getPE();
			nlohmann::json e;
			e["node"] = (int)n->idx;
			e["op"] = n->op;
			e["x"] = pe->X;
			e["y"] = pe->Y;
			e["t"] = pe->T;
			e["lat"] = n->rootDP->getLat();
			placement.push_back(e);
		}
	}
	r["placement"] = placement;

	std::ofstream out(reportFileName.c_str());
	if (!out.is_open())
	{
		std::cerr << "[pins] cannot write report " << reportFileName << "\n";
		return;
	}
	out << r.dump(2) << "\n";
	out.close();
	std::cout << "[pins] report -> " << reportFileName << " (ok=" << (ok ? "true" : "false") << ")\n";
}

bool PinnedMapper::MapWithPins(CGRA *cgra, DFG *dfg)
{
	if (!loadPins())
	{
		writeReport(false, cgra);
		return false;
	}

	bool ok = PathFinderMapper::Map(cgra, dfg);
	writeReport(ok, cgra);
	return ok;
}

} /* namespace CGRAXMLCompile */
