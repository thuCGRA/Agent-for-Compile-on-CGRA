#!/usr/bin/env bash
# 生成伐谋的可视化报告（evo/report.py 的启动器）。
#
# report.py 需要 famou 环境（Python >= 3.12 + openai + famou-v2 的依赖），
# 系统 python3 是 3.8 跑不起来。这里直接用 conda 环境里的解释器，
# 不走 conda activate —— 非交互 ssh 下 conda 不在 PATH。
#
# 用法：
#   bash evo/report.sh                       # 最新一个实验
#   bash evo/report.sh --exp famou_data/<exp>
#   bash evo/report.sh --out out/report
#
# 环境变量：
#   FAMOU_PY   famou 环境的 Python（默认 ~/miniconda3/envs/famou/bin/python）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAMOU_PY="${FAMOU_PY:-$HOME/miniconda3/envs/famou/bin/python}"

if [ ! -x "$FAMOU_PY" ]; then
  echo "找不到 $FAMOU_PY" >&2
  echo "先按 evo/README.md 第四节建 famou 环境，或设置 FAMOU_PY 指向它。" >&2
  exit 1
fi

exec "$FAMOU_PY" "$ROOT/evo/report.py" "$@"
