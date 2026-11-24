import pathlib

cpp = pathlib.Path("/home/sutong/acc-thu-agent/src/mapper/src/mapper/SimulatedAnnealingMapper.cpp")
t = cpp.read_text()

def sub(text, old, new, tag, n=1):
    c = text.count(old)
    assert c == n, "%s: 期望 %d 处，实际 %d 处" % (tag, n, c)
    return text.replace(old, new)

# ---------- 1) inner_map 主分支：空候选表不能 .front() ----------
old1 = (
    '\t\tauto dp_candidate = getRandomDPCandidate(selected_dfg_node).front();\n'
    '\t\tLOG(SA) << "map this DFG node:" << selected_dfg_node->idx << " op:" << selected_dfg_node->op << "to pe:" << dp_candidate->getPE()->getName() ;\n'
)
new1 = (
    '\t\t// M1: 约束（--constraints）过滤之后候选表可能为空。上游在这里直接 .front()，\n'
    '\t\t// 空表就是未定义行为——实测 SIGSEGV，gdb 栈：inner_map:417 -> SAMap:145 -> main:179。\n'
    '\t\t// 空表只说明"这个节点此刻没有合法位置"，那是一次失败的移动，不是崩溃的理由：\n'
    '\t\t// 回滚已清掉的节点、恢复快照、继续下一次移动。\n'
    '\t\tstd::vector<DataPath *> dp_candidates = getRandomDPCandidate(selected_dfg_node);\n'
    '\t\tif (dp_candidates.empty())\n'
    '\t\t{\n'
    '\t\t\tLOG(SA) << "no legal candidate for node " << selected_dfg_node->idx\n'
    '\t\t\t        << " (constraints filtered everything), skip this move\\n";\n'
    '\t\t\tmoved_nodes.push_back(selected_dfg_node);\n'
    '\t\t\tfor (auto node : moved_nodes)\n'
    '\t\t\t{\n'
    '\t\t\t\tclearNodeMapping(node);\n'
    '\t\t\t\trestoreMapping(node, old_dfg_node_placement, old_data_routing_path);\n'
    '\t\t\t}\n'
    '\t\t\tdfg_node_placement.clear();\n'
    '\t\t\tdfg_node_placement.insert(old_dfg_node_placement.begin(), old_dfg_node_placement.end());\n'
    '\t\t\tdata_routing_path.clear();\n'
    '\t\t\tdata_routing_path.insert(old_data_routing_path.begin(), old_data_routing_path.end());\n'
    '\t\t\tcontinue;\n'
    '\t\t}\n'
    '\t\tauto dp_candidate = dp_candidates.front();\n'
    '\t\tLOG(SA) << "map this DFG node:" << selected_dfg_node->idx << " op:" << selected_dfg_node->op << "to pe:" << dp_candidate->getPE()->getName() ;\n'
)
t = sub(t, old1, new1, "inner_map empty guard")

# ---------- 2) 死分支里的同一处（child_node != NULL && false），一并加保护 ----------
old2 = (
    '\t\t\t\tclearNodeMapping(parent);\n'
    '\t\t\t\tauto dp_candidate = getRandomDPCandidate(parent).front();\n'
)
new2 = (
    '\t\t\t\tclearNodeMapping(parent);\n'
    '\t\t\t\tstd::vector<DataPath *> parent_candidates = getRandomDPCandidate(parent);\n'
    '\t\t\t\tif (parent_candidates.empty())\n'
    '\t\t\t\t{\n'
    '\t\t\t\t\t// 同样的空表保护（这段当前是死代码，但保留正确的写法）\n'
    '\t\t\t\t\troute_succ = false;\n'
    '\t\t\t\t\tbreak;\n'
    '\t\t\t\t}\n'
    '\t\t\t\tauto dp_candidate = parent_candidates.front();\n'
)
t = sub(t, old2, new2, "parent empty guard")

cpp.write_text(t)
print("OK: inner_map 两处空候选表保护已加")
