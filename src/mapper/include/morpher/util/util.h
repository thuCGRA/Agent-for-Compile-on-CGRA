#include <morpher/util/argparse.h>
#include <morpher/dfg/DFG.h>
#include <morpher/arch/CGRA.h>
#include <morpher/arch/Module.h>
#include <morpher/arch/Port.h>


#include <iostream>
#include <chrono>
#include <math.h>
#include <set>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#pragma once

#ifndef MAPPER_UTIL_H_
#define MAPPER_UTIL_H_


struct lisa_arguments{
	bool lisa_eval_routing_priority = false;
	bool training = false;
	int max_training_iteration  = 5;
	std::string arch_name = "";
	std::string dfg_id = "none";
};

struct arguments
{
	string dfg_filename;
	int xdim = -1;
	int ydim = -1;
	string PEType;
	string json_file_name;
	int userII = 0;
	bool noMutexPaths=false;
	int backtracklimit = 0; // for PathFinderMapper, do not set this for a high number.  
	bool use_json = false;
	int ndps = 1;
	int maxiter = 30;  // for PathFinderMapper,
	int max_hops = 4;  // for HyCUBE
	int mapping_method = 0; // 0: PathFinderMapper, 1: SAMapper (SimulatedAnnealing),  HeuristicMapper will not be used
	int max_II = 32;
	// acc-thu: SA 只要热启动合法就直接 return，退火从不运行。置 true 强制退火。
	bool sa_force_anneal = false;
	int sa_seed = 20136;   // M1.5: SA 的 RNG 种子，同种子必须得到同一映射
	// 上游 SA 把 isCurrMappingValid() 同时当停止条件和接受条件：一旦可行就立刻
	// 返回，所以它只是个可行性搜索器，从不优化。置 true 让它在可行之后继续退火，
	// 最后回滚到代价最小的那个可行快照。
	bool sa_optimize = false;
	int sa_select = 0;   // M1.5: 0=上游随机 1=按 blame 挑节点
	int sa_pick = 0;     // M1.5: 0=上游洗牌取首个 1=按距离排序
	int sa_cost_mode = 0; // 0: upstream (port_usage)  1: wire length
	int sa_budget = 0;
	// M1: 约束文件（JSON），空表示无约束
	std::string constraints_file; // 移动次数上限，0 = 不限（上游行为）
	std::string arch_name = "";
	lisa_arguments lisa_arg;
};

inline arguments old_parse_arguments(int argn, char *argc[])
{
	arguments ret;

	int aflag = 0;
	int bflag = 0;
	char *cvalue = NULL;
	int index;
	int c;

	opterr = 0;

	while ((c = getopt(argn, argc, "d:x:y:t:j:i:eb:m:r:h:")) != -1)
		switch (c)
		{
		case 'd':
			ret.dfg_filename = string(optarg);
			break;
		case 'x':
			ret.xdim = atoi(optarg);
			break;
		case 'y':
			ret.ydim = atoi(optarg);
			break;
		case 't':
			ret.PEType = string(optarg);
			break;
		case 'n':
			ret.ndps = atoi(optarg);
			break;
		case 'j':
			ret.json_file_name = string(optarg);
			ret.use_json = true;
			break;
		case 'i':
			ret.userII = atoi(optarg);
			break;
		case 'e':
			ret.noMutexPaths = true;
			break;
		case 'b':
			ret.backtracklimit = atoi(optarg);
			break;
		case 'r':
			ret.maxiter = atoi(optarg);
		case 'm':
			ret.mapping_method = atoi(optarg);
			break;
		case 'h':
			ret.max_hops = atoi(optarg);
			break;
		case '?':
			if (optopt == 'c')
				fprintf(stderr, "Option -%c requires an argument.\n", optopt);
			else if (isprint(optopt))
				fprintf(stderr, "Unknown option `-%c'.\n", optopt);
			else
				fprintf(stderr,
						"Unknown option character `\\x%x'.\n",
						optopt);
		default:
			abort();
		}
		return ret;
};

