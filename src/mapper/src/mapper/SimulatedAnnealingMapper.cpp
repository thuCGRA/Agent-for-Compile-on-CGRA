#include <morpher/mapper/PathFinderMapper.h>

#include <morpher/mapper/SimulatedAnnealingMapper.h>
#include <queue>
#include <assert.h>
#include <math.h>
#include <algorithm> // std::reverse
#include <morpher/arch/DataPath.h>
#include <morpher/arch/FU.h>

#include <stack>
#include <functional>
#include <set>
#include <iostream>
#include <sstream>
#include <unordered_set>
#include <unordered_map>
#include <memory>
#include <bitset>

namespace CGRAXMLCompile
{

} /* namespace CGRAXMLCompile */



bool CGRAXMLCompile::SAMapper::SAMap(CGRA *cgra, DFG *dfg)
{
	std::stack<DFGNode *> mappedNodes;
	std::stack<DFGNode *> unmappedNodes;
	std::map<DFGNode *, std::priority_queue<dest_with_cost>> estimatedRouteInfo;

	// Disable mutex paths to test pathfinder
	this->enableMutexPaths = true;

	check_parent_violation = false;

	this->cgra = cgra;
	this->dfg = dfg;

	Check_DFG_CGRA_Compatibility();

	if (cgra->is_spm_modelled)
	{
		UpdateVariableBaseAddr();
	}
	sortBackEdgePriorityASAP();

	std::string mappingLogFileName = fNameLog1 + cgra->getCGRAName() + "_MTP=" + std::to_string(enableMutexPaths);	// + ".mapping.csv";
	std::string mappingLog2FileName = fNameLog1 + cgra->getCGRAName() + "_MTP=" + std::to_string(enableMutexPaths); // + ".routeInfo.log";

	bool mapSuccess = false;

	std::string congestionInfoFileName = mappingLogFileName + ".congestion.info";
	LOG(SA) << "Opening congestion file : " << congestionInfoFileName << "!\n";
	congestionInfoFile.open(congestionInfoFileName.c_str());
	assert(congestionInfoFile.is_open());

	std::string mappingLogFileName_withIter = mappingLogFileName + "_SA" + ".mapping.csv";
	std::string mappingLog2FileName_withIter = mappingLog2FileName + "_SA" + ".routeInfo.log";
	std::string mappingLog4FileName_withIter = mappingLogFileName + "_II=" + std::to_string(cgra->get_t_max()) + "_SA" + ".mappingwithlatency.txt";

	mappingLog.open(mappingLogFileName_withIter.c_str());
	mappingLog2.open(mappingLog2FileName_withIter.c_str());
	mappingLog4.open(mappingLog4FileName_withIter.c_str());

	LOG(SA) << "Opening mapping csv file : " << mappingLogFileName_withIter << "\n";
	LOG(SA) << "Opening routeInfo log file : " << mappingLog2FileName_withIter << "\n";

	assert(mappingLog.is_open());
	assert(mappingLog2.is_open());
	assert(mappingLog4.is_open());

	while (!mappedNodes.empty())
	{
		mappedNodes.pop();
	}
	while (!unmappedNodes.empty())
	{
		unmappedNodes.pop();
	}

	for (DFGNode *node : sortedNodeList)
	{
		unmappedNodes.push(node);
	}

	LOG(SA_) << "*******************************************************SA MAP begin***************************\n";

	data_routing_path.clear();
	dfg_node_placement.clear();
	//initial mapping
	if (!initMap())
	{
		assert(false && "how come?");
		std::cout << "cannot find an initial mapping, exit....\n";
		return false;
	}

	//get mapping information
	
	std::stringstream congestion_detail;
	int unmapped_node_numer = getNumberOfUnmappedNodes();
	int overuse_number = getCongestionNumber(congestion_detail);
	int conflict_number =  getConflictNumber(congestion_detail);
	LOG(SA_) << "Initial mapping done. unmapped node:" << unmapped_node_numer << " overuse:" << overuse_number<< " conflict:" << conflict_number<<" cost:"<<getCost() << " \n";
	LOG(SA_)<<"unmapped node: \n";
	for (auto node : sortedNodeList)
	{
		if (dfg_node_placement.find(node) == dfg_node_placement.end())
		{
			LOG(SA_)<<"\t"<<node->idx<<"  "<<node->op<<"\n";
		}
	}

	LOG(ROUTE)<<congestion_detail.str();


	
	if (unmapped_node_numer == 0 && overuse_number == 0)
	{
		LOG(SA_) << "find a valid initial mapping, exit....II =" << this->cgra->get_t_max() << "\n";
		if (!forceAnneal)
		{
			dumpSAMapping();
			return true;
		}
		LOG(SA_) << "forceAnneal: warm start is valid but annealing anyway\n";
	}
	// 热启动本身就可能可行，先记下来；即使后面退火把解弄坏，也有东西可回滚。
	considerBest();


	//start Simulated Annealing mapping
	LOG(SA_) << "maximum temperature:" << maximum_temp << " minimum temperature:" << minimim_temp << "\n";
	curr_cost = getCost();
	curr_temp = maximum_temp;

	LOG(SA_)<<"###############current mapping: \n"<<dumpMappingToStr();

	while (curr_temp > minimim_temp && !budgetExhausted)
	{
		LOG(SA_) << "*******************************current temperature:" << curr_temp << "\n";
		float accept_rate = inner_map();

		congestion_detail.clear();
		LOG(SA_) << "accept_rate:" << accept_rate << " # of overuse:" << getCongestionNumber(congestion_detail)<< " # of conflict:" << getConflictNumber(congestion_detail)
			<< " unmapped nodes:" << getNumberOfUnmappedNodes()<<" cost:"<<curr_cost << "\n";
		LOG(SA_)<<"unmapped node: \n";
		for (auto node : sortedNodeList)
		{
			if (dfg_node_placement.find(node) == dfg_node_placement.end())
			{
				LOG(SA_)<<"\t"<<node->idx<<"  "<<node->op<<"\n";
			}
		}
		LOG(ROUTE)<<congestion_detail.str();
		LOG(SA_)<<"###############current mapping: \n"<<dumpMappingToStr();

		curr_temp = updateTemperature(curr_temp, accept_rate);

		// 第三处早退：可行即跳出温度循环。--sa_optimize 必须关掉它，
		// 否则退火只跑完"变可行"的那一个温度，等于没退火。
		if (!optimizeAfterFeasible && isCurrMappingValid())
		{
			LOG(SA_) << "find a valid mapping, exit...II =" << this->cgra->get_t_max() << "\n";
			break;
		}
	}

	bool ok = false;
	if (haveBest)
	{
		restoreBestSnapshot();
		LOG(SA_) << "restore best snapshot, cost=" << bestCost << "\n";
		ok = isCurrMappingValid();
	}
	else
	{
		ok = isCurrMappingValid();
	}
	if (ok)
	{
		dumpSAMapping();
	}
	else
	{
		mappingLog << "Map Failed!.\n";
		mappingLog.close();
		mappingLog2.close();
		mappingLog4.close();
	}
	return ok;
}

