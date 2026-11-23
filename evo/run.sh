#!/usr/bin/env bash
# 用真实 LLM 端点跑伐谋演化。
#
# 端点与密钥来自 ~/.config/cgra_agent/credentials.env（与 ~/cac 共用同一份，
# 不进仓库）：
#     CGRA_LLM_BASE_URL=https://...
#     CGRA_LLM_MODEL=...
#     CGRA_LLM_API_KEY=sk-...
#
# 用法：
#   bash evo/run.sh                 # 用 evo/config.yaml（已存在则直接用）
#   bash evo/run.sh --regen         # 从 config.example.yaml 重新生成 config.yaml
#   bash evo/run.sh -r famou_data/<exp>   # 恢复已有实验，其余参数透传给 run_famou.py
#
# 环境变量：
#   FAMOU_HOME   famou-v2 的路径（默认 ~/famou-v2）
#   FAMOU_PY     跑伐谋的 Python（默认 ~/miniconda3/envs/famou/bin/python）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVO="$ROOT/evo"
FAMOU_HOME="${FAMOU_HOME:-$HOME/famou-v2}"
FAMOU_PY="${FAMOU_PY:-$HOME/miniconda3/envs/famou/bin/python}"
CREDS="${CREDS:-$HOME/.config/cgra_agent/credentials.env}"

[ -x "$FAMOU_PY" ] || { echo "找不到 $FAMOU_PY（先按 evo/README.md 建 famou 环境）" >&2; exit 1; }
[ -f "$FAMOU_HOME/run_famou.py" ] || { echo "找不到 $FAMOU_HOME/run_famou.py" >&2; exit 1; }

REGEN=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--regen" ]; then REGEN=1; else ARGS+=("$a"); fi
done

if [ "$REGEN" = "1" ] || [ ! -f "$EVO/config.yaml" ]; then
  [ -f "$CREDS" ] || { echo "缺少 $CREDS（含 CGRA_LLM_* 三行）" >&2; exit 1; }
  "$FAMOU_PY" - "$EVO/config.example.yaml" "$EVO/config.yaml" "$CREDS" <<'PY'
import re, sys
example, out, creds = sys.argv[1], sys.argv[2], sys.argv[3]
env = {}
for line in open(creds):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
base = env["CGRA_LLM_BASE_URL"].rstrip("/")
# cac 的客户端约定：base 没有路径时补 /v1
if not re.search(r"://[^/]+/.+", base):
    base += "/v1"
text = open(example, encoding="utf-8").read()
text = re.sub(r"(?m)^    api_base:.*$", "    api_base: %s" % base, text)
text = re.sub(r"(?m)^    model:.*$", "    model: %s" % env["CGRA_LLM_MODEL"], text)
text = re.sub(r"(?m)^    api_key:.*$", "    api_key: %s" % env["CGRA_LLM_API_KEY"], text)
open(out, "w", encoding="utf-8").write(text)
print("wrote %s (model=%s)" % (out, env["CGRA_LLM_MODEL"]))
PY
fi

cd "$ROOT"
exec "$FAMOU_PY" "$FAMOU_HOME/run_famou.py" \
  -c "$EVO/config.yaml" -p "$EVO/init.py" -e "$EVO/evaluator.py" "${ARGS[@]+"${ARGS[@]}"}"
