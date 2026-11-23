#!/usr/bin/env python3
"""evo/router.py —— 在时间扩展图上真正布一次线。

为什么需要它：`check.py` 算的 cost 只是"绕路的静态下界"，
它假设每个 PE 的每条链路在每个相位都能随便用。实测这个假设不成立：

  * morpher 自己的真解在 check.py 下 cost = 36 / 44 / 18，
    而我们的贪心基线只有 12 / 21 / 13 —— 静态指标说我们比真解好，
    这不可信。原因是 morpher 的解是**展开过的**绝对调度（mac 的 t 到 11），
    折回 mod II 之后跳数自然变大，两边根本不是一个口径。

所以静态分只能在同一套口径内部排序。要判断"这个放置到底能不能布出来"，
必须真的在时间扩展图上搜路径。

模型（照 HyCUBE 4x4 的 ADL）：
  * 单元 = (x, y)，x == 0 那一列是 PE_MEM；
  * 链路 = 网格四邻，端口 NORTH_O -> (x, y-1).SOUTH_I 等；
  * 一跳耗 1 拍。给一条边 p->c，父节点第 t_p 拍出结果，算子延迟 lat_p，
    那么需要在 h 跳后到达 c，且 h ≡ (t_c - t_p - lat_p) (mod II)、h >= 曼哈顿距离。
    多出来的圈就是绕路。
  * 容量：同一条链路在同一个相位（mod II）只能走一条边。这是真正的资源约束。

搜索：逐边 DFS，允许 h = h0 + k*II（k 小），沿途记录 (链路, 相位) 占用，
遇到冲突就换路；全部边布完即成功。失败时报出布不出来的边。
"""
import sys

MAX_EXTRA_LAPS = 3        # 允许最多多绕几圈去找空链路
MAX_STEPS = 200000        # DFS 步数上限，防止退化


def neighbours(prob):
    """{(x,y): [((nx,ny), port), ...]}，只连阵列内的邻居。"""
    xdim, ydim = prob["xdim"], prob["ydim"]
    adj = {}
    for x in range(xdim):
        for y in range(ydim):
            outs = []
            for dx, dy, port in ((0, -1, "N"), (1, 0, "E"), (-1, 0, "W"), (0, 1, "S")):
                nx, ny = x + dx, y + dy
                if 0 <= nx < xdim and 0 <= ny < ydim:
                    outs.append(((nx, ny), port))
            adj[(x, y)] = outs
    return adj


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class _Budget:
    def __init__(self, steps):
        self.left = steps

    def spend(self):
        self.left -= 1
        return self.left > 0


def _find_walk(adj, src, dst, hops, phase0, ii, used, budget):
    """找一条恰好 hops 跳、从 src 到 dst 的走法，且沿途 (链路, 相位) 都空闲。

    返回 [(链路, 相位), ...] 或 None。
    """
    path = []

    def dfs(cur, remaining, phase):
        if not budget.spend():
            return False
        if remaining == 0:
            return cur == dst
        # 剪枝：剩余跳数必须够走到 dst，且奇偶/步数不能差太多
        d = _dist(cur, dst)
        if d > remaining:
            return False
        # 先试真正移动，最后试原地等一拍（数据压在寄存器里，不占链路）。
        # 没有"等"这一档时，h 的奇偶必须等于曼哈顿距离的奇偶，
        # 于是 II=4 的偶数模会把一半的边判成不可布 —— 实测 morpher 自己的
        # 真解就过不了，说明模型缺了等待这一维。
        for nxt, _port in adj[cur] + [(cur, None)]:
            if nxt == cur:
                path.append(None)
                if dfs(cur, remaining - 1, phase + 1):
                    return True
                path.pop()
                continue
            slot = ((cur, nxt), phase % ii)
            if slot in used:
                continue
            used[slot] = None
            path.append(slot)
            if dfs(nxt, remaining - 1, phase + 1):
                return True
            path.pop()
            used.pop(slot, None)
        return False

    if dfs(src, hops, phase0):
        return list(path)
    return None


def route(prob, place, ii, max_extra=MAX_EXTRA_LAPS, budget_steps=MAX_STEPS):
    """给整个放置布线。

    place: {node_idx: (x, y, t)}
    返回 {"ok", "unrouted": [...], "hops_total", "detour", "stats"}。
    """
    nodes = {n["idx"]: n for n in prob["nodes"]}
    pe_mem = {tuple(p) for p in prob["pe_mem"]}
    alu_lat = prob.get("alu_lat_map") or {}
    mem_lat = prob.get("mem_lat_map") or {}
    adj = neighbours(prob)

    def lat_of(idx):
        x, y, _t = place[idx]
        op = nodes[idx]["op"]
        if (x, y) in pe_mem:
            return mem_lat.get(op) or alu_lat.get(op) or 1
        return alu_lat.get(op) or mem_lat.get(op) or 1

    # 按"可用跳数余量"从小到大排：余量小的先布，容易被逼死的先照顾
    edges = []
    for p, c, _npb in prob["edges"]:
        if p not in place or c not in place:
            continue
        src = (place[p][0], place[p][1])
        dst = (place[c][0], place[c][1])
        d = _dist(src, dst)
        need = (place[c][2] - place[p][2] - lat_of(p)) % ii
        h0 = d + ((need - d) % ii)
        edges.append((h0 - d, p, c, src, dst, d, h0))
    edges.sort()

    used = {}
    unrouted = []
    hops_total = 0
    detour_total = 0
    budget = _Budget(budget_steps)

    for _slack, p, c, src, dst, d, h0 in edges:
        placed_path = None
        for k in range(max_extra + 1):
            hops = h0 + k * ii
            if hops > d + max_extra * ii + ii:
                break
            path = _find_walk(adj, src, dst, hops, place[p][2] + lat_of(p), ii, used, budget)
            if path is not None:
                placed_path = path
                break
            if budget.left <= 0:
                break
        if placed_path is None:
            unrouted.append((p, c))
            continue
        for slot in placed_path:
            if slot is not None:
                used[slot] = (p, c)
        hops_total += len(placed_path)
        detour_total += len(placed_path) - d

    return {
        "ok": not unrouted,
        "unrouted": unrouted,
        "hops_total": hops_total,
        "detour": detour_total,
        "stats": {"edges": len(edges), "routed": len(edges) - len(unrouted),
                  "slots_used": len(used)},
    }


def main(argv):
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from evo import harness, verify_against_mapper as V
    probs = harness.load_problems(list(harness.KERNELS))
    print("%-11s %-6s %-6s %-7s %-7s" % ("kernel", "legal", "routable", "hops", "detour"))
    print("-" * 50)
    for k in harness.KERNELS:
        prob = probs[k]
        valid = {x["idx"] for x in prob["nodes"]}
        place, ii, _src = V.find_solution(k, valid)
        if not place:
            print("%-11s no artifact" % k)
            continue
        r = route(prob, place, prob["tight"])
        print("%-11s %-6s %-6s %-7d %-7d" % (k, "-", r["ok"], r["hops_total"], r["detour"]))
        for e in r["unrouted"][:3]:
            print("    unrouted: %s" % (e,))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