// 上游 SA 从不写放置产物：printMappingLog/printMappingLog2 只在
// HeuristicMapper::Map() 的成功路径调用，而 SA 走的是 SAMap()。
// 这里补上同样的收尾，另外写 mappingwithlatency.txt（第 4 列是绝对时刻，
// routeInfo.log 里的 -T 只是相位，两者 %II 后等价）。
// 只在可行时记录快照，且只保留代价最小的那个。
// getCost() = unmapped*100 + congestion*10 + conflict*10 + port_usage
void CGRAXMLCompile::SAMapper::considerBest()
{
	if (!isCurrMappingValid())
	{
		return;
	}
	int c = getCost();
	if (haveBest && c >= bestCost)
	{
		return;
	}
	bestCost = c;
	best_placement = dfg_node_placement;
	best_routing = data_routing_path;
	haveBest = true;
	LOG(SA_) << "new best cost=" << bestCost << " at move " << movesDone << "\n";
}

bool CGRAXMLCompile::SAMapper::restoreBestSnapshot()
{
	if (!haveBest)
	{
		return false;
	}
	for (DFGNode *node : sortedNodeList)
	{
		clearNodeMapping(node);
	}
	// 先全部放置，再全部布线：assignPath 需要两端 rootDP 都已就位。
	for (DFGNode *node : sortedNodeList)
	{
		auto it = best_placement.find(node);
		if (it == best_placement.end())
		{
			continue;
		}
		it->second.first->assignNode(node, it->second.second, this->dfg);
		node->rootDP = it->second.first;
	}
	for (auto &kv : best_routing)
	{
		assignPath(kv.first.first, kv.first.second, kv.second);
	}
	dfg_node_placement = best_placement;
	data_routing_path = best_routing;
	return true;
}

void CGRAXMLCompile::SAMapper::dumpSAMapping()
{
	mappingLog << "Map Success!.\n";
	mappingLog2 << "Map Success!.\n";
	this->printMappingLog();
	this->printMappingLog2();
	for (DFGNode *node : sortedNodeList)
	{
		if (node->rootDP == NULL)
		{
			continue;
		}
		mappingLog4 << node->idx << "," << node->rootDP->getPE()->X << ","
					<< node->rootDP->getPE()->Y << "," << node->rootDP->getLat() << "\n";
	}
	mappingLog.close();
	mappingLog2.close();
	mappingLog4.close();
}

bool CGRAXMLCompile::SAMapper::initMap()
{

	std::stack<DFGNode *> mappedNodes;
	std::stack<DFGNode *> unmappedNodes;
	std::map<DFGNode *, std::priority_queue<dest_with_cost>> estimatedRouteInfo;
	enableBackTracking = false;
	int backTrackLimit = 4;
	int backTrackCredits = 4;
	while (!mappedNodes.empty())
	{
		mappedNodes.pop();
	}
	while (!unmappedNodes.empty())
	{
		unmappedNodes.pop();
	}

	for (DFGNode *node : sortedNodeList)
	{
		unmappedNodes.push(node);
	}

	while (!unmappedNodes.empty())
	{

		DFGNode *node = unmappedNodes.top();
		unmappedNodes.pop();

		std::stringstream MapHeader;
		MapHeader << "current node = " << node->idx;
		MapHeader << ",op = " << node->op;
		MapHeader << ",unmapped nodes = " << unmappedNodes.size();
		MapHeader << ",mapped nodes = " << mappedNodes.size();
		MapHeader << ",freeMemNodes = " << cgra->freeMemNodes;
		MapHeader << ",unmappedMemNodes = " << dfg->unmappedMemOps;
		MapHeader << ",II = " << cgra->get_t_max();
		MapHeader << ",btCredits = " << backTrackCredits;

		// MapHeader << ",PEType = " << this->cgra->peType;
		// MapHeader << ",XDim = " << this->cgra->get_x_max();
		// MapHeader << ",YDim = " << this->cgra->get_y_max();
		// MapHeader << ",DPs = " << this->cgra->numberofDPs;

		MapHeader << ",CGRA=" << this->cgra->getCGRAName();
		MapHeader << ",MaxHops=" << this->cgra->max_hops;

		MapHeader << ",BB = " << node->BB;
		MapHeader << ",mutexPathEn = " << this->enableMutexPaths;
		MapHeader << "\n";

		LOG(SA_) << MapHeader.str();


		bool isEstRouteSucc = false;

		// fill the routing information
		if (estimatedRouteInfo.find(node) == estimatedRouteInfo.end())
		{
			// the routes are not estimated.

			DFGNode *failedNode;
			std::priority_queue<dest_with_cost> estimatedRoutes;
			isEstRouteSucc = estimateRouting(node, estimatedRoutes, &failedNode);
			if (isEstRouteSucc)
			{
				estimatedRouteInfo[node] = estimatedRoutes;
			}
		}

		bool isRouteSucc = false;
		DFGNode *failedNode = NULL;

		LOG(SA_)<< "estimatedRouteInfo[node].size = " << estimatedRouteInfo[node].size() << "\n";
		mappingLog << "estimatedRouteInfo[node].size = " << estimatedRouteInfo[node].size() << "\n";
		if (!estimatedRouteInfo[node].empty())
		{
			isRouteSucc = Route(node, estimatedRouteInfo[node], &failedNode);
			if (!isRouteSucc)
			{
				LOG(ROUTE) << "route not successful!\n";
			}
			// else if(!estimatedRouteInfo[node].empty()){
			// 	//route not successful
			// }
		}
		else
		{
			isRouteSucc = false;
		}

		if (!isRouteSucc)
		{
			std::cout << "----------node" << node->idx << " not mapped in initial mapping\n";
			node->SAClear(this->dfg);
		}
	}

	return true;
}

