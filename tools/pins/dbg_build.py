#!/usr/bin/env python3
"""Temporary: name the first failing lookup inside buildPath."""
import os
import sys

ROOT = os.path.expanduser("~/acc-thu-ilp")
F_PF = os.path.join(ROOT, "src/mapper/src/mapper/PathFinderMapper.cpp")
s = open(F_PF, encoding="utf-8", errors="replace").read()
if "BUILDPATH: fail at" in s:
    print("already instrumented")
    sys.exit(0)

anchor = """			if (nm.compare(nm.size() - suffix.size(), suffix.size(), suffix) == 0)
			{
				to = cand;
				return true;
			}
		}
		return false;
	};"""
repl = """			if (nm.compare(nm.size() - suffix.size(), suffix.size(), suffix) == 0)
			{
				to = cand;
				return true;
			}
		}
		if (getenv("ROUTEPATH_DEBUG"))
		{
			std::cout << "BUILDPATH: fail at " << from.second->getFullName()
			          << " looking for " << suffix << " lat+" << latDelta << " among "
			          << nexts.size() << ":";
			for (LatPort cand : nexts)
				std::cout << " " << cand.first << "/" << cand.second->getName();
			std::cout << "\\n";
		}
		return false;
	};"""
if s.count(anchor) != 1:
    sys.exit("anchor found %d times" % s.count(anchor))
s = s.replace(anchor, repl)
open(F_PF, "w", encoding="utf-8").write(s)
print("instrumented buildPath")