inline arguments parse_arguments(int argn, char *argc[]){

	

	arguments ret;
	lisa_arguments lisa_arg ;

	d:x:y:t:j:i:eb:m:r:h:
	auto args = util::argparser("CGRA mapper argument parser.");
	args.set_program_name("mapper")
        .add_help_option()
        .add_option<int>("-x", "--xdim", "the x dimension of CGRA", -1)
		.add_option<int>("-y", "--ydim", "the x dimension of CGRA", -1)
		.add_option<std::string>("-j", "--json_arch", "architcture file in json", "")
		.add_option<std::string>("-d", "--dfg", "dfg file", "")
		.add_option<std::string>("-t", "--petype", "PE type", "")
		.add_option<int>("-i", "--ii", "initial II", 0)
		.add_option<int>("-m", "--mapping", "mapping method", 0)
		.add_option<int>("-n", "--datapath_number", "datapath_number", 1)
		.add_option<int>("-r", "--max_iter", "max iteration", 30)
		.add_option<int>("-h", "--hops", "hops for hycube", 4)
		.add_option<bool>("-e", "--noMutexPaths", "noMutexPaths", false)
		.add_option<int>("-b", "--backtrack_limit", "back track limit", 0)
		.add_option<int>("", "--max_II", "max II", 32)
		.add_option<int>("", "--sa_seed", "RNG seed for SA (same seed => same mapping)", 20136)
					.add_option<bool>("", "--sa_force_anneal", "SA: anneal even if the PathFinder warm start is already valid", false)
		.add_option<bool>("", "--sa_optimize", "SA: keep annealing after feasibility and return the cheapest valid snapshot", false)
		.add_option<int>("", "--sa_select", "SA: node selection 0=random(upstream) 1=blame-ranked", 0)
		.add_option<int>("", "--sa_pick", "SA: candidate pick 0=shuffle(upstream) 1=distance-ranked", 0)
		.add_option<int>("", "--sa_cost_mode", "cost function: 0=upstream(port_usage), 1=total wire length", 0)
		.add_option<int>("", "--sa_budget", "SA: max number of moves (0 = unlimited)", 0)
		.add_option<std::string>("", "--constraints", "JSON file with pins/forbidden (M1)", "")
		.add_option<std::string>("", "--arch_name", "architecture name", "")
		.add_option<bool>("", "--lisa_training", "lisa training", false)
		.add_option<std::string>("", "--dfg_id", "the dfg id used to generate training data", "none")
        .parse(argn, argc);

	ret.dfg_filename = args.get_option<std::string>("--dfg");
	ret.xdim = args.get_option<int>("--xdim");
	ret.ydim = args.get_option<int>("--ydim");
	ret.PEType = args.get_option<std::string>("--petype");
	ret.json_file_name = args.get_option<std::string>("--json_arch");
	ret.userII = args.get_option<int>("--ii");
	ret.noMutexPaths = args.get_option<bool>("--noMutexPaths");
	ret.backtracklimit = args.get_option<int>("--backtrack_limit");
	ret.ndps =  args.get_option<int>("--datapath_number");
	ret.maxiter = args.get_option<int>("--max_iter");
	ret.max_hops =  args.get_option<int>("--hops");
	ret.mapping_method = args.get_option<int>("--mapping");
	ret.max_II = args.get_option<int>("--max_II");
	ret.sa_force_anneal = args.get_option<bool>("--sa_force_anneal");
	ret.sa_seed = args.get_option<int>("--sa_seed");
	ret.sa_optimize = args.get_option<bool>("--sa_optimize");
	ret.sa_select = args.get_option<int>("--sa_select");
	ret.sa_pick = args.get_option<int>("--sa_pick");
	ret.sa_cost_mode = args.get_option<int>("--sa_cost_mode");
	ret.sa_budget = args.get_option<int>("--sa_budget");
	ret.constraints_file = args.get_option<std::string>("--constraints");
	ret.use_json = true;
	ret.arch_name = args.get_option<std::string>("--arch_name");
	lisa_arg.arch_name = args.get_option<std::string>("--arch_name");
	lisa_arg.training = args.get_option<bool>("--lisa_training");
	lisa_arg.dfg_id = args.get_option<std::string>("--dfg_id");
	ret.lisa_arg = lisa_arg;
	assert(ret.dfg_filename!= "");
	assert(ret.json_file_name!= "");
	args.print_as_ini(std::cout);

	return ret;

}




#endif /* LISAMAPPER_H_ */