float CGRAXMLCompile::SAMapper::inner_map()
{
	int accepted_number = 0;

	for (int i = 0; i < movement_in_each_temp; i++)
	{
		if (moveBudget > 0 && movesDone >= moveBudget)
		{
			budgetExhausted = true;
			break;
		}
		movesDone++;
		std::stringstream congestion_detail;
		LOG(SA) << "************ NO." << i << " movement, unmapped nodes:" << getNumberOfUnmappedNodes() << ", congestion:" << getCongestionNumber(congestion_detail) 
			<< ", congestion:" << getConflictNumber(congestion_detail);
		LOG(ROUTE)<<congestion_detail.str();

		// select DFG node to unmap. If this node is not mapped yet, will select a parent to unmap as well.
		std::vector<DFGNode *> moved_nodes;
		auto selected_dfg_node = selectDFGNodeToUnmap();
		DFGNode *child_node = NULL;
		if (dfg_node_placement.find(selected_dfg_node) == dfg_node_placement.end())
		{
			
			// this node is not placed yet because of routing failure.
			// The reason for routing failure is that it cannot route to its parent nodes or recurrent dependency constraint;
			// child_node = selected_dfg_node;
			// selected_dfg_node = selectAParentForDFGNode(child_node);
			// LOG(SA) << "----------be careful. Have a child node " << child_node->idx << "\n";
		}
		// assert(dfg_node_placement.find(selected_dfg_node)!= dfg_node_placement.end());
		std::map<DFGNode *, std::pair<DataPath *, int>> old_dfg_node_placement;
		old_dfg_node_placement.insert(dfg_node_placement.begin(), dfg_node_placement.end());
		std::map<dfg_data, std::vector<LatPort>> old_data_routing_path;
		old_data_routing_path.insert(data_routing_path.begin(), data_routing_path.end());

		LOG(SA)<<"select DFG node "<< selected_dfg_node->idx <<" to unmap";
		// start map
		if (dfg_node_placement.find(selected_dfg_node) != dfg_node_placement.end()){
			LOG(SA)<<"current placement:" << selected_dfg_node->rootDP->getFullName();
			clearNodeMapping(selected_dfg_node);
		}else{
			LOG(SA)<<"this DFG node is not placed yet" ;
		}
		
		// M1: 约束（--constraints）过滤之后候选表可能为空。上游在这里直接 .front()，
		// 空表就是未定义行为——实测 SIGSEGV，gdb 栈：inner_map:417 -> SAMap:145 -> main:179。
		// 空表只说明"这个节点此刻没有合法位置"，那是一次失败的移动，不是崩溃的理由：
		// 回滚已清掉的节点、恢复快照、继续下一次移动。
		std::vector<DataPath *> dp_candidates = getRandomDPCandidate(selected_dfg_node);
		if (dp_candidates.empty())
		{
			LOG(SA) << "no legal candidate for node " << selected_dfg_node->idx
			        << " (constraints filtered everything), skip this move\n";
			moved_nodes.push_back(selected_dfg_node);
			for (auto node : moved_nodes)
			{
				clearNodeMapping(node);
				restoreMapping(node, old_dfg_node_placement, old_data_routing_path);
			}
			dfg_node_placement.clear();
			dfg_node_placement.insert(old_dfg_node_placement.begin(), old_dfg_node_placement.end());
			data_routing_path.clear();
			data_routing_path.insert(old_data_routing_path.begin(), old_data_routing_path.end());
			continue;
		}
		auto dp_candidate = dp_candidates.front();
		LOG(SA) << "map this DFG node:" << selected_dfg_node->idx << " op:" << selected_dfg_node->op << "to pe:" << dp_candidate->getPE()->getName() ;
		bool route_succ = SARoute(selected_dfg_node, dp_candidate);
		moved_nodes.push_back(selected_dfg_node);

		// map the unmapped parents of this child node
		if (route_succ && child_node != NULL && false)
		{
			// or skip this

			// find the unmapped nodes
			std::vector<DFGNode *> unmappedParents;
			for (auto parent : child_node->parents)
			{
				if (dfg_node_placement.find(parent) == dfg_node_placement.end())
				{
					unmappedParents.push_back(parent);
				}
			}

			for (auto parent : unmappedParents)
			{
				moved_nodes.push_back(parent);
				clearNodeMapping(parent);
				std::vector<DataPath *> parent_candidates = getRandomDPCandidate(parent);
				if (parent_candidates.empty())
				{
					// 同样的空表保护（这段当前是死代码，但保留正确的写法）
					route_succ = false;
					break;
				}
				auto dp_candidate = parent_candidates.front();
				LOG(SA) << "route other parent node = " << parent->idx << " op:" << parent->op << " current_pe:" << dp_candidate->getPE()->getName() << "\n";
				if (!SARoute(parent, dp_candidate))
				{
					route_succ = false;
					break;
				}
			}
		}

		// process the child node
		if (route_succ && child_node != NULL)
		{
			LOG(SA) << "----------be careful. Processing the child node = " << child_node->idx << "\n";
			std::priority_queue<dest_with_cost> estimatedRoutes;
			DFGNode *failedNode;
			bool isEstRouteSucc = estimateRouting(child_node, estimatedRoutes, &failedNode);
			bool isRouteSucc = false;
			if (isEstRouteSucc)
			{
				isRouteSucc = Route(child_node, estimatedRoutes, &failedNode);
			}
			if (!isRouteSucc)
			{
				clearNodeMapping(child_node);
			}
			moved_nodes.push_back(child_node);
		}

		// decide accept or node
		bool accept = false;
		int attempted_cost = getCost();
		if (route_succ && child_node != NULL && dfg_node_placement.find(child_node) != dfg_node_placement.end())
		{
			// if route success, has a child_node and successfully route
			accept = true;
		}
		else if (route_succ)
		{
			// if not route success, then nothing changes and should restore mapping
			accept = whetherAcceptNewMapping(attempted_cost, curr_cost, curr_temp);
		}

		LOG(SA) << "accept " << (accept? "ture" : "false") << " route_succ:" << route_succ << " curr_cost:" << curr_cost << " attepmted cost:" << attempted_cost << "\n";

		if (accept)
		{
			// update overuse
			//  save the mapping state
			curr_cost = attempted_cost;
			accepted_number++;

			old_dfg_node_placement.clear();
			old_data_routing_path.clear();

			considerBest();
			if (!optimizeAfterFeasible && isCurrMappingValid())
			{
				std::cout << "find a valid mapping, exit inner_map....\n";
				return (float(accepted_number)) / movement_in_each_temp;
			}
		}
		else
		{
			for (auto node : moved_nodes)
			{
				clearNodeMapping(node);
				restoreMapping(node, old_dfg_node_placement, old_data_routing_path);
			}

			dfg_node_placement.clear();
			dfg_node_placement.insert(old_dfg_node_placement.begin(), old_dfg_node_placement.end());
			data_routing_path.clear();
			data_routing_path.insert(old_data_routing_path.begin(), old_data_routing_path.end());
			old_dfg_node_placement.clear();
			old_data_routing_path.clear();
		}
	}
	return (float(accepted_number)) / movement_in_each_temp;
}

