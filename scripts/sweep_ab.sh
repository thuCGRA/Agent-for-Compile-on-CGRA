#!/usr/bin/env bash
# 批量对比 placing/routing 算法：stage A 建 DFG+arch，stage B 跑 -m 0 / -m 1
# 输出 /tmp/sweep.tsv: kernel nodes method ok ii lat secs
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +e    # lib.sh 的 activate_tc 会打开 set -e；这里必须关掉，否则单个 kernel 失败会中断整个 sweep

OUTD=/tmp/sw; mkdir -p $OUTD
TSV=/tmp/sweep.tsv
[ -f $TSV ] || printf "kernel\tnodes\tmethod\tok\tii\tlat\tsecs\n" > $TSV

n_nodes() { grep -o '<DFG count="[0-9]*"' "out/$1/dfg.xml" 2>/dev/null | head -1 | grep -o '[0-9]*'; }

echo "[stage A] 构建 DFG + arch"
for k in $(kernels); do
  if [ ! -s "out/$k/dfg.xml" ]; then
    bash scripts/gen_dfg.sh "$k" >$OUTD/$k.gendfg.log 2>&1
    [ -s "out/$k/dfg.xml" ] || { echo "  skip $k (no DFG)"; continue; }
  fi
  if [ ! -s "out/$k/arch.json" ]; then
    bash scripts/inject_mem.sh "$k" >$OUTD/$k.inject.log 2>&1
  fi
  echo "  ok $k nodes=$(n_nodes $k)"
done

echo "[stage B] map -m 0 / -m 1"
for k in $(kernels); do
  [ -s "out/$k/arch.json" ] || continue
  n=$(n_nodes "$k")
  for M in 0 1; do
    t0=$(date +%s)
    timeout 90 bash scripts/map.sh "$k" "$M" 0 >$OUTD/$k.m$M.log 2>&1
    rc=$?
    t1=$(date +%s); secs=$((t1-t0))
    if grep -q "Map Success" $OUTD/$k.m$M.log 2>/dev/null; then
      ii=$(grep -o 'Map Success with II = [0-9]*' $OUTD/$k.m$M.log | head -1 | grep -o '[0-9]*$')
      lat=$(grep -o 'lat = [0-9]*' $OUTD/$k.m$M.log | head -1 | grep -o '[0-9]*$')
      printf "%s\t%s\tm%s\t1\t%s\t%s\t%s\n" "$k" "$n" "$M" "${ii:-?}" "${lat:-?}" "$secs" >> $TSV
      echo "  $k m$M ok II=$ii lat=$lat ${secs}s"
    else
      printf "%s\t%s\tm%s\t0\t-\t-\t%s\n" "$k" "$n" "$M" "$secs" >> $TSV
      echo "  $k m$M FAIL rc=$rc ${secs}s"
    fi
  done
done
echo "[stage B] done"
