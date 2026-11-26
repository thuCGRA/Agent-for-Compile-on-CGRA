#!/usr/bin/env python3
"""Temporary: name the failing stage inside buildPath."""
import os
import sys

ROOT = os.path.expanduser("~/acc-thu-ilp")
F_PF = os.path.join(ROOT, "src/mapper/src/mapper/PathFinderMapper.cpp")
s = open(F_PF, encoding="utf-8", errors="replace").read()
if "BUILDPATH: stage" in s:
    print("already instrumented")
    sys.exit(0)

subs = [
    ('\t\tif (!intraPE(cur, "DP0_T", seg)) // leave the producer FU\n\t\t\treturn false;',
     '\t\tif (!intraPE(cur, "DP0_T", seg)) // leave the producer FU\n\t\t{\n\t\t\tif (getenv("ROUTEPATH_DEBUG")) std::cout << "BUILDPATH: stage=leaveFU " << cur.second->getFullName() << "\\n";\n\t\t\treturn false;\n\t\t}'),
    ('\t\t\t\tif (!intraPE(cur, dirOut(dx, dy), seg)) // walk to the facing output\n\t\t\t\t\treturn false;',
     '\t\t\t\tif (!intraPE(cur, dirOut(dx, dy), seg)) // walk to the facing output\n\t\t\t\t{\n\t\t\t\t\tif (getenv("ROUTEPATH_DEBUG")) std::cout << "BUILDPATH: stage=toOut " << cur.second->getFullName() << " want " << dirOut(dx, dy) << "\\n";\n\t\t\t\t\treturn false;\n\t\t\t\t}'),
    ('\t\t\t\tif (!crossed)\n\t\t\t\t\treturn false;',
     '\t\t\t\tif (!crossed)\n\t\t\t\t{\n\t\t\t\t\tif (getenv("ROUTEPATH_DEBUG")) std::cout << "BUILDPATH: stage=cross " << cur.second->getFullName() << " want " << dirIn(dx, dy) << "\\n";\n\t\t\t\t\treturn false;\n\t\t\t\t}'),
    ('\t\t\t\tif (!intraPE(cur, reg, seg))\n\t\t\t\t\treturn false;',
     '\t\t\t\tif (!intraPE(cur, reg, seg))\n\t\t\t\t{\n\t\t\t\t\tif (getenv("ROUTEPATH_DEBUG")) std::cout << "BUILDPATH: stage=toReg " << cur.second->getFullName() << " want " << reg << "\\n";\n\t\t\t\t\treturn false;\n\t\t\t\t}'),
    ('\t\t\t\tif (!got)\n\t\t\t\t\treturn false;\n\t\t\t\tcur = rin;',
     '\t\t\t\tif (!got)\n\t\t\t\t{\n\t\t\t\t\tif (getenv("ROUTEPATH_DEBUG")) std::cout << "BUILDPATH: stage=regIn " << cur.second->getFullName() << "\\n";\n\t\t\t\t\treturn false;\n\t\t\t\t}\n\t\t\t\tcur = rin;'),
    ('\t\tif (!intraPE(cur, "DP0_" + optype, seg))\n\t\t\treturn false;',
     '\t\tif (!intraPE(cur, "DP0_" + optype, seg))\n\t\t{\n\t\t\tif (getenv("ROUTEPATH_DEBUG")) std::cout << "BUILDPATH: stage=toDPin " << cur.second->getFullName() << " want DP0_" << optype << "\\n";\n\t\t\treturn false;\n\t\t}'),
]
for old, new in subs:
    if s.count(old) != 1:
        sys.exit("anchor %d: %r" % (s.count(old), old[:60]))
    s = s.replace(old, new)
open(F_PF, "w", encoding="utf-8").write(s)
print("instrumented stages")
