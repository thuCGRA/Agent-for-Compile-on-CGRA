#!/usr/bin/env python3
"""把 morpher 自己布出来的解放进 check.py，看静态判据和真解是否同一量级。

这是 evo 适应度的**校准实验**：如果连 morpher 的真实解在 check.py 下都不合法，
那说明静态模型和硬件约束对不上，分数不能用来排序候选。

数据来源（按可靠性排序）：
  1. `*.routeInfo.log` 里的 `node=<idx>,mapped=PE[_MEM]_X<x>_Y<y>-T<t>`
     —— 唯一在三个 kernel 上都非空的完整放置记录；
  2. `*.mappingwithlatency.txt` 的 `node,x,y,t` —— 常常是 0 字节，只能当兜底。

注意 t 是**绝对调度时刻**，check.py 内部会折成 mod II 的相位。
比较只在解本身被求出的那个 II 上做：把 II=4 的解折到 II=3 必然撞槽位，
那不是判据错，是问题变了。

用法：
    python3 evo/verify_against_mapper.py [kernel ...]
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from evo import check, harness

# 两种写法：ALU 是 `PE_X1|_Y2|-T2`，PE_MEM 是 `PE_MEM_X0_Y2-T0`，
# 差别只在 y 后面那个分隔符是 `|` 还是空。
ROUTE_RE = re.compile(r"node=(\d+),mapped=PE(?:_MEM)?_X(\d+)\|?_Y(\d+)\|?-T(\d+)")
TXT_RE = re.compile(r"^(\d+),(\d+),(\d+),(\d+)$")


def parse_route_log(path):
    place = {}
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            m = ROUTE_RE.search(line)
            if m:
                place[int(m.group(1))] = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
    return place


def parse_latency_txt(path):
    place = {}
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            m = TXT_RE.match(line.strip())
            if m:
                place[int(m.group(1))] = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
    return place


def find_solution(kernel, valid_ids):
    """在 out/<kernel>/m*/ 下找 mapper 的放置，返回 (place, ii, source)。"""
    kdir = os.path.join(harness.root_dir(), "out", kernel)
    if not os.path.isdir(kdir):
        return None, None, None
    best = (None, None, None)
    for sub in sorted(os.listdir(kdir)):
        d = os.path.join(kdir, sub)
        if not os.path.isdir(d) or not sub.startswith("m"):
            continue
        ii = None
        res = os.path.join(d, "result.txt")
        if os.path.isfile(res):
            parts = open(res, errors="replace").read().split()
            for p in parts:
                if p.isdigit():
                    ii = int(p)
                    break
        # 优先 routeInfo.log：第 4 列是 PE 的相位（0..II-1），正是 pin 需要的语义。
        # mappingwithlatency.txt 第 4 列是**绝对调度时刻**（mac 上能到 11），
        # 必须 % II 才是相位 —— 两个文件语义不同，混用会让 pin 全错。
        for want in ("routeInfo", "latency"):
            for name in sorted(os.listdir(d)):
                path = os.path.join(d, name)
                place = {}
                if want == "routeInfo" and name.endswith(".routeInfo.log"):
                    place = parse_route_log(path)
                elif want == "latency" and name.endswith(".mappingwithlatency.txt"):
                    place = {n: (v[0], v[1], v[2] % (ii or 4))
                             for n, v in parse_latency_txt(path).items()}
                else:
                    continue
                # 路由日志里混着内部节点，只留 DFG 里真的有的 idx
                place = {k: v for k, v in place.items() if k in valid_ids}
                if len(place) == len(valid_ids):
                    return place, ii or 4, os.path.join(sub, name)
                if place and best[0] is None:
                    best = (place, ii or 4, os.path.join(sub, name))
    return best


def main(argv):
    kernels = argv[1:] or list(harness.KERNELS)
    probs = harness.load_problems(kernels)
    print(f"{'kernel':<11} {'nodes':>5} {'found':>5} {'II':>3} {'legal':>6} {'cost':>7}  source")
    print("-" * 78)
    ok_cnt = 0
    for k in kernels:
        prob = probs.get(k)
        if prob is None:
            print(f"{k:<11}  (no out/{k}/dfg.xml)")
            continue
        n = len(prob["nodes"])
        valid = {x["idx"] for x in prob["nodes"]}
        place, ii, src = find_solution(k, valid)
        if not place:
            print(f"{k:<11} {n:>5} {'-':>5} {'-':>3} {'-':>6} {'-':>7}  no artifact")
            continue
        r = check.check(prob, place, ii=ii)
        if r["ok"]:
            ok_cnt += 1
        print(f"{k:<11} {n:>5} {len(place):>5} {ii:>3} {str(r['ok']):>6} "
              f"{r['cost']:>7.1f}  {src}")
        for e in r["errors"][:4]:
            print(f"    error: {e}")
    print("-" * 78)
    print(f"morpher 的真实解被 check.py 判为合法: {ok_cnt}/{len(kernels)}")
    return 0 if ok_cnt == len(kernels) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