int CGRAXMLCompile::SAMapper::nodeBlame(DFGNode *node)
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

CGRAXMLCompile::DFGNode *CGRAXMLCompile::SAMapper::selectDFGNodeToUnmap()
{

	// M1.5: 用成员 rng（可 --sa_seed），不再每次调用都重新播种真随机源
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
		LOG(SA) << "selectStrategy=1: top blame=" << ranked[0].first << " pick node " << chosen->idx << "\n";
		return chosen;
	}

	return sortedNodeList[std::abs(std::round(uniform_dist(e1)))];

	// select random node;

	std::vector<DFGNode *> unmapped_node;
	for (auto node : sortedNodeList)
	{
		if (dfg_node_placement.find(node) == dfg_node_placement.end())
		{
			unmapped_node.push_back(node);
		}
	}
	

	if (unmapped_node.size() == 0)
	{
		std::uniform_int_distribution<int> uniform_dist(0, sortedNodeList.size() - 1);
		return sortedNodeList[std::abs(std::round(uniform_dist(e1)))];
	}
	else
	{
		std::uniform_int_distribution<int> uniform_dist(0, unmapped_node.size() - 1);
		return unmapped_node[std::abs(std::round(uniform_dist(e1)))];
	}
}

CGRAXMLCompile::DFGNode *CGRAXMLCompile::SAMapper::selectAParentForDFGNode(DFGNode *target_node)
{
	// the parent node include: nodes has data dependency, or ones which has recurrent dependency (load/Store constraint)
	std::vector<DFGNode *> candidates;
	candidates.insert(candidates.end(), target_node->parents.begin(), target_node->parents.end());
	for (auto node : sortedNodeList)
	{
		auto rec_parent = node->recParents;
		// as the rec_parent is reversed in DFG, the rec_parent means rec_child actually.
		if (std::find(rec_parent.begin(), rec_parent.end(), target_node) != rec_parent.end())
		{
			candidates.push_back(node);
		}
	}
	assert(candidates.size() > 0);
	std::mt19937 &e1 = rng;
	std::uniform_int_distribution<int> uniform_dist(0, candidates.size() - 1);
	return candidates[std::abs(std::round(uniform_dist(e1)))];
}

bool CGRAXMLCompile::SAMapper::clearNodeMapping(DFGNode *node)
{
	LOG(SA) << "clear node =" << node->idx;
	bool mapped = (dfg_node_placement.find(node) != dfg_node_placement.end());
	if (mapped)
	{
		dfg_node_placement.erase(dfg_node_placement.find(node));
	}
	bool routed = false;
	for (auto it = data_routing_path.begin(); it != data_routing_path.end();)
	{
		// LOG(SA)<<"routing path:" << it->first.first->idx<<" -> "<<it->first.second->idx;
		if (it->first.first == node || it->first.second == node)
		{
			routed = true;
			it = data_routing_path.erase(it);
		}
		else
		{
			it++;
		}
	}
	// assert(mapped ==  routed);

	node->SAClear(this->dfg);
	return true;
}

bool CGRAXMLCompile::SAMapper::restoreMapping(DFGNode *node, std::map<DFGNode *, std::pair<DataPath *, int>> &dfg_node_placement,
											  std::map<dfg_data, std::vector<LatPort>> &data_routing_path)
{
	// assign placement
	if (dfg_node_placement.find(node) != dfg_node_placement.end())
	{
		auto placement_info = dfg_node_placement[node];
		placement_info.first->assignNode(node, placement_info.second, this->dfg);
		node->rootDP = placement_info.first;
	}

	// assign routing
	for (auto &data_path : data_routing_path)
	{
		if (data_path.first.first == node || data_path.first.second == node)
		{
			assignPath(data_path.first.first, data_path.first.second, data_path.second);
		}
	}

	return true;
}

int CGRAXMLCompile::SAMapper::getCongestionNumber(std::stringstream & congestion_detail)
{
	int congestion_number = 0;

	std::stringstream congestion_ss;

	for (std::pair<Port *, std::set<DFGNode *>> pair : congestedPorts)
	{
		Port *p = pair.first;
		if (pair.second.size() > 1)
		{
			for (DFGNode *node1 : pair.second)
			{
				for (DFGNode *node2 : pair.second)
				{
					if (node1 == node2)
					{
						continue;
					}
					if (this->dfg->isMutexNodes(node1, node2, p))
						continue;
					congestion_detail << "CONGESTION:" << p->getFullName();
					for (DFGNode *node : pair.second)
					{
						congestion_detail << "\t ," << node->idx << "|BB=" << node->BB;
					}
					congestion_detail << "\n";
					congestion_number++;
					//					break;
				}
			}
		}
	}
	return congestion_number;
}

int CGRAXMLCompile::SAMapper::getConflictNumber(std::stringstream & congestion_detail)
{
	int conflict_number = 0;

	for (std::pair<Port *, std::set<DFGNode *>> pair : conflictedPorts)
	{
		Port *p = pair.first;
		if (p->getNode() != NULL)
		{
			conflict_number += pair.second.size();

			congestion_detail<< "CONFLICT :" << p->getFullName();
			for (DFGNode *node : pair.second)
			{
				congestion_detail << "\t ," << node->idx << "|BB=" << node->BB;
			}
			congestion_detail << ", with MAPPED = " << p->getNode()->idx << "|BB=" << p->getNode()->BB;
		}
	}

	return conflict_number;
}

int CGRAXMLCompile::SAMapper::getPortUsage()
{
	int usage_number = 0;

	for (std::pair<Port *, std::set<DFGNode *>> pair : congestedPorts)
	{
		usage_number += pair.second.size();
	}
	return usage_number;
}

// total hop count over all currently routed edges.
// Each entry of data_routing_path is the A*-found path for one DFG edge;
// consecutive LatPort elements are the PE hops it takes.
int CGRAXMLCompile::SAMapper::getWireLength()
{
	int wire = 0;
	for (auto &kv : data_routing_path)
	{
		const std::vector<LatPort> &path = kv.second;
		if (path.size() > 1)
			wire += (int)path.size() - 1;
	}
	return wire;
}

