#!/usr/bin/env python3
"""端到端验证：进化出来的放置，morpher 到底认不认？

这条链是唯一能证明「进化出的映射是真的」的路径：

    放置 {node: (x,y,t)}
      → pins.json
      → cgra_xml_mapper -m 3（PathFinder + 钉住候选集）   ← morpher 自己布线
      → bitstream
      → hycube_simulator                                  ← 数值对不对

我们自己的 router 只能回答「这个放置在我的抽象模型下布不布得出来」。
它不跑 morpher 的路由器，也不做数值仿真。两者可能分歧，分歧本身就是结论。

用法：
    python evo/verify_end_to_end.py --program evo/init.py
    python evo/verify_end_to_end.py --program <best>.py --kernel array_add
    python evo/verify_end_to_end.py --placement place.json --kernel mac

输出一张表：
    kernel | router | mapper | sim | 说明
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from evo import harness, router as router_mod  # noqa: E402


def run(cmd, timeout=900):
    p = subprocess.run(cmd, cwd=ROOT, shell=True, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def write_pins(place, path, phase_free=False):
    pins = []
    for node, val in sorted(place.items(), key=lambda kv: int(kv[0])):
        x, y, t = int(val[0]), int(val[1]), int(val[2])
        pins.append({"node": int(node), "x": x, "y": y, "t": -1 if phase_free else t})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pins": pins}, f, indent=1)
    return len(pins)


def read_report(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def verify_one(kernel, place, ii, mode=1, phase_free=False, mem=4096, timeout=900, tag=None):
    """返回一个 dict，字段都是可空的事实，不做推断。"""
    out = {"kernel": kernel, "ii": ii, "mode": mode, "phase_free": phase_free}
    prob = harness.load_problems([kernel])[kernel]
    out["n_nodes"] = prob["n_nodes"]

    # 1) 我们的 router 怎么说
    try:
        rt = router_mod.route(prob, place, ii)
        out["router_routable"] = bool(rt["ok"])
        out["router_detour"] = int(rt["detour"])
        out["router_unrouted"] = len(rt["unrouted"])
    except Exception as e:  # router 自己崩了也要记录，不能吞
        out["router_routable"] = None
        out["router_error"] = repr(e)[:200]

    # 2) 交给 morpher 布线
    pins_path = os.path.join(ROOT, "out", kernel, "pins_in_%s.json" % ("pf" if phase_free else "t"))
    os.makedirs(os.path.dirname(pins_path), exist_ok=True)
    out["pins_total"] = write_pins(place, pins_path, phase_free=phase_free)
    out["pins_path"] = pins_path

    e2e_tag = tag or ("pins%d%s" % (mode, "_pf" if phase_free else ""))
    rc, log = run("bash scripts/map_pinned.sh %s %s %d %d 4 4 %s"
                  % (kernel, pins_path, mode, ii, e2e_tag), timeout=timeout)
    out["map_rc"] = rc
    out["map_ok"] = "Map Success" in log
    rep = read_report(os.path.join(ROOT, "out", kernel, e2e_tag, "pins_report.json"))
    if rep:
        out["mapper_ok"] = bool(rep.get("ok"))
        out["mapper_stage"] = rep.get("stage")
        out["pins_applied"] = rep.get("pins_applied")
        out["pins_rejected"] = rep.get("pins_rejected")
        out["mapper_ii"] = rep.get("ii")
        if rep.get("error"):
            out["mapper_error"] = str(rep["error"])[:400]
    else:
        out["mapper_ok"] = False
        out["mapper_error"] = (log or "")[-400:]

    # 3) 只有布通了才仿真
    if out.get("mapper_ok"):
        rc, slog = run("bash scripts/sim.sh %s %d %d %s" % (kernel, mode, mem, e2e_tag), timeout=timeout)
        out["sim_rc"] = rc
        out["sim_ok"] = "Mismatches::0" in slog and "Matches::0," not in slog
        import re
        m = re.search(r"Matches::(\d+), Mismatches::(\d+)", slog)
        if m:
            out["matches"] = int(m.group(1))
            out["mismatches"] = int(m.group(2))
        m = re.search(r"CGRA cycles: (\d+)", slog)
        if m:
            out["cycles"] = int(m.group(1))
        if not m:
            out["sim_note"] = (slog or "")[-300:]
    else:
        out["sim_ok"] = None  # 没布通就没仿真，这是"未测"不是"失败"

    return out


def placement_from_program(program, kernels):
    probs = harness.load_problems(list(kernels))
    res = harness.run_program(program, probs)
    out = {}
    for k in kernels:
        prob = probs[k]
        tight = prob["tight"]
        slot = (res.get(k) or {}).get(tight) or {}
        if not slot.get("ok"):
            out[k] = None
        else:
            out[k] = (slot["place"], tight)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", help="可演化程序（取其 place() 的输出）")
    ap.add_argument("--placement", help='单个放置 JSON：{node: [x,y,t]}')
    ap.add_argument("--kernel", default=None, help="只验一个 kernel")
    ap.add_argument("--ii", type=int, default=None, help="覆盖 II（默认用该 kernel 的紧档）")
    ap.add_argument("--mode", type=int, default=1, help="1=strict 2=repair")
    ap.add_argument("--phase-free", action="store_true", help="只钉 (x,y)，相位交给 morpher")
    ap.add_argument("--mem", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--tag", default=None, help="产物目录后缀，默认 pins<mode>[_pf]")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    kernels = [a.kernel] if a.kernel else list(harness.KERNELS)

    if a.placement:
        with open(a.placement, encoding="utf-8") as f:
            raw = json.load(f)
        # 三种写法都认：{"pins":[...]}（map_pinned 的输入）、
        # {"placement":[...]}（mapper 的 report）、{node: [x,y,t]}（手写）
        if isinstance(raw.get("pins"), list):
            raw = {"placement": raw["pins"]}
        if isinstance(raw.get("placement"), list):
            place = {int(e["node"]): (e["x"], e["y"], e.get("t", -1)) for e in raw["placement"]}
        else:
            place = {int(k): tuple(v) for k, v in raw.items()}
        probs = harness.load_problems(kernels)
        jobs = [(k, place, a.ii or probs[k]["tight"]) for k in kernels]
    else:
        if not a.program:
            ap.error("需要 --program 或 --placement")
        got = placement_from_program(a.program, kernels)
        jobs = [(k, got[k][0], a.ii or got[k][1]) for k in kernels if got[k]]

    rows = []
    for k, place, ii in jobs:
        r = verify_one(k, place, ii, mode=a.mode, phase_free=a.phase_free,
                       mem=a.mem, timeout=a.timeout, tag=a.tag)
        rows.append(r)
        print("%-10s router=%-5s detour=%-4s mapper=%-5s sim=%-5s %s" % (
            k,
            r.get("router_routable"),
            r.get("router_detour"),
            r.get("mapper_ok"),
            r.get("sim_ok"),
            (r.get("mapper_error") or "")[:80] if not r.get("mapper_ok") else "",
        ))

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1, ensure_ascii=False)
        print("->", a.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
