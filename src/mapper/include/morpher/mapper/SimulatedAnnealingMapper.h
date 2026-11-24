/*
 * SimulatedAnnealingMapper.h
 * Since PathFinderMapper has implemented many functions and other source file also use PathFinderMapper, better to create a mapper which bases
 * on PathFinderMapper
 */

#include <morpher/mapper/PathFinderMapper.h>
#include <morpher/arch/DataPath.h>
#include <morpher/util/debug.h>

#include <string>
#include <random>
#include <algorithm> 
#include <chrono>
#include <sys/time.h>
#ifndef SAMAPPER_H_
#define SAMAPPER_H_

namespace CGRAXMLCompile
{


using port_util = std::pair<int,int>;
using dfg_data = std::pair<DFGNode*,DFGNode*>; // <src, des>

class SAMapper : public PathFinderMapper
{
public:
	SAMapper(std::string fName) : PathFinderMapper(fName){
		mapping_method_name  = "SA";
	};

	bool SAMap(CGRA *cgra, DFG *dfg);

	// false = 上游行为：热启动（initMap 的 PathFinder 式放置）只要合法就 return，
	// 退火循环一次都不跑。true = 忽略热启动的合法性，真的退火。
	// M1.5: SA 的随机性必须可复现。上游每个随机点都现场 new 一个
	// std::random_device（真随机源），导致同一输入两次跑出不同映射、
	// 甚至一次能映射一次不能。做 agent 与 SA 的对比实验前先钉住种子。
	std::mt19937 rng{20136u};
	void seedRng(unsigned s) { rng.seed(s); }
	bool forceAnneal = false;

	// M1.5: 两个邻域算子的策略。0 = 上游行为（纯随机 / 洗牌取首个），
	// 1 = 有依据的挑选。默认 0，保证 M0/M1 的结论不被悄悄改掉。
	int selectStrategy = 0;
	int pickStrategy = 0;
	int costMode = 0;
	int getWireLength();

	// 一个节点"有多该被挪"：所在 PE 上还挤了几个节点 + 已布线边上多绕了几跳。
	// 只用现在就能算的信息，不假装知道布线能不能通（那要 SARoute 才知道）。
	int nodeBlame(DFGNode *node);

	// 一个候选 DP 有多好：到已放置邻居的曼哈顿距离之和 * 2 + 该 PE 的占用数。
	// 便宜的代理指标，用来在 .front() 之前把明显更远的候选排到后面。
	int candidateScore(DataPath *dp, DFGNode *node);

	// 把当前 dfg_node_placement 落盘（mapping.csv / routeInfo.log / mappingwithlatency.txt）。
	// 上游只在 HeuristicMapper::Map() 的成功路径里写这些文件，SAMap() 不经过那段，
	// 所以 SA 的产物一直是 0 字节。
	void dumpSAMapping();

	// acc-thu: 可行之后继续退火。上游把 isCurrMappingValid() 同时当停止条件与
	// 接受条件，第一次可行就 return，于是 SA 从来没做过优化。
	bool optimizeAfterFeasible = false;
	int moveBudget = 0; // 0 = 不限
	int movesDone = 0;
	bool budgetExhausted = false;
	bool haveBest = false;
	int bestCost = 0;
	std::map<DFGNode *, std::pair<DataPath *, int>> best_placement;
	std::map<dfg_data, std::vector<LatPort>> best_routing;
	void considerBest();
	bool restoreBestSnapshot();

	bool initMap();
	bool Route(DFGNode *node, std::priority_queue<dest_with_cost> &estimatedRoutes, DFGNode **failedNode);

	float inner_map();
	DFGNode* selectDFGNodeToUnmap();
	DFGNode* selectAParentForDFGNode(DFGNode* target_node);
	bool clearNodeMapping(DFGNode * node);
	std::vector<DataPath*> getRandomDPCandidate(DFGNode *node);
	bool SARoute(DFGNode *node, DataPath * candidate);
	int getCost();
	bool restoreMapping(DFGNode *node, 	std::map<DFGNode*, std::pair<DataPath*, int>> & dfg_node_placement, 
										std::map<dfg_data, std::vector<LatPort>> & data_routing_path);
	
	
	int getCongestionNumber(std::stringstream & congestion_detail);
	int getCongestionNumber(){
		std::stringstream ss;
		return getCongestionNumber(ss);
	};

	int getConflictNumber(std::stringstream & congestion_detail);
	int getConflictNumber(){
		std::stringstream ss;
		return getConflictNumber(ss);
	};
	int getPortUsage();
	int getNumberOfUnmappedNodes();
	int checkAnyUnroutedEdge();
	std::string mapStatus();
	bool isCurrMappingValid(){
		int overuse_number = getCongestionNumber();
		int conflict_number = getConflictNumber();
		int unmapped_node_number = getNumberOfUnmappedNodes();
		int unrouted_edge = checkAnyUnroutedEdge();
		if(unmapped_node_number == 0 && conflict_number == 0 && unrouted_edge > 0) assert(false && "some edges are not routed!");
		
		if(unmapped_node_number == 0 && conflict_number == 0  && overuse_number == 0){
			return true;
		}else{
			return false;
		}
	}

	float updateTemperature(float t, float acceptance_rate)
	{
			if (acceptance_rate > 0.96)
			{
					return t * 0.5;
			}
			else if (acceptance_rate > 0.8)
			{
					return t * 0.9;
			}
			else if (acceptance_rate > 0.15)
			{
					return t * 0.98;
			}
			else
			{
					return t * 0.95;
			}
	}

	static inline double getcurrenttime()
	{
			struct timeval t;
			gettimeofday(&t, NULL);
			return t.tv_sec + t.tv_usec * 0.000001;
	}

	bool whetherAcceptNewMapping(float new_cost, float old_cost, float temperature)
	{
			if (new_cost < old_cost)
					return true;

			float probability = exp(-(new_cost - old_cost) / temperature);

			// 上游用全局 rand()（从不 srand，实际恒定），改走 rng 以便 --sa_seed 统一控制
			std::uniform_real_distribution<double> u01(0.0, 1.0);
			return probability > u01(rng);
	}
	//	bool LeastCostPathAstar(Port* start, Port* end, DataPath* endDP, std::vector<Port*>& path, int& cost, DFGNode* node, std::map<Port*,std::set<DFGNode*>>& mutexPaths, DFGNode* currNode);



protected:

	int congest_num;
	int routing_num;

	float maximum_temp = 200;
	float minimim_temp = 10;
	float curr_temp;
	int movement_in_each_temp = 100;
 	float cold_accept_rate = 0.01;
	std::map<DFGNode*, std::pair<DataPath*, int>> dfg_node_placement;
	std::map<dfg_data, std::vector<LatPort>> data_routing_path;
	int maximum_routing_iteration = 3;

	int curr_cost;

	int backtrack_credit = 3; // this is different from the PathFinder one.



	

	
};

} /* namespace CGRAXMLCompile */

#endif /* SAMAPPER_H_ */
