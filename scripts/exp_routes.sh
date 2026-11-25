#!/usr/bin/env bash
# --routes 布线后端的判定实验（确定性口径）
#
# 目的：回答"给 mapper 一张走廊，能不能让它布出它自己 A* 布不出的东西"
# 设计：同一个放置（pins 固定），只切换 --routes 的有无与 slack，
#       这样 pins 相同 => 放置相同 => 差异只能来自走廊。
set -u
cd ~/acc-thu-ilp
source scripts/lib.sh
activate_tc
set +eu
PY=~/acc-thu-ilp/.venv/bin/python
export CP_SAT_THREADS=${CP_SAT_THREADS:-1}
export CP_SAT_SEED=${CP_SAT_SEED:-0}
OUTD=/tmp/rgx; mkdir -p $OUTD
TSV=/tmp/exp_routes.tsv
[ -f $TSV ] || printf "kernel\tnodes\tii\trounds\tcegis\tpins_only\tslack0\tslack1\tslack2\n" > $TSV
n_nodes() { grep -o '<DFG count="[0-9]*"' "out/$1/dfg.xml" 2>/dev/null | head -1 | grep -o '[0-9]*'; }

run_map() { # $1=dir $2=pins $3=guide-or-empty
  local D=$1 P=$2 G=$3
  rm -rf "$D"; mkdir -p "$D"
  cp "$KD/dfg.xml" "$D/dfg.xml"; cp "$KD/arch.json" "$D/arch.json"
  local args=(-d dfg.xml -j arch.json -x 4 -y 4 -i "$ii" -t HyCUBE_4REG -m 0
              --pins "$P" --pins_mode 1)
  [ -n "$G" ] && args+=(--routes "$G")
  ( cd "$D" && timeout 120 "$OUT/cgra_xml_mapper" "${args[@]}" > map.log 2>&1 )
  if grep -q "Map Success" "$D/map.log"; then echo OK; else echo FAIL; fi
}

for k in "$@"; do
  KD="$(kdir "$k")"
  [ -s "$KD/mem_alloc.txt" ] || { echo "$k: no mem_alloc.txt"; continue; }
  bash scripts/inject_mem.sh "$k" 2 2048 4x4 >/dev/null 2>&1
  [ -s "$KD/arch.json" ] || { echo "$k: inject_mem failed"; continue; }
  ii=${II:-4}
  n=$(n_nodes "$k")
  pins=$OUTD/$k.pins; routes=$OUTD/$k.routes.json
  rm -f "$pins" "$routes"

  timeout 900 $PY tools/ilp/cegis.py --dfg "$KD/dfg.xml" --arch "$KD/arch.json" --ii "$ii" \
      --rounds 8 --place-time-limit 30 --route-time-limit 20 \
      --pins-out "$pins" --routes-out "$routes" > $OUTD/$k.cegis.log 2>&1
  cegis=$([ -s "$pins" ] && echo OK || echo FAIL)
  rounds=$(grep -c '^round ' $OUTD/$k.cegis.log 2>/dev/null)
  echo "[$k] n=$n II=$ii cegis=$cegis rounds=$rounds"
  [ "$cegis" = OK ] || { printf "%s\t%s\t%s\t-\tFAIL\t-\t-\t-\t-\n" "$k" "$n" "$ii" >> $TSV; continue; }

  po=$(run_map /tmp/x_${k}_po "$pins" "")
  s0=$(run_map /tmp/x_${k}_s0 "$pins" "$($PY tools/ilp/routes_to_guide.py $routes --slack 0 --xdim 4 --ydim 4 -o $OUTD/$k.g0 >/dev/null 2>&1; echo $OUTD/$k.g0)")
  s1=$(run_map /tmp/x_${k}_s1 "$pins" "$($PY tools/ilp/routes_to_guide.py $routes --slack 1 --xdim 4 --ydim 4 -o $OUTD/$k.g1 >/dev/null 2>&1; echo $OUTD/$k.g1)")
  s2=$(run_map /tmp/x_${k}_s2 "$pins" "$($PY tools/ilp/routes_to_guide.py $routes --slack 2 --xdim 4 --ydim 4 -o $OUTD/$k.g2 >/dev/null 2>&1; echo $OUTD/$k.g2)")
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$k" "$n" "$ii" "$rounds" "$cegis" "$po" "$s0" "$s1" "$s2" >> $TSV
  echo "    pins_only=$po slack0=$s0 slack1=$s1 slack2=$s2"
done
echo "[exp_routes] done"