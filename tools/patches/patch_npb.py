#!/usr/bin/env python3
"""幂等补丁：DFG.cpp 里 `int npb;` 未初始化。

问题：上游 cgra_me_bench 的 noSemiOLOAD DFG（`applications/cgra_me_bench/*/main_INNERMOST_LN1_
noSemiOLOAD_PartPred_DFG.xml`）里 `<Output type="P"/>` **没有 NPB 属性**。而
`src/mapper/src/dfg/DFG.cpp:230` 写的是裸的 `int npb;`，`QueryIntAttribute("NPB", &npb)` 失败时
tinyxml2 不写回，`npb` 保留栈上垃圾值，下一个 `assert(npb == 0 || npb == 1)` 直接 SIGABRT：
    cgra_xml_mapper: src/dfg/DFG.cpp:233: Assertion `npb == 0 || npb == 1' failed.

修法：初始化为 0（= 非 NPB，与"属性缺失"的语义一致）。
"""
import sys, pathlib

p = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "src/mapper/src/dfg/DFG.cpp")
s = p.read_text(encoding="utf-8", errors="replace")
old = "int npb;"
new = "int npb = 0;  // NPBHX: QueryIntAttribute leaves it untouched when NPB is absent"
if new in s:
    print("已经打过补丁")
    sys.exit(0)
if s.count(old) != 1:
    raise SystemExit("找不到唯一的 `int npb;`（实际 %d 处）" % s.count(old))
p.write_text(s.replace(old, new), encoding="utf-8")
print("已打补丁：", p)
