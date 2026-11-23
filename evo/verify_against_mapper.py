#!/usr/bin/env python3
"""把 morpher 自己布出来的解代回 check.py，看静态适应度和真解是否同一量级。

用途：evo 的适应度只是绕路的静态下界，必须证明它没有离谱。
如果 mapper 的真实解在 check.py 下也是"合法 + 低 cost"，
那 check.py 至少是一个可信的排序器；如果真解被判非法，
说明 check.py 的模型和 morpher 的约束不一致，分数不可用。

用法：
    python3 evo/verify_against_mapper.py [kernel ...]
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from evo import cgra, check, harness

# mapping.csv 的行形如：
#   current node = 11,op = LOADB,unmapped nodes = 19,...
# 以及放置行（不同 morpher 版本格式略有差异，这里只认带坐标的行）
NODE_RE = re.compile(r"current node\s*=\s*(\d+)")
PLACE_RE = re.compile(
    r"node\s*[=:]\s*(\d+).*?(?:x|X)\s*[=:]\s*(\d+).*?(?:y|Y)\s*[=:]\s*(\d+).*?"
    r"(?:t|T)\s*[=:]\s*(\d+)"
)


def parse_mapping_csv(path):
    """从 mapper 的 mapping.csv 里抽 (node -> (x, y, t))。

    优先用 mappingwithlatency.txt（每行 node,x,y,t），它最干净；
    mapping.csv 作为兜底。
    """
    place = {}
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            m = PLACE_RE.search(line)
            if m:
                place[int(m.group(1))] = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
    return place


def parse_mapping_txt(path):
    """mappingwithlatency.txt：每行 `node,x,y,t`。"""
    place = {}
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            parts = line.strip().split(",")
            if len(parts) != 4:
                continue
            try:
                n, x, y, t = (int(p) for p in parts)
            except ValueError:
                continue
            place[n] = (x, y, t)
    return place


def find_mapping(kernel):
    """在 out/<kernel>/m*/ 下找 mapper 产出的解。"""
    kdir = os.path.join(harness.root_dir(), "out", kernel)
    if not os.path.isdir(kdir):
        return None, None
    for sub in sorted(os.listdir(kdir)):
        d = os.path.join(kdir, sub)
        if not os.path.isdir(d) or not sub.startswith("m"):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".mappingwithlatency.txt"):
                place = parse_mapping_txt(os.path.join(d, name))
                if place:
                    return place, os.path.join(d, name)
        for name in sorted(os.listdir(d)):
            if name.endswith(".mapping.csv"):
                place = parse_mapping_csv(os.path.join(d, name))
                if place:
                    return place, os.path.join(d, name)
    return None, None


def main(argv):
    kernels = argv[1:] or list(harness.KERNELS)
    probs = harness.load_problems(kernels)
    print(f"{'kernel':<12} {'nodes':>5} {'mapper':>7} {'legal4':>7} {'cost4':>7} "
          f"{'legal3':>7} {'cost3':>7}  source")
    print("-" * 92)
    for k in kernels:
        prob = probs.get(k)
        if prob is None:
            print(f"{k:<12} (no dfg/arch in out/)")
            continue
        place, src = find_mapping(k)
        n = len(prob["nodes"])
        if not place:
            print(f"{k:<12} {n:>5} {'-':>7} {'-':>7} {'-':>7} {'-':>7} {'-':>7}  no mapping artifact")
            continue
        # mapper 的解里 t 是绝对相位（可能 >= II），check.py 用模 II
        r4 = check.check(prob, place, ii=harness.II_BASE)
        r3 = check.check(prob, place, ii=harness.II_TARGET)
        print(f"{k:<12} {n:>5} {len(place):>7} "
              f"{str(r4['ok']):>7} {r4['cost']:>7.1f} "
              f"{str(r3['ok']):>7} {r3['cost']:>7.1f}  {os.path.basename(os.path.dirname(src))}")
        if not r4["ok"]:
            for e in r4["errors"][:3]:
                print(f"    ii4 error: {e}")
        if not r3["ok"]:
            for e in r3["errors"][:3]:
                print(f"    ii3 error: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
