#!/usr/bin/env python3
"""给 acc-thu 的 mapper 加「钉住放置」能力（-m 3 = PinnedMapper）。

我们自己的 router 说某个放置可布，不等于 morpher 布得出来。要证明进化出的
映射是真的，只能把它交给 morpher 布一遍、再仿真。这个补丁就是那条通道：

    --pins pins.json      {node: (x,y,t)} 或 {"pins":[{node,x,y,t}]}
    --pins_mode 1         1=strict（每个节点必须落在 pin 上）
                          2=repair（布不到的 pin 丢掉，其余保留）
    --report rep.json     机器可读结果

设计上和 ~/cac 的 LLMMapper 同源，但改了三处：

1. 名字叫 PinnedMapper 而不是 LLMMapper —— 它跟 LLM 没关系，输入是任意放置。
2. 没有 prefer 模式。实测 prefer 与 strict 在 10 个 kernel 上同分
   （docs/FINDINGS.md 记过），多一个模式只会让人以为有区别。
3. strict 失败后不扫 II。pin 里的 t 是按某个 II 算的相位，换 II 就全错，
   继续 II++ 只会产出无意义的失败。
"""
import io
import sys

M = "src/mapper/"

HOOK_DECL = '''	// 基类在 estimateRouting() 成功之后、Route() 之前调用；返回 false 表示
	// 放弃这个节点（候选队列按当前状态处理）。默认实现什么都不做。
	virtual bool restrictDestinations(DFGNode *node, std::priority_queue<dest_with_cost> &estimatedRoutes)
	{
		(void)node; (void)estimatedRoutes;
		return true;
	}
	std::string lastRestrictError;
'''

HOOK_CALL = '''				if (!this->restrictDestinations(node, estimatedRoutes))
				{
					std::cout << "Pins rejected node " << node->idx << " :: " << this->lastRestrictError << "\\n";
					mappingLog << "Pins rejected node " << node->idx << " :: " << this->lastRestrictError << "\\n";
					mappingLog.close();
					mappingLog2.close();
					mappingLog4.close();
					return false;
				}
				estimatedRouteInfo[node] = estimatedRoutes;'''

ARGS_FIELDS = '''	std::string arch_name = "";
	// 钉住放置：pins_file 为空时 PinnedMapper 退化成普通 PathFinder
	std::string pins_file = "";
	int pins_mode = 1; // 1=strict, 2=repair
	std::string report_file = "";
'''

ARGS_OPTS = '''		.add_option<bool>("", "--lisa_training", "lisa training", false)
		.add_option<std::string>("", "--pins", "placement pins json (node/x/y/t)", "")
		.add_option<int>("", "--pins_mode", "1=strict, 2=repair", 1)
		.add_option<std::string>("", "--report", "machine readable result json", "")
'''

ARGS_READ = '''	lisa_arg.training = args.get_option<bool>("--lisa_training");
	ret.pins_file = args.get_option<std::string>("--pins");
	ret.pins_mode = args.get_option<int>("--pins_mode");
	ret.report_file = args.get_option<std::string>("--report");
'''


def sub(s, old, new, path):
    if new in s:
        print("  already applied:", path)
        return s
    if old not in s:
        raise SystemExit("anchor not found in %s:\n%s" % (path, old[:200]))
    return s.replace(old, new, 1)


def patch_header():
    p = M + "include/morpher/mapper/HeuristicMapper.h"
    s = io.open(p, encoding="utf-8").read()
    anchor = ("	bool estimateRouting(DFGNode *node, std::priority_queue<dest_with_cost> &estimatedRoutes, DFGNode **failedNode);\n"
              "	bool Route(DFGNode *node, std::priority_queue<dest_with_cost> &estimatedRoutes, DFGNode **failedNode);\n")
    s = sub(s, anchor, anchor + "\n" + HOOK_DECL, p)
    io.open(p, "w", encoding="utf-8").write(s)
    print("  patched", p)


