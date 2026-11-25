#!/usr/bin/env bash
# mem_alloc.txt + 上游 ADL → 带 bank 的 ADL（arch.json）
# 用法: scripts/inject_mem.sh <kernel> [banks] [banksize] [dims]
#   dims: 4x4（默认）或 8x8 —— 决定用哪个 ADL 模板、默认 bank 数、以及哨兵地址
# 产物: out/<kernel>/arch.json、out/<kernel>/dims
#
# 为什么需要这一步：上游 hycube_original_updatemem*.json 里只有
# DP/FU/FU_MEM/PE/PE_MEM/CGRA，没有 SPM_B*_WRAPPER。bank 的划分取决于每个 kernel
# 的变量地址（mem_alloc.txt），所以要按 kernel 生成。update_mem_alloc.py 只写
# DATA_LAYOUT，不写 PE→bank 连线（这是上游 ADL 的已知缺口）。
#
# bank 数不是自由参数，是硬件结构：x=0（8x8 还有 x=7）整列是 PE_MEM，每个 bank 两个
# MEMPORT，所以 4x4 = 4/2 = 2 个 bank，8x8 = 16/2 = 8 个。这个数字写在 ADL 模板的
# CGRA.CONNECTIONS 里，bank 数对不上会直接撞 mapper 的 checkOps。
#
# bank_size 的优先级：命令行第 3 个参数 > out/<k>/manifest.json > 2048。
source "$(dirname "$0")/lib.sh"

k="${1:?用法: inject_mem.sh <kernel> [banks] [banksize] [dims]}"
W="$(kdir "$k")"
[ -f "$W/mem_alloc.txt" ] || { echo "先跑 gen_dfg.sh $k" >&2; exit 2; }

DIMS="${4:-4x4}"
case "$DIMS" in
  4x4) TPL="$ARCH/hycube_4x4_updatemem.json"; XD=4; YD=4; DEF_BANKS=2 ;;
  8x8) TPL="$ARCH/hycube_8x8_updatemem.json"; XD=8; YD=8; DEF_BANKS=8 ;;
  *)   echo "dims 只支持 4x4 / 8x8（收到 $DIMS）" >&2; exit 2 ;;
esac
[ -f "$TPL" ] || { echo "缺 ADL 模板 $TPL" >&2; exit 2; }

MAN="$W/manifest.json"
if [ -n "${3:-}" ]; then
  BANKSIZE="$3"
elif [ -f "$MAN" ] && grep -q '"bank_size"' "$MAN"; then
  BANKSIZE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["bank_size"])' "$MAN")"
else
  BANKSIZE=2048
fi
# bank 数只由阵列形状决定；manifest 里记的是 kernel 需要几个 bank，不是硬件有几个
NOBANKS="${2:-$DEF_BANKS}"

banner "[$k] 注入 bank 分配 → arch.json（$DIMS，$NOBANKS bank × $BANKSIZE 字）"

# 哨兵地址必须随 (banks, banksize) 走：loopend = 总字数/2 - 1，loopstart = 总字数 - 2。
# 上游 DFG pass 与 synth_mem_alloc 都按 2 bank × 2048 写这两个地址，换阵列后必须重写，
# 否则仿真器的 while(dmem[MEM_SIZE/2-1]==0) 永远等不到，跑成死循环。
python3 - "$W/mem_alloc.txt" "$NOBANKS" "$BANKSIZE" <<'PY'
import sys
path, banks, bs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
total = banks * bs
want = {"loopend": total // 2 - 1, "loopstart": total - 2}
out, changed = [], []
for ln in open(path).read().splitlines():
    if "," in ln and not ln.startswith("var_name"):
        name, _, addr = ln.partition(",")
        n = name.strip()
        if n in want and addr.strip().lstrip("-").isdigit():
            if int(addr) != want[n]:
                changed.append("%s %s->%d" % (n, addr.strip(), want[n]))
            ln = "%s,%d" % (n, want[n])
    out.append(ln)
open(path, "w").write("\n".join(out) + "\n")
if changed:
    print("  哨兵重写: " + ", ".join(changed))
PY

python3 "$SRC/mapper/update_mem_alloc.py" \
  "$TPL" "$W/mem_alloc.txt" "$BANKSIZE" "$NOBANKS" "$W/arch.json"

# 记下这次用的阵列/存储参数，map.sh / sim.sh 读它，避免各处硬编码 4x4
printf '%s %s %s %s\n' "$XD" "$YD" "$NOBANKS" "$BANKSIZE" > "$W/dims"

NB="$(grep -o 'SPM_B[0-9]*_WRAPPER"' "$W/arch.json" | sort -u | wc -l)"
echo "bank 数: $NB   产物: $W/arch.json"
echo "阵列: $(cat "$W/dims")   （xdim ydim banks bank_size）"
