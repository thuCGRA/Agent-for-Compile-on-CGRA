# 臂 A 模块边界与合并说明

`interfaces.py` 是冻结契约（`Placer` / `Router` 两个 Protocol）。本文件说明哪些代码属于
A 臂、哪些是共享工具、B 臂要接 `placers/sa.py` 与 `routers/llm.py` 时改哪里。

## 1. 槽位

pipeline 只通过工厂拿实现，不直接 import 具体类：

| 槽位 | 工厂 | 工厂文件 | 当前实现 |
|---|---|---|---|
| Placer | `make_placer(kind, client=..., pins_file=..., seed=...)` | `src/cgra_agent/placers/__init__.py` | `llm`（A 臂）、`greedy`（参考）、`fixed`（回放） |
| Router | `make_router(kind, repo_root=..., xdim=..., ydim=..., timeout=...)` | `src/cgra_agent/routers/__init__.py` | `pathfinder`（vendor pinned mapper） |

CLI 对应 `--placer {llm,greedy,fixed}` 与 `--router pathfinder`。

## 2. 文件归属

**A 臂专属（合并时随臂走）**
- `src/cgra_agent/placers/llm.py` —— LLM 布局器（A 臂主体）
- `src/cgra_agent/llm/prompts.py` —— A 臂 prompt（含 R3 延迟语义）
- `src/cgra_agent/placers/pin_select.py` —— pin 密度消融的固定选点规则（A 臂实验设计）
- `scripts/mock_replay.py`、`scripts/eval_map.py`、`scripts/eval_density.py` —— A 臂评测辅助

**共享工具（与臂无关，合并时保留一份）**
- `src/cgra_agent/tools/{dfg,facts,validate,mapper,sim,trace,jobdir}.py`
- `src/cgra_agent/pipeline.py`（S0–S9 状态机、预算分桶、II 阶梯、pin 子集开关）
- `src/cgra_agent/cli.py`、`src/cgra_agent/llm/client.py`（OpenAI 兼容客户端，任何臂可复用）
- `configs/*.yaml`（4x4 共享架构参数）

**参考实现（不属于任何臂，只用于评测/对照）**
- `src/cgra_agent/placers/greedy.py`、`src/cgra_agent/placers/fixed.py`
- `src/cgra_agent/routers/pathfinder.py`（所有臂共用的路由器）

## 3. B 臂接入步骤

1. 把 `sa.py` 放到 `src/cgra_agent/placers/sa.py`，实现
   `place(facts, arch, ii, budget, *, feedback=None, previous=None) -> Placement`。
2. 在 `placers/__init__.py` 的 `_PLACERS` 加 `"sa": ...`，并在 `make_placer` 里加构造分支
   （构造参数按需，`--placer sa` 即可选中）。
3. 把 `routers/llm.py` 放到 `src/cgra_agent/routers/llm.py`，实现
   `route(*, job, dfg, arch, ii, pins=None, pins_mode=0) -> MapReport`；
   在 `routers/__init__.py` 的 `_ROUTERS` 注册，`make_router` 加分支，`--router llm` 选中。
4. `interfaces.py` 不改；pipeline 不加新参数。
5. 注意：`pipeline` 的 `pin_ratio/pin_rule` 是 A 臂实验开关（默认 1.0 = 全 pin）；
   B 臂默认不设即可。`validate` 的硬规则（依赖延迟下界）是共享的，R3/R6 只告警。

## 4. 契约细节（照抄 `interfaces.py`）

- `Placer.place(facts: dict, arch: dict, ii: int, budget: dict) -> Placement`
- `Router.route(placement: Placement, facts: dict, arch: dict, ii: int, budget: dict) -> MapReport`
  —— 注意 `interfaces.py` 的 Protocol 是 5 位置参数；pipeline 里 `PathFinderRouter` 用的是
  关键字版 `route(*, job, dfg, arch, ii, pins, pins_mode)`。合并时以 pipeline 的关键字版为准，
  `interfaces.py` 保持不动（它只是类型契约，Python 不做运行时校验）。