int CGRAXMLCompile::SAMapper::getCost()
{
	int unmapped_node_number = getNumberOfUnmappedNodes();
	int port_usage = getPortUsage();
	int congestion_number = getCongestionNumber();
	int conflict_number = getConflictNumber();
	// costMode 1: replace port_usage with wire length.
	// The upstream cost has no delay/detour term at all, so once the warm start
	// is feasible there is nothing left to minimise and the annealer spins.
	int wire = getWireLength();
	int shaping = (costMode == 1) ? wire : port_usage;
	int total_cost = unmapped_node_number * 100 + congestion_number * 10 + conflict_number * 10 + shaping;
	LOG(COST) << "unmapped node:" << unmapped_node_number << " port usage:" << port_usage
	          << " wire:" << wire << " congestion:" << congestion_number
	          << " total cost:" << total_cost << "\n";
	return total_cost;
}

bool CGRAXMLCompile::SAMapper::SARoute(DFGNode *node, DataPath *candidateDP)
{
	std::map<DFGNode *, std::vector<Port *>> parent_output_ports;
	std::map<DFGNode *, Port *> alreadyMappedChildPorts;

	int minLat = 0;
	int II = 0;

	// this part is from PathFinder
	{
		for (DFGNode *parent : node->parents)
		{
			//		std::cout << "parent = " << parent->idx << "\n";
			if (parent->rootDP != NULL)
			{ // already mapped
				assert(parent->rootDP->getOutputDP()->getOutPort("T"));
				parent_output_ports[parent].push_back(parent->rootDP->getOutputDP()->getOutPort("T"));
			}
		}

		for (DFGNode *child : node->children)
		{
			if (child->rootDP != NULL)
			{ // already mapped
				LOG(ROUTE) << "child=" << child->idx << ",childOpType=" << node->childrenOPType[child] << "\n";
				assert(child->rootDP->getLat() != -1);
				if (node->childrenOPType[child] == "PS")
				{
					LOG(ROUTE) << "Skipping.....\n";
					continue;
				}
				assert(child->rootDP->getInPort(node->childrenOPType[child]));
				alreadyMappedChildPorts[child] = child->rootDP->getInPort(node->childrenOPType[child]);

				int ii = child->rootDP->getCGRA()->get_t_max();
				assert(child->rootDP->getLat() != -1);
				//Previsouly, this assumes that mapped children are for next iteration.
				// For SA, the mapped children might not be so. 
				if(node->childNextIter[child] == 1){
					alreadyMappedChildPorts[child]->setLat(child->rootDP->getLat() + ii);
				}else if (node->childNextIter[child] == 0){
					alreadyMappedChildPorts[child]->setLat(child->rootDP->getLat() );
				}else {
					assert(false);
				} 
			}
			else if (child->idx == node->idx)
			{
				// adding a placeholder as this will be modified according to the destination in consideration.
				alreadyMappedChildPorts[child] == NULL;
			}
		}

		minLat = getlatMinStartsPHI(node, parent_output_ports);
		PE *pe = candidateDP->getPE();
		CGRA *cgra = candidateDP->getCGRA();
		II = cgra->get_t_max();
		int t = pe->T;
		int minLatmodii = minLat % II;

		if (minLatmodii > t)
		{
			minLat += II + (t - minLatmodii);
		}
		else
		{
			minLat += t - minLatmodii;
		}
	}

	LOG(ROUTE) << "***routing destination:" << candidateDP->getFullName() << "\n";
	// check latency constraint before mapping
	bool route_success = false;
	for (int iteration = 0; iteration < maximum_routing_iteration; iteration++)
	{
		//
		LOG(ROUTE) << "routing iteration:" << iteration << "\n";
		int minLatDestVal_prime = minLat + II * iteration;

		LatPort candiLatport; // get a latency according to the iteration

		std::map<DFGNode *, std::vector<LatPort>> mappedChildPaths;

		FU *parentFU = candidateDP->getFU();
		assert(parentFU->supportedOPs.find(node->op) != parentFU->supportedOPs.end());
		int latency = parentFU->supportedOPs[node->op];
		int minLatDestVal = minLatDestVal_prime;
		// route parent nodes
		for (auto &parent_info : parent_output_ports)
		{
			auto parent = parent_info.first;
			minLatDestVal = minLatDestVal_prime + parent->childNextIter[node] * II;
			if (parent->getOPtype(node) == "PS")
			{
				LOG(ROUTE) << "Skipping.....\n";
				continue;
			}

			Port *destPort = candidateDP->getInPort(parent->getOPtype(node));
			LatPort destPortLat = std::make_pair(minLatDestVal, destPort);
			if (!canExitCurrPE(destPortLat))
			{
				LOG(ROUTE) << "cannot exit current PE. Continue.....\n";
				route_success = false;
				break;
			}
			auto startCand = parent_info.second.front();
			LatPort startCandLat = std::make_pair(startCand->getLat(), startCand);
			int cost;
			std::vector<LatPort> path;
			std::map<Port *, std::set<DFGNode *>> mutexPaths;
			route_success = LeastCostPathAstar(startCandLat, destPortLat, candidateDP, path, cost, parent, mutexPaths, node);

			if (route_success)
			{

				LOG(SA) << "routing success from node" << parent->idx << "," << startCand->getFullName() << "," << startCandLat.first << " to "
						<< node->idx << ", node" << destPort->getFullName() << "," << destPortLat.first << "\n";
				assignPath(parent, node, path);
				data_routing_path.emplace(std::make_pair(parent, node), path);
			}
			else
			{

				LOG(SA) << "routing failure from " << parent->idx << "," << startCand->getFullName() << "," << startCandLat.first << " to "
						<< node->idx << "," << destPort->getFullName() << "," << destPortLat.first << "\n";
				break;
			}
		}

		if (!route_success)
		{
			node->SAClear(this->dfg);
			continue;
		}

		// route child nodes
		for (std::pair<DFGNode *, Port *> pair : alreadyMappedChildPorts)
		{
			DFGNode *child = pair.first;
			Port *childDestPort = pair.second;
			DataPath *childDP = child->rootDP;

			if (node->childrenOPType[child] == "PS")
			{
				LOG(ROUTE) << "Skipping.....\n";
				continue;
			}

			if (child->idx == node->idx)
			{
				childDestPort = candidateDP->getInPort(node->childrenOPType[child]);
				// if (detailedDebug) cout << "setting latency = " << minLatDestVal + ii << "\n";
				childDestPort->setLat(minLatDestVal + II);
				childDP = candidateDP;
			}

			std::vector<LatPort> path;
			int cost;

			FU *parentFU = candidateDP->getFU();
			assert(parentFU->supportedOPs.find(node->op) != parentFU->supportedOPs.end());
			int latency = parentFU->supportedOPs[node->op];
			Port *destPort = candidateDP->getOutputPort(latency);

			std::map<Port *, std::set<DFGNode *>> mutexPaths;
			// if (detailedDebug)
			// 	std::cout << "already child Estimating Path" << destPort->getFullName() << "," << minLatDestVal + latency << ","
			// 				<< "--->" << childDestPort->getFullName() << "," << childDestPort->getLat() << "," << "exist_child = " << child->idx
			// 				<< "\n";
			// if (detailedDebug)
			// 	std::cout << "lat = " << childDestPort->getLat() << ",PE=" << childDestPort->getMod()->getPE()->getName() << ",t=" << childDestPort->getMod()->getPE()->T << "\n";
			childDestPort->setLat(childDP->getLat() + node->childNextIter[child] * II);
			LatPort childDestPortLat = std::make_pair(childDestPort->getLat(), childDestPort);
			assert(childDestPort->getLat() != -1);
			LatPort destPortLat = std::make_pair(minLatDestVal + latency, destPort);

			route_success = LeastCostPathAstar(destPortLat, childDestPortLat, childDP, path, cost, node, mutexPaths, child);

			if (route_success)
			{

				LOG(SA) << "routing success from " << node->idx << "," << destPort->getFullName() << "," << destPortLat.first << " to "
						<< child->idx << "," << childDestPort->getFullName() << "," << childDestPortLat.first << "\n";
				assignPath(node, child, path);
				data_routing_path.emplace(std::make_pair(node, child), path);
			}
			else
			{

				LOG(SA) << "routing failure from " << node->idx << "," << destPort->getFullName() << "," << destPortLat.first << " to "
						<< child->idx << "," << childDestPort->getFullName() << "," << childDestPortLat.first << "\n";
				break;
			}
		}
		if (!route_success)
		{
			node->SAClear(this->dfg);
			continue;
		}

		candidateDP->assignNode(node, minLatDestVal, this->dfg);
		dfg_node_placement.emplace(node, std::make_pair(candidateDP, minLatDestVal));
		node->rootDP = candidateDP;
		break;
	}
	// if(route_success)
	return route_success;
}

