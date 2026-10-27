# 计划：两条实验臂（v0.6）

> 上一版（ES 骨架、n0–n12、五层架构）作废，已删除。本文件只描述当前方案怎么落地。

## 1. 要回答的问题

同一套 LLVM 提取 + 仿真裁决的主链上，**LLM 放在编译器的哪个位置才有用**。拆成两个可独立证伪的假设：

- **H1（臂 A）**：让 LLM 做**布局**（节点 → PE × 周期），布线交给寻路程序，能在更少尝试内达到同等或更好的 II。
- **H2（臂 B）**：让 LLM 做**布线**（边 → 跳序列），布局交给模拟退火，能解出寻路程序解不出的拥塞实例，或在同 II 下降低布线代价。

只测一个臂无法区分「LLM 有用」与「LLM 放在这里有用」，所以做 2×2：

| 臂 | 布局 | 布线 | 作用 |
|---|---|---|---|
| **A** | LLM | 寻路程序（PathFinder） | 主臂 1 |
| **B** | 模拟退火 | LLM | 主臂 2 |
| C | 模拟退火 | 寻路程序 | 纯算法基线 |
| D | LLM | LLM | 全 LLM（上限/下限探针） |

C 是必须的：没有它，A 的提升分不清是 LLM 还是「有放置先验」本身。D 便宜（复用 A、B 的组件），用来观察两级 LLM 误差是否叠加。

## 2. 结构：流水线可插拔

两个臂不是两套代码，是同一 pipeline 的两个实现槽位：

```python
class Placer(Protocol):
    def place(self, facts, arch, ii, budget) -> Placement: ...

class Router(Protocol):
    def route(self, placement, facts, arch, budget) -> MapReport: ...
```

```
DFG → facts → Placer → Placement → Router → MapReport + bin → Sim → SimResult → Diagnose
```

| 臂 | Placer | Router |
|---|---|---|
| A | `LLMPlacer` | `PathFinderRouter`（`-m 3 --pins_mode 1`） |
| B | `SAPlacer` | `LLMRouter`（LLM 出路径 → `route_check` → mapper 校验模式） |
| C | `SAPlacer` | `PathFinderRouter` |
| D | `LLMPlacer` | `LLMRouter` |

失败回退也按槽位分：`validate` 报错 → 回 Placer；`route_check`/mapper 报冲突 → 回 Router；仿真失配 → Diagnose 判定回哪个槽位。这样「失败归因」这件事本身是被两条臂共享的，不会变成某一臂的特权。

## 3. 臂 A（先做）

复用已有的 `tools/morpher_llm` 思路，但按 v0.6 收敛到最小：

1. `dfg_build.sh` 包住 LLVM 段（固定参数，从 config 读）。
2. `facts` 产出给 LLM 的紧凑文本（节点、边、bank、ResMII、每相位容量）。
3. `LLMPlacer` 只决定**关键节点**（度数高、跨 bank、链首尾）的 `(x,y,t)`；其余交给 mapper。
4. `validate` 本地预检，不合法不进 mapper。
5. `PathFinderRouter` 调 `-m 3 --pins_mode 1`（strict），失败退 `3`（repair）。
6. `hycube_simulator` 裁决；失配则 `Diagnose` 出 CDD。
7. 预算：`max_rounds=4`、`max_llm_calls=12`、单次 mapper 超时 600 s、单 kernel 总墙钟 20 min。

验收：`array_add` 与 `gemm` 在 4×4 上端到端跑通，且至少一个 kernel 的 II 不差于 method 0 基线（`array_add` 基线 II=4, lat=11）。

## 4. 臂 B（难，先做 spike）

三个新件：

**(a) `SAPlacer`**（纯 Python，无 LLM）
- 状态：全节点 `(x,y,t)`；硬约束：唯一槽位、op 支持、LOAD/STORE 在所属 bank 的 memory PE、边相位差 ≥ 曼哈顿距离。
- 代价：`J = 总跳数 + 10·违反数 + 0.5·关键路径下界超出量`；不用真 II（太贵），II 由后面的 mapper/校验给。
- 移动：单点迁移、两点交换、时间平移；几何降温 0.95，5 次重启，固定种子。
- 输出：全量 `Placement`（不是关键节点子集）。

**(b) `route_check` + `route_encode`**（Python，确定性）
- 校验：路径连续、起止正确、每跳方向在架构连接表内、每跳长度 ≤ 相位差、每 `(PE, cycle, port)` 容量不超。
- 编码：抽象跳序列 → HyCUBE route code（`0..7` 方向 + 跳数），供写 bin 用。