def patch_map_loop():
    p = M + "src/mapper/PathFinderMapper.cpp"
    s = io.open(p, encoding="utf-8").read()
    old = "\t\t\t\testimatedRouteInfo[node] = estimatedRoutes;"
    n = s.count(old)
    if n != 1:
        raise SystemExit("expected exactly 1 occurrence of the anchor in %s, found %d" % (p, n))
    s = sub(s, old, "\t\t\t" + HOOK_CALL, p)
    io.open(p, "w", encoding="utf-8").write(s)
    print("  patched", p)


def patch_util():
    p = M + "include/morpher/util/util.h"
    s = io.open(p, encoding="utf-8").read()
    s = sub(s, '\tstd::string arch_name = "";\n', ARGS_FIELDS, p)
    s = sub(s, '\t\t.add_option<bool>("", "--lisa_training", "lisa training", false)\n', ARGS_OPTS, p)
    s = sub(s, '\tlisa_arg.training = args.get_option<bool>("--lisa_training");\n', ARGS_READ, p)
    io.open(p, "w", encoding="utf-8").write(s)
    print("  patched", p)


def patch_main():
    p = M + "src/CGRA_xml_compiler.cpp"
    s = io.open(p, encoding="utf-8").read()

    s = sub(s, "#include <morpher/mapper/SimulatedAnnealingMapper.h>\n",
            "#include <morpher/mapper/SimulatedAnnealingMapper.h>\n#include <morpher/mapper/PinnedMapper.h>\n", p)

    s = sub(s, """	}else if(mapping_method  == 2){
		mapper = new LISAMapper(inputDFG_filename);
	}else{""",
            """	}else if(mapping_method  == 2){
		mapper = new LISAMapper(inputDFG_filename);
	}else if(mapping_method == 3){
		PinnedMapper *pinned = new PinnedMapper(inputDFG_filename);
		pinned->setPinsFile(args.pins_file);
		pinned->setPinsMode(args.pins_mode == 2 ? PinnedMapper::PINS_REPAIR : PinnedMapper::PINS_STRICT);
		pinned->setReportFile(args.report_file);
		mapper = pinned;
	}else{""", p)

    s = sub(s, """		}else if(mapping_method  == 2){
			LISAMapper * lisa_mapper = static_cast<LISAMapper*>(mapper);
			mappingSuccess = lisa_mapper->LISAMap(  args, tdi, II);
			if(args.lisa_arg.dfg_id != "none"){
				// this is genreatring traning data. No need to dump results;
				return 0;
			}
		}else{""",
            """		}else if(mapping_method  == 2){
			LISAMapper * lisa_mapper = static_cast<LISAMapper*>(mapper);
			mappingSuccess = lisa_mapper->LISAMap(  args, tdi, II);
			if(args.lisa_arg.dfg_id != "none"){
				// this is genreatring traning data. No need to dump results;
				return 0;
			}
		}else if(mapping_method  == 3){
			PinnedMapper * pinned_mapper = static_cast<PinnedMapper*>(mapper);
			mappingSuccess = pinned_mapper->MapWithPins(tempCGRA, &tempDFG);
		}else{""", p)

    # strict pin 失败后不要 II++
    s = sub(s, """			delete tempCGRA;
			II++;
""",
            """			delete tempCGRA;
			if(mapping_method == 3){
				// pin 里的 t 是按某个 II 算出来的相位，换 II 就全错。
				// 继续 II++ 只会产出一堆无意义的失败，直接退出。
				std::cout << "### pinned mapping failed at II = " << II << ", not scanning further\\n";
				break;
			}
			II++;
""", p)

    io.open(p, "w", encoding="utf-8").write(s)
    print("  patched", p)


if __name__ == "__main__":
    print("patching mapper for pinned placement")
    patch_header()
    patch_map_loop()
    patch_util()
    patch_main()
    print("done")
