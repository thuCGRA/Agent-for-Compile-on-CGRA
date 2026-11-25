#!/bin/bash
# 用 ROUTE=1 打开 estimateRouting 的候选日志，看它到底"愿意"给哪些 (PE, phase)
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
K=${1:-array_add}
D=/tmp/route_diag_$K
rm -rf $D; mkdir -p $D
cp out/$K/dfg.xml $D/dfg.xml
cp out/$K/arch.json $D/arch.json
cp /tmp/rgx/$K.pins $D/pins.txt 2>/dev/null || { echo "no pins"; exit 1; }
cd $D
ROUTE=1 timeout 60 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
   -t HyCUBE_4REG -m 0 --pins pins.txt --pins_mode 1 > route.log 2>&1
echo "exit=$?"
echo "=== pin 期望的 (node -> x,y,t) ==="
grep -v '^#' pins.txt | head -6
echo "=== estimateRouting 对 node 1 的候选 dest（第一轮） ==="
grep -n "Candidate Dests = \|Candidate Dest =" route.log | head -25
echo "=== 每个候选 dest 的 (PE, t) 与到达相位 ==="
grep -n "lat = .*,PE=.*,t=" route.log | head -20
echo "=== 失败行 ==="
grep -n -E "PIN:|not among" route.log | head -6