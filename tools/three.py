"""三裁判分类：mapper（有没有落出 bitstream）/ verify / sim。

两裁判的 2x2 放不下 `mapper_ok / verify_reject / sim_abort` 这种"两个静态裁判先打架"
的格子，所以这里按三格重新分：

  mapper: ok / reject(no bitstream)
  verify: ok / reject
  sim:    pass / mismatch / abort / none
"""

import csv
import os
from collections import Counter

FILES = {
    "50格·mode1": "/home/steve/cac-armB/docs/arm_b_confusion_m1_strict.csv",
    "50格·mode2": "/home/steve/cac-armB/docs/arm_b_confusion_m2_prefer.csv",
    "50格·mode3": "/home/steve/cac-armB/docs/arm_b_confusion_50cells.csv",
    "LLM·mode1": None,
    "LLM·mode2": None,
    "LLM·mode3": None,
}
LLM_CSV = "/home/steve/cac-armB/docs/arm_b_confusion_llm.csv"


def classify3(r):
    m = "reject" if r["cell"] == "no_mapping" else "ok"
    v = "ok" if r["verify_ok"] == "True" else "reject"
    sv = r["sim_verdict"]
    if sv in ("no-sim", ""):
        s = "none"
    elif sv == "abort":
        s = "abort"
    elif sv.startswith("mm:"):
        s = "pass" if sv == "mm:0" else "mismatch"
    else:
        s = sv
    return (m, v, s)


def label(k):
    m, v, s = k
    if m == "reject":
        return "mapper_reject（无 bitstream）"
    if v == "reject" and s in ("abort", "mismatch"):
        return "mapper_ok / verify_reject / sim_fail"
    if v == "reject" and s == "pass":
        return "mapper_ok / verify_reject / sim_pass（静态误杀）"
    if v == "ok" and s in ("abort", "mismatch"):
        return "mapper_ok / verify_ok / sim_fail（静态漏报）"
    if v == "ok" and s == "pass":
        return "mapper_ok / verify_ok / sim_pass"
    if v == "ok" and s == "none":
        return "mapper_ok / verify_ok / 无仿真"
    return "%s/%s/%s" % (m, v, s)


def main():
    llm = list(csv.DictReader(open(LLM_CSV)))
    for mode in ("1", "2", "3"):
        FILES["LLM·mode%s" % mode] = [r for r in llm if r["mode"] == mode]
    out = []
    for name, src in FILES.items():
        rows = list(csv.DictReader(open(src))) if isinstance(src, str) else src
        c = Counter(label(classify3(r)) for r in rows)
        out.append((name, len(rows), c))
        print("== %s (%d 格)" % (name, len(rows)))
        for k, n in c.most_common():
            print("   %-46s %d" % (k, n))
    return out


if __name__ == "__main__":
    main()
