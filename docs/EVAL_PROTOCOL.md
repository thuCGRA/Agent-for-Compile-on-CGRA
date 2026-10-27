# 评测协议（v1）

所有实验共用本协议。改判读标准必须改本文件并在 FINDINGS 里记原因。

## 1. 固定条件

- 架构：HyCUBE 4x4（扩大阵列的实验单列，不混入主表）。
- II：按 kernel 固定为目标 II（`max(UserGiven, RecMin)`）。不做自由 II 搜索，除非实验名里写明 `free-II`。
- kernel：10 个 —— array_add / array_cond / trmm / conv2d / conv3 / mac / mults1 / gemm / gemm_pace / hpcg。
- 重复：耗时类指标 3 次，报中位数与 IQR。
- 温度 0，同一 prompt 只跑一次；重跑走缓存。

## 2. 指标

主指标：

| 指标 | 定义 |
|---|---|
| `strict_accept` | 首调 strict 模式下被接受的 pin 数 / 总数；另记 per-kernel 是否 ok |
| `lat_gap` | `lat` 相对 `none` 的差（周期） |
| `mapper_time` | 映射墙钟时间，中位数 |
| `llm_calls` / `prompt_tokens` / `completion_tokens` | 每次实验累计 |

次指标：`repair_accept`、`verify_rounds`、`conflicts_first_round`、`sim(matches/mismatches)`。

失败分类：`static` / `runtime` / `semantic` / `toolchain` / `static-disagreement`（verify 拒但 sim 过）。

## 3. 对照

| 名称 | 说明 |
|---|---|
| `none` | `-m 0`，mapper 自己放置+布线 |
| `rule` | 纯规则放置（ASAP%II + bank），无搜索、无模型 |
| `greedy` | 带回溯的贪心放置 |
| `oracle` | mapper 自己 `-m 0` 的放置回灌 strict（价值上限，实测等于 none） |
| `llm` | 仅看 DFG/架构，不看 A* 结果 |

## 4. 判读标准（预先登记）

`llm` 相对 `none`：

- **有贡献**：`strict_accept ≥ 8/10` 且 `lat_gap ≤ +1`。
- **代价旋钮**：`strict_accept` 提升但 `lat_gap > +1`——只说明它换来了别的代价，不算质量收益。
- **无差别**：`strict_accept` 与 `lat_gap` 都在基线噪声内。
- **有害**：`strict_accept < rule` 或 `lat_gap ≥ +3` 且无时间收益。

已有的锚点：`rule` = 3/10，`lat_gap` +3~+5；`greedy` = 10/10 可放置但 strict 全量 pin 被拒 5 个；`none` = 10/10。

## 5. 成本核算

每次实验记录：调用数、prompt/completion tokens、墙钟时间、mapper 调用次数。论文里报"每 kernel 的编译成本"，不报总 token。

## 6. 禁止事项

- 无端点时不得产出数字（必须 `NO_ENDPOINT` 非零退出）。
- 不得把 `oracle` 当 `llm` 的对照而不标注"剪枝上界"。
- 不得把 `verify fail + sim pass` 计入失败。
- 不得在自由 II 数据里混入固定 II 的表。