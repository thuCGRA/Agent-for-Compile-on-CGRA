#!/usr/bin/env python3
"""Temporary: name the A* start/end ports so they can be compared with routeInfo."""
import os
import re
import sys

ROOT = os.path.expanduser("~/acc-thu-ilp")
F_PF = os.path.join(ROOT, "src/mapper/src/mapper/PathFinderMapper.cpp")
s = open(F_PF, encoding="utf-8", errors="replace").read()
if "ROUTEPATH: ports" in s:
    print("already instrumented")
    sys.exit(0)

pat = re.compile(r'^(\s*)Port \*destPort = node->rootDP->getInPort\(parent->getOPtype\(node\)\);$', re.M)
m = pat.search(s)
if not m:
    sys.exit("destPort line not found")
ind = m.group(1)
add = (
    "\n" + ind + 'if (getenv("ROUTEPATH_DEBUG"))'
    "\n" + ind + '    std::cout << "ROUTEPATH: ports " << parent->idx << "->" << node->idx'
    "\n" + ind + '              << " src=" << startPort->getFullName()'
    "\n" + ind + '              << " op=" << parent->getOPtype(node)'
    "\n" + ind + '              << " dst=" << (destPort ? destPort->getFullName() : "NULL") << "\\n";'
)
s = s[: m.end()] + add + s[m.end():]
open(F_PF, "w", encoding="utf-8").write(s)
print("instrumented ports")
