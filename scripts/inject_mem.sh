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
#
# bank_size 的优先级：命令行第 3 个参数 > out/<k>/manifest.json 的 bank_size > 2048。
# 预生成的 polybench DFG 里有 4000 字的数组（doitgen），2048 放不下，import_prebuilt.sh
# 会在 manifest 里记下放大的 bank_size，这里自动跟上。
source "$(dirname "$0")/lib.sh"

k="${1:?用法: inject_mem.sh <kernel> [banks] [banksize]}"
W="$(kdir "$k")"
[ -f "$W/mem_alloc.txt" ] || { echo "先跑 gen_dfg.sh $k" >&2; exit 2; }

MAN="$W/manifest.json"
if [ -n "${3:-}" ]; then
  BANKSIZE="$3"
elif [ -f "$MAN" ] && grep -q '"bank_size"' "$MAN"; then
  BANKSIZE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["bank_size"])' "$MAN")"
else
  BANKSIZE=2048
fi
if [ -n "${2:-}" ]; then
  NOBANKS="$2"
elif [ -f "$MAN" ] && grep -q '"banks"' "$MAN"; then
  NOBANKS="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["banks"])' "$MAN")"
else
  NOBANKS=2
fi

banner "[$k] 注入 bank 分配 → arch.json（$NOBANKS bank × $BANKSIZE 字）"
python3 "$SRC/mapper/update_mem_alloc.py" \
  "$ARCH/hycube_4x4_updatemem.json" \
  "$W/mem_alloc.txt" \
  "$BANKSIZE" "$NOBANKS" \
  "$W/arch.json"

echo "bank 数: $(grep -c '_WRAPPER' "$W/arch.json")   产物: $W/arch.json"