std::vector<CGRAXMLCompile::DataPath *> CGRAXMLCompile::SAMapper::getRandomDPCandidate(DFGNode *node)
{
	std::vector<DataPath *> candidateDests;
	int penalty = 0;
	std::map<DataPath *, int> dpPenaltyMap;
	unordered_set<PE *> allPEs = cgra->getAllPEList();
	for (PE *currPE : allPEs)
	{
		for (Module *submod : currPE->subModules)
		{
			if (FU *fu = dynamic_cast<FU *>(submod))
			{

				if (fu->supportedOPs.find(node->op) == fu->supportedOPs.end())
				{
					continue;
				}

				if (fu->currOP.compare(node->op) == 0)
				{
					for (Module *submodFU : fu->subModules)
					{
						if (DataPath *dp = dynamic_cast<DataPath *>(submodFU))
						{

							if (cgra->is_spm_modelled)
							{
								if (!node->base_pointer_name.empty())
								{
									// base pointer name is not empty
									if (dp->accesible_memvars.find(node->base_pointer_name) == dp->accesible_memvars.end())
									{
										// this dp does not support the variable
										continue;
									}
								}
							}

							if (checkDPFree(dp, node, penalty))
							{
								//									if(dp->getMappedNode()==NULL){
								//									if(dataPathCheck(dp,&node)){

								if (node->blacklistDest.find(dp) == node->blacklistDest.end())
								{
									// M1: 退火阶段也必须遵守约束。initMap 走的是
									// PathFinderMapper::estimateRouting（那里已过滤），
									// 但邻域算子走这里，漏掉就等于约束只在热启动时有效。
									if (!constraints.allowed(node->idx,
									                         dp->getPE()->getPosition_X(),
									                         dp->getPE()->getPosition_Y()))
										continue;
									candidateDests.push_back(dp);
									dpPenaltyMap[dp] = penalty;
								}
							}
						}
					}
				}
				else if (fu->currOP.compare("NOP") == 0)
				{
					for (Module *submodFU : fu->subModules)
					{
						if (DataPath *dp = dynamic_cast<DataPath *>(submodFU))
						{

							bool is_mem_op = node->op.find("LOAD") != string::npos || node->op.find("STORE") != string::npos;
							if (cgra->is_spm_modelled)
							{
								if (!node->base_pointer_name.empty() && is_mem_op)
								{
									// base pointer name is not empty
									if (dp->accesible_memvars.find(node->base_pointer_name) == dp->accesible_memvars.end())
									{
										// this dp does not support the variable
										LOG(ROUTE) << "memvar=" << node->base_pointer_name << " is not supported in " << dp->getFullName() << "\n";
										continue;
									}
								}
							}

							if (checkDPFree(dp, node, penalty))
							{
								//									if(dp->getMappedNode()==NULL){
								//									if(dataPathCheck(dp,&node)){

								if (node->blacklistDest.find(dp) == node->blacklistDest.end())
								{
									// M1: 退火阶段也必须遵守约束。initMap 走的是
									// PathFinderMapper::estimateRouting（那里已过滤），
									// 但邻域算子走这里，漏掉就等于约束只在热启动时有效。
									if (!constraints.allowed(node->idx,
									                         dp->getPE()->getPosition_X(),
									                         dp->getPE()->getPosition_Y()))
										continue;
									candidateDests.push_back(dp);
									dpPenaltyMap[dp] = penalty;
								}
							}
						}
					}
				}
			}
		}
	}

	if (pickStrategy == 1 && !candidateDests.empty())
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
		        << " for node " << node->idx << "\n";
	}
	else
	{
		// M1.5: 候选表洗牌也走 rng（上游用 system_clock 当种子，无法复现）
		shuffle(candidateDests.begin(), candidateDests.end(), rng);
	}

	return candidateDests;
}

