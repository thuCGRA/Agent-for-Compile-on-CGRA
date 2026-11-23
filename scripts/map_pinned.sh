#!/usr/bin/env bash
# 钉住一个给定放置去布线 → bitstream
#
# 用法: scripts/map_pinned.sh <kernel> <pins.json> [mode] [init_II] [xdim] [ydim]
#   mode: 1 = strict（每个节点必须落在 pin 上）
#         2 = repair（布不到的 pin 丢掉，其余保留）
#
# 产物: out/<kernel>/pins<m>/ 下，与 map.sh 同构，外加 pins_report.json
#
# 用途：我们自己的 router 说某个放置可布，不等于 morpher 布得出来。这个脚本把
# 放置原样交给 morpher 的 PathFinder，让它按 pin 约束布线。布通了才说明那个
# 放置是 morpher 认可的解；再跑 sim.sh 才知道功能对不对。
source "$(dirname "$0")/lib.sh"
activate_tc

k="${1:?用法: map_pinned.sh <kernel> <pins.json> [mode] [init_II] [xdim] [ydim]}"
PINS="${2:?缺少 pins.json}"
MODE="${3:-1}"
II="${4:-0}"
XDIM="${5:-4}"
YDIM="${6:-4}"
SUB="${7:-}"

W="$(kdir "$k")"
[ -f "$W/dfg.xml" ]   || { echo "先跑 gen_dfg.sh $k" >&2; exit 2; }
[ -f "$W/arch.json" ] || { echo "先跑 inject_mem.sh $k" >&2; exit 2; }
[ -f "$PINS" ]        || { echo "找不到 $PINS" >&2; exit 2; }
PINS="$(cd "$(dirname "$PINS")" && pwd)/$(basename "$PINS")"

[ -n "$SUB" ] && R="$W/$SUB" || R="$W/pins$MODE"
rm -rf "$R"; mkdir -p "$R"
ln -s ../dfg.xml  "$R/dfg.xml"
ln -s ../arch.json "$R/arch.json"
cd "$R"

banner "[$k] mapper -m 3 (pinned) --pins_mode $MODE -i $II"
"$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json \
  -x "$XDIM" -y "$YDIM" -i "$II" -t HyCUBE_4REG -m 3 \
  --pins "$PINS" --pins_mode "$MODE" --report pins_report.json | tee map.log

echo
if grep -q "Map Success" map.log; then
  IIF="$(grep -o 'Map Success with II = [0-9]*' map.log | head -1 | grep -o '[0-9]*$')"
  echo "结果: II = ${IIF:-?}，pinned 映射成功"
  echo "bitstream: $(ls ./*.bin 2>/dev/null || echo '(无)')"
  if [ -f pins_report.json ]; then
    echo "report: $(grep -o '"pins_applied": [0-9]*' pins_report.json | head -1) / $(grep -o '"pins_total": [0-9]*' pins_report.json | head -1)"
  fi
else
  echo "pinned 映射失败。看 $R/map.log 与 pins_report.json"
  [ -f pins_report.json ] && { echo "--- report ---"; cat pins_report.json; }
  exit 1
fi
