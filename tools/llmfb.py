"""模型对结构化反馈是否敏感：call1 -> call2 到底改了什么。

A 的 call2 prompt 里确实带 `## Feedback on the previous attempt`，且点名了
"no candidate DP matches pin (x,y,t) for node N"，还列出 estimateRouting 提供过的
10 个候选 (x,y,t)。这里检查：

1. 被点名的那个节点，call2 有没有挪进候选集；
2. 整体改了多少个节点；
3. 最终写入 `repair/pins.json` 的是不是 call2（`strict/pins.json` 是不是也被覆盖了）。
"""

import json
import re
import os

ROOT = "/home/steve/CGRA_Agent_Compiler/work/eval-llm"
KERNELS = ["array_add", "array_cond", "trmm", "conv3", "gemm", "conv2d", "gemm_pace",
           "hpcg", "mac", "mults1"]
CAND_RE = re.compile(r"\((\d+),(\d+),t(\d+)\)")
PIN_RE = re.compile(r"pin \(x=(\d+),y=(\d+),t=(\d+)\) for node (\d+)")


def pins(text):
    try:
        d = json.loads(text)
    except Exception:
        return None
    return d.get("pins") if isinstance(d, dict) else None


def key(p):
    return (int(p["node"]), int(p["x"]), int(p["y"]), int(p["t"]))


def main():
    print("%-10s %-5s %-9s %-9s %-5s %-6s %s" % (
        "kernel", "call2", "flagged", "moved-in", "chg", "in-cand", "strict==call1?"))
    for k in KERNELS:
        d1 = os.path.join(ROOT, k, "llm", "call1.json")
        d2 = os.path.join(ROOT, k, "llm", "call2.json")
        if not os.path.exists(d2):
            print("%-10s %-5s %s" % (k, "no", "(single call)"))
            continue
        c1, c2 = json.load(open(d1)), json.load(open(d2))
        p1, p2 = pins(c1["response"]), pins(c2["response"])
        user = [m for m in c2["messages"] if m["role"] == "user"][-1]["content"]
        m = PIN_RE.search(user)
        cands = {(int(x), int(y), int(t)) for x, y, t in CAND_RE.findall(user)}
        flagged = int(m.group(4)) if m else -1
        p1m = {int(p["node"]): (int(p["x"]), int(p["y"]), int(p["t"])) for p in (p1 or [])}
        p2m = {int(p["node"]): (int(p["x"]), int(p["y"]), int(p["t"])) for p in (p2 or [])}
        moved = sum(1 for n in set(p1m) | set(p2m) if p1m.get(n) != p2m.get(n))
        fixed = "n/a"
        in_cand = "n/a"
        if flagged in p2m:
            fixed = "n/a"
            in_cand = "yes" if p2m[flagged] in cands else "no"
        strict = json.load(open(os.path.join(ROOT, k, "strict", "pins.json")))["pins"]
        repair = json.load(open(os.path.join(ROOT, k, "repair", "pins.json")))["pins"]
        same_strict_c1 = (strict == p1)
        same_strict_rep = (strict == repair)
        print("%-10s %-5s %-9s %-9s %-5d %-6s strict==call1:%s repair==call2:%s strict==repair:%s" % (
            k, "yes", "node%d" % flagged if flagged >= 0 else "-", "", moved, in_cand,
            same_strict_c1, repair == p2, same_strict_rep))


if __name__ == "__main__":
    main()
