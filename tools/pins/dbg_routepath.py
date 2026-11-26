#!/usr/bin/env python3
"""Temporary: print why a --route-path candidate is rejected (first N per edge)."""
import os
import sys

ROOT = os.path.expanduser("~/acc-thu-ilp")
F_PF = os.path.join(ROOT, "src/mapper/src/mapper/PathFinderMapper.cpp")

s = open(F_PF, encoding="utf-8", errors="replace").read()
if "ROUTEPATH: reject" in s:
    print("already instrumented")
    sys.exit(0)

anchor = """					if (rpNext >= (int)edgePath->steps.size())
						continue;"""
repl = """					if (rpNext >= (int)edgePath->steps.size())
					{
						if (getenv("ROUTEPATH_DEBUG"))
							std::cout << "ROUTEPATH: reject " << node->idx << "->" << currNode->idx
							          << " beyond end of sequence\\n";
						continue;
					}"""
assert s.count(anchor) == 1, s.count(anchor)
s = s.replace(anchor, repl)

anchor2 = """					if (rpNextPE == NULL ||
					    std::make_pair(rpNextPE->getPosition_X(), rpNextPE->getPosition_Y()) !=
					        edgePath->steps[rpNext].first)
						continue;"""
repl2 = """					if (rpNextPE == NULL ||
					    std::make_pair(rpNextPE->getPosition_X(), rpNextPE->getPosition_Y()) !=
					        edgePath->steps[rpNext].first)
					{
						if (getenv("ROUTEPATH_DEBUG"))
							std::cout << "ROUTEPATH: reject " << node->idx << "->" << currNode->idx
							          << " PE " << (rpNextPE ? rpNextPE->getName() : "NULL")
							          << " expected (" << edgePath->steps[rpNext].first.first << ","
							          << edgePath->steps[rpNext].first.second << ")\\n";
						continue;
					}"""
assert s.count(anchor2) == 1, s.count(anchor2)
s = s.replace(anchor2, repl2)

anchor3 = """					if (nextLatPort.first % II != edgePath->steps[rpNext].second)
						continue;"""
repl3 = """					if (nextLatPort.first % II != edgePath->steps[rpNext].second)
					{
						if (getenv("ROUTEPATH_DEBUG"))
							std::cout << "ROUTEPATH: reject " << node->idx << "->" << currNode->idx
							          << " phase " << (nextLatPort.first % II) << " expected "
							          << edgePath->steps[rpNext].second << "\\n";
						continue;
					}"""
assert s.count(anchor3) == 1, s.count(anchor3)
s = s.replace(anchor3, repl3)

open(F_PF, "w", encoding="utf-8").write(s)
print("instrumented")