// compared to the PathFinderMapper one, add some additional information for SA.
bool CGRAXMLCompile::SAMapper::Route(DFGNode *node,
									 std::priority_queue<dest_with_cost> &estimatedRoutes,
									 DFGNode **failedNode)
{

	LOG(ROUTE) << "Route begin...\n";

	int parentRoutingPortCount = 0;
	int routedParents = 0;

	for (DFGNode *parent : node->parents)
	{
		int thisParentNodeCount = 0;
		if (parent->rootDP != NULL)
		{
			thisParentNodeCount = parent->routingPorts.size();
		}

		//		if(thisParentNodeCount>0){
		//			routedParents++;
		//			thisParentNodeCount--; //remove the T port in the cout
		//		}
		parentRoutingPortCount += thisParentNodeCount;
	}
	//	if(parentRoutingPortCount>0){
	//		parentRoutingPortCount-=1; //remove the T port in the cout
	//	}

	int addedRoutingParentPorts = 0;

	bool routeSucc = false;
	dest_with_cost currDest;
	while (!estimatedRoutes.empty())
	{
		currDest = estimatedRoutes.top();
		estimatedRoutes.pop();

		if (currDest.dest->getMappedNode() != NULL)
		{
			LOG(ROUTE) << "currDest is not NULL \n";
			LOG(ROUTE) << "currDP:" << currDest.dest->getName() << ",currPE:" << currDest.dest->getPE()->getName() << "\n";
			LOG(ROUTE) << "currNode:" << currDest.dest->getMappedNode()->idx << "\n";
		}
		assert(currDest.dest->getMappedNode() == NULL);
		LOG(ROUTE) << "alreadyMappedChilds = " << currDest.alreadyMappedChilds.size() << "\n";

		bool alreadMappedChildRouteSucc = true; // this will change to false if failure in alreadyMappedChilds
		std::map<DFGNode *, std::vector<LatPort>> mappedChildPaths;
		std::map<DFGNode *, std::map<Port *, std::set<DFGNode *>>> mappedChildMutexPaths;
		while (!currDest.alreadyMappedChilds.empty())
		{
			dest_child_with_cost dest_child_with_cost_ins = currDest.alreadyMappedChilds.top();
			currDest.alreadyMappedChilds.pop();

			std::vector<LatPort> possibleStarts;
			possibleStarts.clear();
			possibleStarts.push_back(dest_child_with_cost_ins.startPort);
			for (std::pair<Port *, int> pair : node->routingPorts)
			{
				possibleStarts.push_back(std::make_pair(pair.first->getLat(), pair.first));
				assert(pair.first->getLat() != -1);
			}

			std::priority_queue<cand_src_with_cost> q;
			std::map<Port *, std::set<DFGNode *>> mutexPathsTmp;
			std::vector<LatPort> pathTmp;
			for (LatPort p : possibleStarts)
			{
				int cost;
				if (LeastCostPathAstar(p, dest_child_with_cost_ins.childDest, dest_child_with_cost_ins.childDP, pathTmp, cost, node, mutexPathsTmp, dest_child_with_cost_ins.child))
				{
					pathTmp.clear();
					q.push(cand_src_with_cost(p, dest_child_with_cost_ins.childDest, cost));
				}
			}

			int cost;
			std::vector<LatPort> path;
			LatPort src = dest_child_with_cost_ins.startPort;
			LatPort dest = dest_child_with_cost_ins.childDest;

			while (!q.empty())
			{
				cand_src_with_cost head = q.top();
				q.pop();
				std::map<Port *, std::set<DFGNode *>> mutexPaths;
				alreadMappedChildRouteSucc = LeastCostPathAstar(head.src, dest, dest_child_with_cost_ins.childDP, path, cost, node, mutexPaths, dest_child_with_cost_ins.child);
				if (alreadMappedChildRouteSucc)
				{
					assignPath(node, dest_child_with_cost_ins.child, path);
					data_routing_path.emplace(std::make_pair(node, dest_child_with_cost_ins.child), path);
					mappedChildPaths[dest_child_with_cost_ins.child] = path;
					mappedChildMutexPaths[dest_child_with_cost_ins.child] = mutexPaths;
					LOG(SA) << "Route success :: from=" << src.second->getFullName() << "--> to=" << dest.second->getFullName() << "|node=" << node->idx << "\n";
					break;
				}
				else
				{
					LOG(SA) << "Route Failed :: from=" << src.second->getFullName() << "--> to=" << dest.second->getFullName() << "\n";
					for (LatPort p : path)
					{
						if (p.second->getMod()->getPE())
						{
							LOG(SA) << p.second->getMod()->getPE()->getName() << "-->";
						}
					}
					LOG(SA) << "\n";

					for (LatPort p : path)
					{
						LOG(SA) << p.second->getFullName() << "\n";
					}
				}
				path.clear();
			}
			if (!alreadMappedChildRouteSucc)
			{
				*failedNode = dest_child_with_cost_ins.child;
				break;
			}
		}

		if (alreadMappedChildRouteSucc)
		{
			for (std::pair<Port *, int> pair : node->routingPorts)
			{
				Port *p = pair.first;
				int destIdx = pair.second;
				LOG(ROUTE) << "to:" << destIdx << "," << p->getFullName() << "\n";
			}
		}

		if (!alreadMappedChildRouteSucc)
		{
			node->SAClear(this->dfg);
			continue; // try the next dest
		}
		else
		{
			std::stringstream ss;
			ss << "Already Mapped child Routes....\n";
			for (std::pair<DFGNode *, std::vector<LatPort>> pair : mappedChildPaths)
			{
				DFGNode *child = pair.first;
				for (LatPort lp : pair.second)
				{
					Port *p = lp.second;
					ss << "to:" << child->idx << " :: ";
					ss << p->getFullName();
					if (mappedChildMutexPaths[child].find(p) != mappedChildMutexPaths[child].end())
					{
						ss << "|mutex(";
						for (DFGNode *mutexnode : mappedChildMutexPaths[child][p])
						{
							ss << mutexnode->idx << ",";
						}
						ss << ")";
					}
					ss << "\n";
				}
				ss << "\n";
			}
			ss << "\n";
			LOG(ROUTE) << ss.str();
		}

		bool parentRoutSucc = true;
		addedRoutingParentPorts = 0;
		std::map<DFGNode *, std::map<Port *, std::set<DFGNode *>>> mappedParentMutexPaths;
		while (!currDest.parentStartLocs.empty())
		{
			parent_cand_src_with_cost pcswc = currDest.parentStartLocs.top();
			currDest.parentStartLocs.pop();
			DFGNode *parent = pcswc.parent;
			std::priority_queue<cand_src_with_cost> &q = pcswc.cswc;

			bool succ = false;
			while (!q.empty())
			{
				cand_src_with_cost cand_src_with_cost_ins = q.top();
				q.pop();
				LatPort src = cand_src_with_cost_ins.src;
				LatPort dest = cand_src_with_cost_ins.dest;
				std::vector<LatPort> path;
				std::map<Port *, std::set<DFGNode *>> mutexPath;
				int cost;
				succ = LeastCostPathAstar(src, dest, currDest.dest, path, cost, parent, mutexPath, node);
				if (succ)
				{
					//					bool routedParent=true;
					//					if(parent->routingPorts.size()==0){ //unrouted parent
					//						routedParent=false;
					//					}
					assignPath(parent, node, path);
					data_routing_path.emplace(std::make_pair(parent, node), path);

					mappedParentMutexPaths[parent] = mutexPath;
					addedRoutingParentPorts += path.size();
					//					if(routedParent){
					addedRoutingParentPorts -= 1;
					//					}
					//					for(Port* p : path){
					//						std::cout << p->getFullName() << ",\n";
					//					}
					//					std::cout << "\n";
					break;
				}
				else
				{
					addedRoutingParentPorts = 0;
					node->SAClear(this->dfg);
					LOG(SA) << "Route Failed :: from=" << src.second->getFullName() << "--> to=" << dest.second->getFullName() << "\n";
				}
				path.clear();
			}
			if (!succ)
			{
				*failedNode = parent;
				node->SAClear(this->dfg);
				addedRoutingParentPorts = 0;
				parentRoutSucc = false; // at least one parent failed to route, try a new dest
				break;
			}
		}

		if (parentRoutSucc)
		{ // all parents routed succesfull + all mapped childs are connected
			routeSucc = true;
			std::stringstream ss;
			ss << "node=" << node->idx << ",op=" << node->op << " is mapped to " << currDest.dest->getPE()->getName() << ",lat=" << currDest.destLat << "\n";
			ss << "routing info ::\n";
			for (DFGNode *parent : node->parents)
			{
				ss << "parent routing port size = " << parent->routingPorts.size() << "\n";
				int prev_lat = -1;
				for (std::pair<Port *, int> pair : parent->routingPorts)
				{
					Port *p = pair.first;
					//					if(node.routingPortDestMap[p]==&node){
					ss << "fr:" << parent->idx << " :: ";
					ss << ",dest=" << pair.second << " :: ";
					ss << p->getFullName();
					ss << ",lat=" << p->getLat();

					if (mappedParentMutexPaths[parent].find(p) != mappedParentMutexPaths[parent].end())
					{
						ss << "|mutex(";
						for (DFGNode *mutexnode : mappedParentMutexPaths[parent][p])
						{
							ss << mutexnode->idx << ",";
						}
						ss << ")";
					}
					ss << std::endl;
					//					}
					if (prev_lat != -1)
					{
						//							assert(p->getLat() - prev_lat <= 1);
					}
					prev_lat = p->getLat();
				}
			}
			ss << "routing info done.\n";
			currDest.dest->assignNode(node, currDest.destLat, this->dfg);
			node->rootDP = currDest.dest;
			dfg_node_placement.emplace(node, std::make_pair(currDest.dest, currDest.destLat));

			mappingLog4 << node->idx << "," << currDest.dest->getPE()->X << "," << currDest.dest->getPE()->Y << "," << currDest.destLat << "\n";
			ss << "mappingLog4=" << node->idx << "," << currDest.dest->getPE()->X << "," << currDest.dest->getPE()->Y << "," << currDest.destLat << "\n";
			LOG(ROUTE) << ss.str();

			break;
		}
		node->SAClear(this->dfg);
	}

	if (routeSucc)
	{
		LOG(SA) << "Route success...\n";

		int parentRoutingPortCountEnd = 0;
		//		int mappedParentCount=0;
		for (DFGNode *parent : node->parents)
		{
			if (parent->rootDP != NULL)
			{
				//				mappedParentCount++;
				parentRoutingPortCountEnd += parent->routingPorts.size();
			}
		}
		parentRoutingPortCountEnd = std::max(0, parentRoutingPortCountEnd - routedParents);
		if (parentRoutingPortCountEnd != parentRoutingPortCount + addedRoutingParentPorts)
		{
			LOG(ROUTE) << "parentRoutingPortCountEnd=" << parentRoutingPortCountEnd << "\n";
			LOG(ROUTE) << "addedRoutingParentPorts=" << addedRoutingParentPorts << "\n";
			LOG(ROUTE) << "parentRoutingPortCount=" << parentRoutingPortCount << "\n";
		}

		//		assert(parentRoutingPortCountEnd==parentRoutingPortCount+addedRoutingParentPorts);
		return true;
	}
	else
	{
		currDest.dest->assignNode(node, currDest.destLat, this->dfg);
		dfg_node_placement.emplace(node, std::make_pair(currDest.dest, currDest.destLat));
		node->rootDP = currDest.dest;
		node->SAClear(this->dfg);
		LOG(ROUTE) << "Route failed...\n";

		int parentRoutingPortCountEnd = 0;
		//		int mappedParentCount=0;
		for (DFGNode *parent : node->parents)
		{
			if (parent->rootDP != NULL)
			{
				//				mappedParentCount++;
				parentRoutingPortCountEnd += parent->routingPorts.size();
			}
		}
		parentRoutingPortCountEnd = std::max(0, parentRoutingPortCountEnd - routedParents);
		if (parentRoutingPortCountEnd != parentRoutingPortCount + addedRoutingParentPorts)
		{
			LOG(ROUTE) << "parentRoutingPortCountEnd=" << parentRoutingPortCountEnd << "\n";
			LOG(ROUTE) << "addedRoutingParentPorts=" << addedRoutingParentPorts << "\n";
			LOG(ROUTE) << "parentRoutingPortCount=" << parentRoutingPortCount << "\n";
		}
		//		assert(parentRoutingPortCountEnd==parentRoutingPortCount);
		assert(*failedNode != NULL);
		return false;
	}
}

std::string CGRAXMLCompile::SAMapper::mapStatus()
{
	std::stringstream ss;
	for (auto node : sortedNodeList)
	{
		if (dfg_node_placement.find(node) == dfg_node_placement.end())
		{
			ss << "node " << node->idx << " not placed.\n";
		}
		else
		{
			auto p = dfg_node_placement[node];
			ss << "node " << node->idx << " placed on " << p.first->getFullName() << " lat:" << p.second << "\n";
		}
	}
	return ss.str();
}

int CGRAXMLCompile::SAMapper::getNumberOfUnmappedNodes()
{
	int n = 0;
	for (auto node : sortedNodeList)
	{
		if (dfg_node_placement.find(node) == dfg_node_placement.end())
		{
			n++;
		}
	}
	return n;
}

int CGRAXMLCompile::SAMapper::checkAnyUnroutedEdge()
{
	int n = 0;
	for (auto node : sortedNodeList)
	{
		for (auto child : node->children)
		{
			if (node->childrenOPType[child] == "PS")
				continue;
			if (data_routing_path.find(std::make_pair(node, child)) == data_routing_path.end())
			{
				LOG(SA) << "did not route edge: " << node->idx << " ->" << child->idx << "\n";
				n++;
			}
		}
	}
	return n;
}
