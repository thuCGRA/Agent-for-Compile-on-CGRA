#!/usr/bin/env bash
# mem_alloc.txt + 上游 ADL → 带 bank 的 ADL（arch.json）
# 用法: scripts/inject_mem.sh <kernel> [banks] [banksize]
# 产物: out/<kernel>/arch.json
#
# 为什么需要这一步：上游 Morpher_CGRA_Mapper/json_arch/hycube_original_updatemem.json 里
# 只有 DP/FU/FU_MEM/PE/PE_MEM/CGRA，没有 SPM_B*_WRAPPER。bank 的划分取决于每个 kernel
# 的变量地址（mem_alloc.txt），所以要按 kernel 生成。update_mem_alloc.py 干的就是这件事：
# 把变量按地址区间塞进噪音最小的 bank，并追加 SPM_B0_WRAPPER / SPM_B1_WRAPPER。
# 注意它只写 DATA_LAYOUT，不写 PE→bank 的连线（这是上游 ADL 的已知缺口）。
source "$(dirname "$0")/lib.sh"

k="${1:?用法: inject_mem.sh <kernel> [banks] [banksize]}"
NOBANKS="${2:-2}"
BANKSIZE="${3:-2048}"
W="$(kdir "$k")"
[ -f "$W/mem_alloc.txt" ] || { echo "先跑 gen_dfg.sh $k" >&2; exit 2; }

banner "[$k] 注入 bank 分配 → arch.json"
python3 "$SRC/mapper/update_mem_alloc.py" \
  "$ARCH/hycube_4x4_updatemem.json" \
  "$W/mem_alloc.txt" \
  "$BANKSIZE" "$NOBANKS" \
  "$W/arch.json"

echo "bank 数: $(grep -c '_WRAPPER' "$W/arch.json")   产物: $W/arch.json"
