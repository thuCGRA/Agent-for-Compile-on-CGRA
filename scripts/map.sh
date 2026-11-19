#!/usr/bin/env bash
# DFG + ADL → 布局布线 → bitstream
# 用法: scripts/map.sh <kernel> [method] [init_II] [xdim] [ydim]
#   method: 0 = PathFinder（Morpher 论文的默认算法）
#           1 = SimulatedAnnealing（上游 SA，注意它在 PathFinder 成功时会直接返回）
# 产物: out/<kernel>/m<method>/ 下
#   dfg.xmlarch.json_MTP=1_II=<II>_Iter=0.mappingwithlatency.txt   每行 node,x,y,t
#   dfg.xmlarch.json_MTP=1_Iter=0.mapping.csv                      逐节点日志
#   dfg.xmlarch.json_DP1_XDim=4_YDim=4_II=<II>_MTP=1_binary.bin   给仿真器的配置包
#   dfg.xmlarch.json_MTP=1.congestion.info
source "$(dirname "$0")/lib.sh"
activate_tc

k="${1:?用法: map.sh <kernel> [method] [init_II] [xdim] [ydim]}"
M="${2:-0}"
II="${3:-0}"
XDIM="${4:-4}"
YDIM="${5:-4}"
W="$(kdir "$k")"
[ -f "$W/dfg.xml" ]   || { echo "先跑 gen_dfg.sh $k" >&2; exit 2; }
[ -f "$W/arch.json" ] || { echo "先跑 inject_mem.sh $k" >&2; exit 2; }

R="$W/m$M"
rm -rf "$R"; mkdir -p "$R"
ln -s ../dfg.xml  "$R/dfg.xml"
ln -s ../arch.json "$R/arch.json"
cd "$R"

banner "[$k] mapper -m $M -i $II -x $XDIM -y $YDIM"
"$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json \
  -x "$XDIM" -y "$YDIM" -i "$II" -t HyCUBE_4REG -m "$M" | tee map.log

echo
if grep -q "Map Success" map.log; then
  IIF="$(grep -o 'Map Success with II = [0-9]*' map.log | head -1 | grep -o '[0-9]*$')"
  NMAP="$(grep -c '^current node' ./*.mapping.csv 2>/dev/null || echo 0)"
  echo "结果: II = ${IIF:-?}，已放置 $NMAP 个节点"
  echo "bitstream: $(ls ./*.bin 2>/dev/null || echo '(无)')"
  echo "结果行: $(tail -1 result.txt 2>/dev/null)"
else
  echo "映射失败（日志里没有 Map Success）。看 $R/map.log 与 result.txt"
  exit 1
fi
# 说明：*.mappingwithlatency.txt 只有部分路径会写（内容为空是正常的），
# 逐节点放置请看 *.mapping.csv 或 *.routeInfo.log（后者还带每条边的完整路由）。
