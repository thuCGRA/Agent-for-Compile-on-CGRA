#!/usr/bin/env python3
"""修 `--route-cycles` 的默认值：把"每条边 d<=1"换成"d <= manhattan+1"。

背景：`docs/06-algo-sweep.md` 里量到 CP-SAT 放置模型在 gemm(II=9) 上 INFEASIBLE，而同一
图在 `--spatial --hops 8 --route-cycles 3` 下 FEASIBLE(hops=124)。根因是 `delay_values`
是一个**全局**常量列表，`--route-cycles 1` 把每条边的 d 都卡在 {0,1}；但长边（跨 3..8 跳）
真实需要的寄存器拍数远超 1。而 `ilp_mapper.py` 的头注释本来就写着 `d` 上界是
`manhattan(u,v) + 1` —— 代码没实现它。

修法：把全局 `delay_values` 换成按边计算的 `delay_options(hh)`，`--route-cycles` 默认 -1
（auto = d <= min(hh+1, II-1)，另加 II-1 回绕候选）。
"""
import sys, pathlib

p = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "tools/ilp/ilp_mapper.py")
s = p.read_text(encoding="utf-8")

OLD1 = '''    # Admissible extra route delays.  0 = combinational, 1 = one register.
    # `route_cycles_max >= 2` also admits II-1, the -1 wrap seen on trmm.
    if timing == "register":
        # A route of h hops crosses h registers, and even a same-PE transfer
        # crosses one, so the admissible delays are exactly 1..max_hops.
        delay_values = list(range(1, max_hops + 1))
    elif route_cycles_max <= 0:
        delay_values = [0]
    elif route_cycles_max == 1:
        delay_values = [0, 1]
    else:
        delay_values = sorted({0, 1, ii - 1})
'''

NEW1 = '''    # Admissible extra route delays, computed **per edge** because the bound
    # depends on the hop count:
    #   route_cycles_max < 0 (auto) -> d <= min(manhattan+1, II-1), plus the II-1
    #       wrap seen on multi-cycle memory ops (trmm).  This is the bound the
    #       module docstring always claimed; capping every edge at 1 made long
    #       edges (3..8 hops) infeasible on the bigger benchmarks.
    #   == 0 -> {0}   (combinational only)
    #   == 1 -> {0,1} (what the mapper's own solutions use on small kernels)
    #   >= 2 -> {0,1,II-1}
    #   timing == "register" -> exactly max(h,1)
    def delay_options(hh):
        if timing == "register":
            return [max(hh, 1)]
        if route_cycles_max < 0:
            hi = min(hh + 1, ii - 1)
            d = list(range(0, hi + 1))
            if ii > 2 and (ii - 1) not in d:
                d.append(ii - 1)
            return d
        if route_cycles_max <= 0:
            return [0]
        if route_cycles_max == 1:
            return [0, 1]
        return sorted({0, 1, ii - 1})
'''

OLD2 = '''                        for dd in delay_values:'''
NEW2 = '''                        for dd in delay_options(hh):'''

OLD3 = '''    ap.add_argument("--route-cycles", type=int, default=1,
                    help="max cycles the route itself may consume (0 = combinational only)")'''
NEW3 = '''    ap.add_argument("--route-cycles", type=int, default=-1,
                    help="max cycles the route itself may consume; -1 (default) = "
                         "auto, d <= min(manhattan+1, II-1); 0 = combinational only")'''

n = 0
for old, new in ((OLD1, NEW1), (OLD2, NEW2), (OLD3, NEW3)):
    if new in s and new != old:
        continue
    if s.count(old) != 1:
        raise SystemExit("锚点不唯一或不存在（%d 处）：%r" % (s.count(old), old[:60]))
    s = s.replace(old, new)
    n += 1
p.write_text(s, encoding="utf-8")
print("已打补丁 %d 处：" % n, p)
