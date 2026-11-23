/*
 * PinnedMapper.h
 *
 *  A PathFinderMapper whose per-node candidate queue is constrained by an
 *  external placement (the "pins" JSON produced by our evolution / agent loop).
 *
 *  Why this exists: the evolution branch scores placements with its own static
 *  checker plus a router we wrote. Neither of them is morpher. The only way to
 *  know whether a placement we produced is real is to hand it to morpher and ask
 *  it to route exactly that placement, then simulate.
 *
 *  Design:
 *   - placement decided outside, routing decided inside. The pin only filters or
 *     reorders the queue that estimateRouting() already built, so Route() and
 *     LeastCostPathAstar() run unchanged. If a pinned placement maps, morpher
 *     really did find a route for it -- we did not weaken the router.
 *   - no assert() on the pins path. A bad hint must produce a machine readable
 *     report, because the caller consumes the report to decide what to try next.
 *   - t < 0 means "phase free": only (x, y) is binding and the mapper picks the
 *     phase. Useful when you trust the spatial decision but not the schedule.
 */

#include <morpher/mapper/PathFinderMapper.h>

#include <map>
#include <string>

#ifndef PINNEDMAPPER_H_
#define PINNEDMAPPER_H_

namespace CGRAXMLCompile
{

struct PlacementPin
{
	bool set = false;
	int node = -1;
	int x = -1;
	int y = -1;
	int t = -1; // phase of the PE (PE::T); < 0 means phase free
};

class PinnedMapper : public PathFinderMapper
{
public:
	enum PinsMode
	{
		PINS_OFF = 0,
		PINS_STRICT = 1, // every node must land exactly on its pin
		PINS_REPAIR = 2	 // unhonourable pins are dropped, the rest are kept
	};

	PinnedMapper(std::string fName) : PathFinderMapper(fName)
	{
		mapping_method_name = "Pinned";
	}

	void setPinsFile(std::string f) { pinsFileName = f; }
	void setReportFile(std::string f) { reportFileName = f; }
	void setPinsMode(PinsMode m) { pinsMode = m; }

	bool loadPins();

	// Same search as method 0, but with pinned candidate queues; writes
	// reportFileName on the way out (success or failure).
	bool MapWithPins(CGRA *cgra, DFG *dfg);

	bool restrictDestinations(DFGNode *node, std::priority_queue<dest_with_cost> &estimatedRoutes) override;

private:
	void writeReport(bool ok, CGRA *cgra);

	std::map<int, PlacementPin> pins;
	std::string pinsFileName;
	std::string reportFileName;
	PinsMode pinsMode = PINS_OFF;
	int pinsApplied = 0;
	int pinsRejected = 0;
};

} /* namespace CGRAXMLCompile */

#endif /* PINNEDMAPPER_H_ */
