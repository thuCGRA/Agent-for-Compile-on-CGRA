# 两臂并行开发约定

> 为什么要拆：A 臂改 Python 工具层，B 臂要改 C++ mapper 并重编，两边都会碰 `src/cgra_agent/tools/`。同一个工作区并行改必然冲突，所以用 git worktree 物理隔离。

## 目录与分支

| 角色 | 路径 | 分支 | 负责 |
|---|---|---|---|
| 主干 | `/home/steve/CGRA_Agent_Compiler` | `master` | 文档、契约、集成后的统一 pipeline |
| A 臂 | `/home/steve/cac-armA` | `arm/A` | LLM 布局 + PathFinder 布线 |
| B 臂 | `/home/steve/cac-armB` | `arm/B` | SA 布局 + LLM 布线 + mapper verify 模式 |

两个 worktree 都从 `f62555b` 拉出，各自 `cp -a` 了一份 `toolchain/vendor`（122 MB）与 `toolchain/out`（92 MB）。B 臂可以随便重编自己的 mapper，不会影响 A 臂。

## 边界

1. **`src/cgra_agent/interfaces.py` 冻结**。两臂都不得修改；需要新字段就加到各自的 `src/cgra_agent/ext.py`，合并时我再决定是否上提。
2. **互不越界**：A 不改 C++、不动 `toolchain/vendor/mapper`；B 不改 `src/cgra_agent/tools/dfg.py` 之外的解析逻辑（facts 以 A 的实现为准，B 可先复制一份临时用）。
3. **共享 conda 环境只读**：两臂都不得 `conda install` / `pip install` 进 `cgra` 或 `cgra-tc`。
4. **各自 commit**，不 merge、不 rebase、不 push。

## 合并顺序

1. 两臂各自跑通验收，把数字写进 `ARM.md`。
2. 我先在 `arm/A` 上复核（跑一遍 `array_add`），再看 `arm/B` 的 spike 结论。
3. 合并策略：以 A 的 `tools/`（dfg/facts/validate/mapper/sim/jobdir）为基线，把 B 的 `placers/sa.py`、`routers/llm.py`、`tools/route.py` 接进来，`pipeline.py` 收敛成「Placer × Router」两个槽位；B 的 C++ 补丁以 patch 形式进 `toolchain/patches/0004-routes.patch`。
4. 合并后主干重跑四臂矩阵，确认 A/B/C/D 都能出结果。

## 已知冲突点

| 文件 | 谁写 | 处理 |
|---|---|---|
| `src/cgra_agent/tools/facts.py` | A 先写，B 可能复制 | 合并取 A 版本，B 的差异逐条看 |
| `src/cgra_agent/pipeline.py` | 两边都会写 | 合并时重写为槽位版，不保留任何一边的原文件 |
| `toolchain/out/*` | 各自构建 | 不入库，合并后用主干 `build.sh` 重建 |
| `work/` `runs/` | 各自产出 | 已忽略 |

## 验收口径（两臂一致）

- 同一 kernel、同一 II 初值、同一 mapper 超时。
- `array_add` 必须 `Mismatches::0`；A 臂 II ≤ 4。
- 所有结论附 run bundle 路径，不报"我记得跑过"。
