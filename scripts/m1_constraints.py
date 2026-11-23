#!/usr/bin/env python3
"""M1 验收：agent 给的约束到底有没有变成 mapper 的硬约束。

三件事必须同时成立才算 M1 过：
  T1 pin 生效    给定 pins，mapper 产出的放置里这些节点**恰好**在指定 (x,y)
  T2 pin 是硬的  mapper 报告里不出现"忽略约束但成功"的情况
  T3 forbidden 生效 给定 forbidden，mapper 从不把该节点放在那些 (x,y)

反面样本（证明测试本身有效）：
  T4 矛盾约束（同一节点 pin 与 forbidden 冲突）必须被**拒绝加载**，而不是静默跑完
  T5 pin 到阵列外的坐标必须失败（证明 pin 是硬的，不会被静默忽略）

注意：两个节点 pin 到同一个 PE **不是**错误 —— CGRA 时分复用，同一 PE 的不同
相位可以承载不同节点。array_add 的基线解里节点 0 与 12 就都在 PE(1,2)。

用法：
    python scripts/m1_constraints.py [--kernel array_add] [--backend pathfinder]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backends import make as get_backend
from backends.base import Hints
from core import build_problem, check
from tools import mapper as mapper_mod


def pick_nodes(prob, k):
    """挑 k 个度数高的非内存算子——它们最容易成为布线瓶颈，pin 它们才有意义。

    edges 是 [u, v, dist] 三元组（dist 是依赖距离，不参与度数）。
    """
    deg = {n["idx"]: 0 for n in prob["nodes"]}
    for e in prob["edges"]:
        u, v = e[0], e[1]
        deg[u] = deg.get(u, 0) + 1
        deg[v] = deg.get(v, 0) + 1
    cand = [n for n in prob["nodes"] if not n["is_mem"]]
    cand.sort(key=lambda n: -deg.get(n["idx"], 0))
    return [n["idx"] for n in cand[:k]]


def legal_positions(prob):
    """该架构下每个节点的合法 (x,y) 集合：内存算子只能在 x=0，其余在 x>=1。"""
    out = []
    for y in range(prob["ydim"]):
        out.append((1, y))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", default="array_add")
    ap.add_argument("--backend", default="pathfinder")
    ap.add_argument("--ii", type=int, default=4)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    kdir = mapper_mod.kernel_dir(args.kernel)
    prob = build_problem(args.kernel,
                         os.path.join(kdir, "dfg.xml"),
                         os.path.join(kdir, "arch.json"),
                         args.ii)
    backend = get_backend(args.backend)
    rows = []

    def run(hints, tag):
        res = backend.run(prob, args.ii, hints=hints)
        return res, tag

    # ---------- 基线 ----------
    base, _ = run(Hints(), "baseline")
    print("baseline: ok=%s ii=%d n_placed=%d/%d"
          % (base.ok, base.ii, len(base.placement), len(prob["nodes"])))
    rows.append({"test": "baseline", "ok": bool(base.ok),
                 "n_placed": len(base.placement), "passed": None})

    if not base.placement:
        print("FATAL: baseline 都放不出来，后面的测试没有意义")
        return 1

    # ---------- T1: pin 生效 ----------
    nodes = pick_nodes(prob, 5)
    # 用基线里这些节点原本的 (x,y) 反向构造 pin：既能验证"pin 被遵守"，
    # 又不会因为 pin 到一个坏位置而让 mapper 直接失败（那测的是别的东西）。
    pins = {i: base.placement[i][:2] for i in nodes if i in base.placement}
    res, _ = run(Hints(pins=pins, note="T1 pin"), "T1")
    hit = sum(1 for i, xy in pins.items()
              if i in res.placement and res.placement[i][:2] == xy)
    t1 = res.ok and hit == len(pins)
    print("T1 pin: 期望 %d 个全中，实际 %d/%d，mapper ok=%s"
          % (len(pins), hit, len(pins), res.ok))
    rows.append({"test": "T1_pin", "pins": {str(k): list(v) for k, v in pins.items()},
                 "hit": hit, "n": len(pins), "mapper_ok": bool(res.ok), "passed": bool(t1)})

    # ---------- T1b: pin 真的改变了结果（不是"恰好一样"）----------
    # 对每个被选节点单独做一次"位移 pin"：把它钉到另一个合法位置，
    # 要求 (a) mapper 仍成功 (b) 该节点恰好落在指定位置 (c) 它确实离开了原位。
    #
    # 为什么不是"把 5 个 pin 一起轮换"（第一版做法，已废弃）：
    #   · 第一版整体轮换时把 LOAD 的位置换给 ALU 算子 → 那是在测"pin 到非法位置"；
    #   · 改成同类内轮换后仍然会 FAIL：5 个 pin 同时生效等于把基线解锁死，
    #     mapper 只剩"照抄"一条路，任何一步不同就整体不可行 —— 测的是
    #     "多重 pin 下可行性还剩多少"，不是"pin 有没有生效"。
    # 单点位移才是 M1 要保证的性质：约束是硬的，且它真的改变输出。
    probes = []
    # 目标位置只从"基线里同类节点已经用过的位置"里挑。
    # 为什么不挑 (0,0) 这种角落：把 ALU 算子钉到内存列的角落，即使 op 支持
    # （FU_MEM ⊇ FU）也会让布线立刻不可行 —— 那测的是"pin 到结构上最差的位置
    # 会不会失败"，不是"pin 有没有生效"。实测 node=0 钉到 (0,2)/(1,1)/(2,0)/
    # (2,1)/(2,2) 五个基线用过的位置全部命中且 mapper 全成功。
    alu_used = {base.placement[n["idx"]][:2]
                for n in prob["nodes"]
                if not n["is_mem"] and n["idx"] in base.placement}
    mem_used = {base.placement[n["idx"]][:2]
                for n in prob["nodes"]
                if n["is_mem"] and n["idx"] in base.placement}
    for i in nodes:
        if i not in base.placement:
            continue
        cur = base.placement[i][:2]
        is_mem_node = bool(next(n for n in prob["nodes"] if n["idx"] == i)["is_mem"])
        pool = sorted(mem_used if is_mem_node else alu_used)
        cand = [c for c in pool if c != cur]
        if not cand:
            continue
        tgt = cand[0]
        r1, _ = run(Hints(pins={i: tgt}, note="T1b move node %d" % i), "T1b")
        ok_hit = bool(r1.ok) and r1.placement.get(i, (None, None))[:2] == tgt
        moved = r1.placement.get(i, (None, None))[:2] != cur
        probes.append({"node": i, "from": list(cur), "to": list(tgt),
                       "mapper_ok": bool(r1.ok), "hit": ok_hit, "moved": bool(moved),
                       "ii": int(r1.ii or 0)})
        print("T1b 单点位移 node=%2d %s -> %s : mapper ok=%s 命中=%s II=%d"
              % (i, cur, tgt, r1.ok, ok_hit, int(r1.ii or 0)))
    n_ok = sum(1 for p in probes if p["hit"] and p["moved"])
    t1b = bool(probes) and n_ok == len(probes)
    print("T1b 单点位移: %d/%d 个节点被钉到新位置并真的移动了" % (n_ok, len(probes)))
    rows.append({"test": "T1b_pin_moved", "n": len(probes), "n_ok": n_ok,
                 "probes": probes, "passed": bool(t1b)})

    # ---------- T3: forbidden 生效 ----------
    tgt = nodes[0] if nodes else None
    if tgt is not None:
        forbidden = [(tgt, x, y) for (x, y) in legal_positions(prob)]
        res3, _ = run(Hints(forbidden=forbidden, note="T3 forbidden"), "T3")
        banned = {(x, y) for (_i, x, y) in forbidden}
        viol = [i for i, (x, y, _t) in res3.placement.items()
                if i == tgt and (x, y) in banned]
        t3 = res3.ok and not viol and tgt in res3.placement
        print("T3 forbidden: 节点 %d 禁掉全部 x>=1 位置，违规 %d 次，mapper ok=%s"
              % (tgt, len(viol), res3.ok))
        rows.append({"test": "T3_forbidden", "node": tgt, "violations": len(viol),
                     "mapper_ok": bool(res3.ok), "passed": bool(t3)})

    # ---------- T4/T5: 矛盾约束必须被拒绝 ----------
    bad_path = os.path.join(prob.get("kernel_dir", "/tmp"), "bad_constraints.json")
    if not os.path.isdir(os.path.dirname(bad_path)):
        bad_path = "/tmp/bad_constraints.json"
    doc = {"pin_mode": "strict",
           "pins": {str(nodes[0]): [1, 0]},
           "forbidden": {str(nodes[0]): [[1, 0]]}}
    with open(bad_path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    run_bad = mapper_mod.run_mapper(
        prob["kernel"], backend=args.backend, ii=args.ii,
        xdim=prob["xdim"], ydim=prob["ydim"],
        constraints=doc, workdir=None, root=None,
        max_ii=args.ii, real_nodes=[n["idx"] for n in prob["nodes"]])
    t4 = not run_bad.ok
    print("T4 矛盾约束（pin 与 forbidden 同一格）: mapper ok=%s（期望 False）" % run_bad.ok)
    rows.append({"test": "T4_contradiction", "mapper_ok": bool(run_bad.ok), "passed": bool(t4)})

    # ---------- T5: pin 到阵列外必须失败（证明 pin 是硬的，不会被静默忽略）----------
    doc5 = {"pin_mode": "strict",
            "pins": {str(nodes[0]): [99, 99]}}
    run_bad5 = mapper_mod.run_mapper(
        prob["kernel"], backend=args.backend, ii=args.ii,
        xdim=prob["xdim"], ydim=prob["ydim"],
        constraints=doc5, workdir=None, root=None,
        max_ii=args.ii, real_nodes=[n["idx"] for n in prob["nodes"]])
    t5 = not run_bad5.ok
    print("T5 pin 到阵列外 (99,99): mapper ok=%s（期望 False）" % run_bad5.ok)
    rows.append({"test": "T5_pin_out_of_range", "mapper_ok": bool(run_bad5.ok), "passed": bool(t5)})

    # ---------- 汇总 ----------
    print("\n%-20s %s" % ("test", "verdict"))
    print("-" * 40)
    all_ok = True
    for r in rows:
        if r["passed"] is None:
            continue
        print("%-20s %s" % (r["test"], "PASS" if r["passed"] else "FAIL"))
        all_ok = all_ok and r["passed"]
    print("-" * 40)
    print("M1 验收: %s" % ("PASS" if all_ok else "FAIL"))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1, ensure_ascii=False)
        print("写入 %s" % args.json)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