**(c) mapper 校验模式**（C++，关键路径）
- `pins.json` 扩展出 `routes: [{from, to, path: [[x,y], ...]}]`。
- `LLMMapper` 新增 `verify` 模式：按给定 placement + routes 直接构造 mapping，只做占用/端口/时序校验，不跑 PathFinder；输出冲突明细 `{pe, cycle, port, edges[]}`。
- 预计 300–500 行，集中在 `LLMMapper.cpp`，加少量 `PathFinderMapper` 钩子。

**Spike（2 天，go/no-go）**：只做「一条边、一个 II」的注入与校验，跑通即继续；跑不通就止损，臂 B 降级为「SA 布局 + LLM 选 II 与 pins 策略」，并在论文里如实写成负结果。

**(d) `LLMRouter`**
- 输入：每条边的起止 `(x,y,t)`、slack、当前拥塞热图、方向表。
- 输出：跳序列；按冲突簇批量提问，减少调用数。
- 循环：`route_check` 失败 → 带违例规则重问（≤3 次）→ 仍失败则回 SA 换布局（外层 ≤2 次）。

验收：`array_add`、`gemm` 上 II=4/8 能出合法 route 并通过仿真；与臂 C 对比「寻路失败实例上 B 是否成功率更高」。

## 5. 目录与文件

```
src/cgra_agent/
  interfaces.py        # Placement / Routes / MapReport / SimResult / CDD + Placer/Router 协议
  pipeline.py          # S0–S9，与臂无关
  arms.py              # A/B/C/D 的装配
  placers/llm.py  sa.py
  routers/pathfinder.py  llm.py
  tools/dfg.py facts.py validate.py mapper.py sim.py jobdir.py route.py
  agents/place.py route.py diagnose.py report.py
  llm/client.py  llm/prompts/{place,route,diagnose}.md
configs/array_add.yaml  gemm.yaml
bench/run_matrix.py    # 4 臂 × N kernel × seed，出 CSV
tests/                 # 契约测试 + 工具单测
```

## 6. 里程碑

| 阶段 | 内容 | 工期 | 验收 |
|---|---|---|---|
| M1 | pipeline 骨架 + 臂 A 最短链（`array_add`） | 5 天 | `cgra run --arm A --kernel array_add` 出 RunBundle，II ≤ 4 |
| M2 | 对照臂 C：SA 布局 + 现有 mapper | 3 天 | `--arm C` 跑通；与 A 同表对比 |
| M3a | mapper verify 模式 spike | 2 天 | 一条边注入成功，或判定 no-go |
| M3b | `route_check`/`route_encode` + `LLMRouter` + 臂 B | 8 天 | `--arm B` 在 2 个 kernel 上出合法 route |
| M4 | 评测矩阵 + 报告 | 5 天 | 4 臂 × 6 kernel × 3 seed，CSV + 图表 |

M1 完成前不动 C++；M3a 不过则 M3b 改道。

## 7. 评测协议

- **kernel**：`array_add`、`gemm`、`md`、`fft`、`stencil`、`spmv`（以 `Morpher_DFG_Generator/benchmarks` 里实际存在的为准，缺的用相近规模替代）。
- **指标**：II、lat、仿真周期、映射成功率、墙钟、LLM 调用数/tokens、失败恢复率。
- **控制变量**：同一 II 初值、同一 mapper 超时、同一 LLM 预算；每臂 3 个 seed。
- **统计**：配对比较（同 kernel 同 seed），报中位数与四分位；不报单次最优。
- **可复现**：每个 RunBundle 存 config、dfg sha256、pins、routes、report、sim 日志。

## 8. 风险

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| mapper verify 模式改不动 | 中 | 臂 B 无法成立 | 2 天 spike，不过则降级并如实报告 |
| LLM 路径不合法率高 | 中 | 调用数爆炸 | 批量提问 + 规则化重问 + 失败即回 SA |
| SA 质量太差，B 变成「SA 的锅」 | 中 | 结论不可信 | SA 与 C 共用，C 先跑出可用基线，再谈 B |
| 仿真慢，矩阵跑不完 | 低 | M4 延期 | 先 4 kernel，II 只取 2 档 |
| Morpher 上游行为差异 | 低 | 结果不可比 | vendor 快照 + patch 固定，记录 sha256 |
