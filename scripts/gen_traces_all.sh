#!/usr/bin/env bash
# Generate instrumentation traces for every mapped kernel that has a config.
set -uo pipefail
cd /home/steve/CGRA_Agent_Compiler
mkdir -p work/traces/logs
PY=/home/steve/miniconda3/envs/cgra/bin/python
MAP=$(/home/steve/miniconda3/envs/cgra-tc/bin/opt 2>/dev/null; true)
for cfg in configs/*.yaml; do
    k=$(basename "$cfg" .yaml)
    case "$k" in fft) echo "SKIP $k (mapper crash)"; continue;; esac
    read -r src func banks bs < <("$PY" - "$cfg" <<'PYEOF'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
print(c["c_source"], c.get("function") or "main", c.get("numberofbanks") or 2,
      c.get("banksize") or 2048)
PYEOF
)
    [ -z "$src" ] && { echo "SKIP $k (no c_source)"; continue; }
    src_abs="/home/steve/CGRA_Agent_Compiler/$src"
    [ -f "$src_abs" ] || { echo "SKIP $k (missing $src)"; continue; }
    echo "== $k ($func, $src)"
    bash scripts/gen_trace.sh "$k" "$src_abs" "$func" "$banks" "$bs" \
        > "work/traces/logs/$k.log" 2>&1
    rc=$?
    tail -3 "work/traces/logs/$k.log"
    echo "rc=$rc"
done
echo ALL_DONE
