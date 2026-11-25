#!/bin/bash
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
K=array_add
D=/tmp/rd2_$K; rm -rf $D; mkdir -p $D
cp out/$K/dfg.xml $D/; cp out/$K/arch.json $D/; cp /tmp/rgx/$K.pins $D/pins.txt
cd $D
ROUTE=1 timeout 40 "$OUT/cgra_xml_mapper" -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
   -t HyCUBE_4REG -m 0 --pins pins.txt --pins_mode 1 > route.log 2>&1
echo "=== node 1 的 pin: 1 0 2 (x=1,y=0,t=2) ==="
echo "=== 候选 dest 总数 ==="
grep -m2 "Candidate Dests = " route.log
echo "=== 候选 dest 的 (PE,t) 前 30 个 ==="
grep "Candidate Dest =" route.log | sed -n '1,30p'
echo "=== 失败行 ==="
grep -m3 "not among" route.log
echo "=== 该 dest 是否在候选里 ==="
grep -c "PE_X1|_Y0|-T2" route.log
grep "PE_X1|_Y0|-T2" route.log | head -5