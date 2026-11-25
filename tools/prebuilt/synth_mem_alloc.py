#!/usr/bin/env python3
"""从上游 Morpher 预生成 DFG 反推一份 mem_alloc.txt。

为什么需要这个：上游 `Morpher_CGRA_Mapper/applications/` 里的 `*_DFG.xml` 是**只带 DFG、不带
mem_alloc** 的（mem_alloc 本来是 run_morpher.py 在跑插桩程序时产出的）。而我们的流水线要求
`out/<k>/mem_alloc.txt` → `inject_mem.sh` → `arch.json`（DATA_LAYOUT）→ mapper。mapper 在
`PathFinderMapper::checkOps`（src/mapper/src/mapper/PathFinderMapper.cpp:3305）会硬检查
「DFG 里每个 LOAD/STORE 的 base pointer 必须出现在 DATA_LAYOUT 里」，缺一个就 `exit(EXIT_FAILURE)`
（而且那条消息走 `LOG(ARCH)`，不设环境变量时**完全静默**，表现为 rc=1 没有任何输出）。

所以这里做的是：把 DFG 里出现过的全部 base pointer 都分配一个地址，按 bank 大小装箱。

布局约定（与 run_morpher.py 产出的真实 mem_alloc 对齐）：
  loopend   -> bank_size*banks/2 - 1            （2 bank×2048 时 = 2047，模拟器的哨兵地址）
  loopstart -> bank_number * bank_size - 2      （4094）
  数组（size > 1）：从 size 最大的开始，轮流放进各 bank
  标量（size == 1/4）：接在数组后面

--auto 时会自动放大 bank_size 直到装得下（doitgen 的 A 有 4000 字，单 bank 2048 放不下），
并把最终选用的 bank_size 写进 sidecar `mem_layout.json`，供 inject_mem.sh 读取。

用法：
  synth_mem_alloc.py <dfg.xml> [-o mem_alloc.txt] [--bank-size 2048] [--banks 2] [--auto]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# 上游 DFG 的属性之间经常缺空格（<Node idx="11" ASAP="0" BB=""CONST="4094">），
# 所以一律用正则，不用 XML parser。
BP_RE = re.compile(r"<BasePointerName([^>]*)>([^<]*)</BasePointerName>")
SIZE_RE = re.compile(r'size\s*=\s*"(\d+)"')


def parse_pointers(path):
    """-> [(name, size)] 按出现顺序去重。"""
    text = open(path, encoding="utf-8", errors="replace").read()
    seen = {}
    order = []
    for attrs, name in BP_RE.findall(text):
        name = name.strip()
        if not name:
            continue
        m = SIZE_RE.search(attrs)
        size = int(m.group(1)) if m else 1
        if name in seen:
            if seen[name] != size:
                print(
                    "warning: %s 的大小不一致（%d vs %d），取较大者"
                    % (name, seen[name], size),
                    file=sys.stderr,
                )
                seen[name] = max(seen[name], size)
            continue
        seen[name] = size
        order.append(name)
    return [(n, seen[n]) for n in order]


def layout(pointers, bank_size, banks):
    """-> {name: base_addr}，保证同一 bank 内不重叠。"""
    total = bank_size * banks
    out = {}

    # 哨兵：模拟器 while(dmem[MEM_SIZE/2-1]==0) 靠 loopend 结束
    reserved = {}
    if any(n == "loopend" for n, _ in pointers):
        # 哨兵地址必须是 MEM_SIZE/2-1（仿真器 while(dmem[MEM_SIZE/2-1]==0)），
        # 而 MEM_SIZE = banks*bank_size；banks=2 时正好等于 bank_size-1，
        # 但 8x8 有 8 个 bank，写死 bank_size-1 会让哨兵落错 bank。
        reserved["loopend"] = total // 2 - 1
    if any(n == "loopstart" for n, _ in pointers):
        reserved["loopstart"] = total - 2

    # 每个 bank 的可分配区间 [cursor, limit)（绝对地址）
    cursor = [i * bank_size for i in range(banks)]
    limit = [(i + 1) * bank_size for i in range(banks)]
    for name, addr in reserved.items():
        b = addr // bank_size
        if addr == b * bank_size:
            cursor[b] = addr + 1
        else:
            limit[b] = min(limit[b], addr)  # 该格保留给哨兵

    arrays = [(n, s) for n, s in pointers if s > 1 and n not in reserved]
    scalars = [(n, s) for n, s in pointers if s <= 1 and n not in reserved]
    arrays.sort(key=lambda t: -t[1])

    turn = 0
    for name, size in arrays:
        for attempt in range(banks):
            b = (turn + attempt) % banks
            if cursor[b] + size <= limit[b]:
                out[name] = cursor[b]
                cursor[b] += size
                turn = b + 1
                break
        else:
            raise ValueError(
                "无法为数组 %s（size=%d）找到空间：%d 个 bank × %d 字"
                % (name, size, banks, bank_size)
            )

    for name, size in scalars:
        need = max(size, 1)
        for b in range(banks):
            if cursor[b] + need <= limit[b]:
                out[name] = cursor[b]
                cursor[b] += need
                break
        else:
            raise ValueError("无法为标量 %s 找到空间" % name)

    out.update(reserved)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="从预生成 DFG 反推 mem_alloc.txt")
    ap.add_argument("dfg")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--bank-size", type=int, default=2048)
    ap.add_argument("--banks", type=int, default=2)
    ap.add_argument(
        "--auto",
        action="store_true",
        help="装不下时自动放大 bank_size（上限 65536），并把结果写进 mem_layout.json",
    )
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    pointers = parse_pointers(a.dfg)
    if not pointers:
        raise SystemExit("%s 里没有 BasePointerName" % a.dfg)

    bank_size = a.bank_size
    while True:
        try:
            addr = layout(pointers, bank_size, a.banks)
            break
        except ValueError as e:
            if not a.auto or bank_size >= 65536:
                raise SystemExit(str(e))
            bank_size *= 2
            if not a.quiet:
                print("  bank_size 不够，放大到 %d" % bank_size, file=sys.stderr)

    lines = ["var_name,base_addr"]
    for name, _ in pointers:
        lines.append("%s,%d" % (name, addr[name]))
    text = "\n".join(lines) + "\n"

    if a.out:
        open(a.out, "w").write(text)
        side = os.path.join(os.path.dirname(os.path.abspath(a.out)), "mem_layout.json")
        with open(side, "w") as f:
            json.dump({"bank_size": bank_size, "banks": a.banks}, f)
            f.write("\n")
        if not a.quiet:
            print(
                "写出 %s（%d 个变量，bank_size=%d × %d）"
                % (a.out, len(pointers), bank_size, a.banks)
            )
            for name, size in pointers:
                print("  %-12s size=%-5d addr=%d" % (name, size, addr[name]))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
