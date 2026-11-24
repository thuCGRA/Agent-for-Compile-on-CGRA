#!/usr/bin/env python
"""M2 验收：Auditor 的归因能不能把失败分到对的门和类。

设计原则：**先考分类题，再考真现场**。
- 前 10 题是"注入失败"：构造一份与真实产物同形的 Attempt（gates/reason 都是
  仿真器/check 真会打出来的字符串），看 audit() 是否给出预期的 gate+cls。
- 后 2 题是真现场（`--live`）：真跑一次 mac/sa（已知会触发 CGRATile.cpp 断言）
  和一次越界 pin（已知会让 mapper 拒绝），看归因是否对得上。

为什么不一上来就用真现场考：真跑一次 SA 要 20–30 s，10 道题就是 5 分钟，
而且我们只有 3 个 kernel，"考的真"不等于"考的全"。注入题覆盖的是
audit 的判定表，真现场覆盖的是判定表和真实错误串之间没有走样。

用法：
    python scripts/m2_audit.py              # 只跑 10 道注入题
    python scripts/m2_audit.py --live       # 再加 2 道真现场
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from agents.audit import audit                                  # noqa: E402
from gates import Attempt, GateResult                           # noqa: E402


def _att(gate: str, reason: str, detail: dict = None, backend: str = "sa",
         err: str = "") -> Attempt:
    gates = {g: GateResult(gate=g, passed=True) for g in ("G1", "G2", "G3", "G4")}
    gates[gate] = GateResult(gate=gate, passed=False, reason=reason,
                             detail=detail or {})
    a = Attempt(kernel="probe", backend=backend, ii=4, gates=gates)
    if err:
        a.artifacts["backend_error"] = err
    return a


CASES = [
    # (说明, attempt, 期望 gate, 期望 cls 前缀)
    ("SELECT 双操作数同拍（168）",
     _att("G1", "hycube_simulator: CGRATile.cpp:168: Assertion '!(I1isvalid && I2isvalid)' failed."),
     "G1", "select_same_cycle"),
    ("SELECT 双操作数同拍（175）",
     _att("G1", "CGRATile.cpp:175: Assertion failed."),
     "G1", "select_same_cycle"),
    ("输入寄存器被覆盖（352）",
     _att("G1", "CGRATile.cpp:352: Assertion `!(regwen && regwen_prev)' failed."),
     "G1", "input_reg_overwrite"),
    ("输入寄存器被覆盖（364）",
     _att("G1", "CGRATile.cpp:364: Assertion failed."),
     "G1", "input_reg_overwrite"),
    ("未知 opcode（285）",
     _att("G1", "CGRATile.cpp:285: unknown opcode 41"),
     "G1", "unknown_opcode"),
    ("trace 缺失（214）",
     _att("G1", "CGRA.cpp:214: cannot open trace file"),
     "G1", "missing_trace"),
    ("仿真失配（无断言）",
     _att("G1", "仿真未通过：Matches=243, Mismatches=17"),
     "G1", "sim_mismatch"),
    ("后端没产出二进制",
     _att("G1", "后端没有产出二进制，无法仿真", err="mapper 没有可读的放置产物"),
     "G1", "no_binary"),
    ("资源冲突：槽位 + 算子",
     _att("G2", "2 resource problem(s)",
          detail={"errors": [{"code": "slot_conflict", "node": 3},
                             {"code": "op_unsupported", "node": 7},
                             {"code": "slot_conflict", "node": 9}]}),
     "G2", "resource:"),
    ("mapper 拒绝这份放置",
     _att("G3", "mapper 没有接受这份放置（它自己另找了或直接失败）"),
     "G3", "mapper_rejected"),
]


def run_injected() -> dict:
    rows, n_ok = [], 0
    for why, att, want_gate, want_cls in CASES:
        got = audit(att)
        ok = got["gate"] == want_gate and got["cls"].startswith(want_cls)
        n_ok += 1 if ok else 0
        rows.append({"case": why, "want": "%s/%s" % (want_gate, want_cls),
                     "got": "%s/%s" % (got["gate"], got["cls"]),
                     "ok": ok, "suspects": got["suspects"],
                     "advice": got["advice"][:1]})
    return {"rows": rows, "ok": n_ok, "total": len(CASES)}


def run_live() -> dict:
    """真现场两题：mac/sa 的 SELECT 断言、越界 pin 被 mapper 拒绝。"""
    from agents.engine import run_attempt
    out = []

    a1 = run_attempt("mac", 4, "sa", params={"optimize": True, "budget": 1000},
                     do_sim=True)
    d1 = audit(a1)
    out.append({"case": "真现场：mac/sa 仿真", "ok_att": a1.ok,
                "want": "select_same_cycle 或 sim_mismatch",
                "got": "%s/%s" % (d1["gate"], d1["cls"]),
                "ok": d1["cls"] in ("select_same_cycle", "sim_mismatch"),
                "reason": (d1["reason"] or "")[:120]})

    a2 = run_attempt("array_add", 4, "pathfinder", pins={0: [99, 99]},
                     do_sim=False, params={"max_ii": 4})
    d2 = audit(a2)
    out.append({"case": "真现场：越界 pin", "ok_att": a2.ok,
                "want": "G1/G3 的后端拒绝",
                "got": "%s/%s" % (d2["gate"], d2["cls"]),
                "ok": (not a2.ok) and d2["gate"] in ("G1", "G3"),
                "reason": (d2["reason"] or "")[:120]})
    return {"rows": out, "ok": sum(1 for r in out if r["ok"]), "total": len(out)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    res = run_injected()
    for r in res["rows"]:
        print("%-4s %-28s want=%-22s got=%-30s %s"
              % ("OK" if r["ok"] else "FAIL", r["case"][:28], r["want"],
                 r["got"], r["suspects"] or ""))
    print("\n注入题: %d/%d 归因正确" % (res["ok"], res["total"]))
    verdict = res["ok"] >= 8
    print("M2 验收（注入题 ≥8/10）: %s" % ("PASS" if verdict else "FAIL"))

    payload = {"injected": res, "verdict": verdict}
    if args.live:
        live = run_live()
        print("\n---- 真现场 ----")
        for r in live["rows"]:
            print("%-4s %-22s want=%-28s got=%-24s %s"
                  % ("OK" if r["ok"] else "FAIL", r["case"], r["want"],
                     r["got"], r["reason"]))
        print("真现场: %d/%d" % (live["ok"], live["total"]))
        payload["live"] = live

    if args.json:
        import json
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("写入 %s" % args.json)


if __name__ == "__main__":
    main()
