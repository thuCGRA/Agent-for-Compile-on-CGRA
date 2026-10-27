#!/usr/bin/env bash
# Run the agent without installing the package (src layout).
#   scripts/run.sh run --arm A --kernel array_add
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/steve/miniconda3/etc/profile.d/conda.sh
conda activate cgra
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python -m cgra_agent.cli "$@"
