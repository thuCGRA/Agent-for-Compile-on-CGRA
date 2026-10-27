# 阶段性发现（v1，2026-10-27）

范围：HyCUBE 4x4，10 个真实 kernel（array_add / array_cond / trmm / conv2d / conv3 / mac / mults1 / gemm / gemm_pace / hpcg），mapper 为 vendor PathFinder，II 按 kernel 固定。
数据来源：A 臂 `work/master_table.csv`、`work/eval-density/same_ii.csv`；B 臂 `docs/ARM_B_FIXII.md`、`docs/ARM_B_KNEE.md`；各臂 ARM.md。

---

## 1. 布局不能改变 II

mapper 的起始 II 是 `max(UserGiven, RecMin)`，RecMin 由 DFG 与架构决定。10 个 kernel 中 9 个 RecMin=4、gemm_pace 为 9；请求 II=3 在结构上不成立，能放置的 4 个 kernel 在 II=3 下发 `placement_not_found`。

**含义**：任何"pin 把 II 压下来"的叙事都不成立。固定 II 是唯一现实的实验设定，也是编译器的真实用法。

## 2. 全量 pin 的价值上限 = 0

`oracle` 定义为 mapper 自己 `-m 0` 的放置回灌（strict），与 `none` 的 II / lat / 耗时逐项相同。这是必然结果，但它给出一个硬边界：把 mapper 自己的解喂回去没有任何收益。

要让 pin 有价值，必须拿出**比 mapper 自己更好**的位置，而不是复述它。

## 3. 选择性 pin：可接受、零代价、暂无收益

| 设置 | strict 接受 | lat / 耗时 |
|---|---|---|
| `none`（`-m 0`） | — | 基线 |
| `memory strict`（只钉 LOAD/STORE） | **1/N，10/10 kernel 失败** | — |
| `memory repair`（同上 + repair 模式） | **N/N，10/10 接受** | 与 `none` 持平（±5%） |

只钉内存节点在 repair 模式下几乎零成本被接受，但也没有收益。**这是"选择性 pin"的第一个正证据，同时也说明它单独不足以产生价值。**

失败原因是第三层约束：mapper 的 DP 候选集比我们的校验更窄，`no candidate DP matches pin` 在"最该有用"的内存节点上同样出现。

## 4. 静态模型与真实工具之间存在三层、双向的错位

| 案例 | 谁更严 | 后果 |
|---|---|---|
| R3 相位差 ≥ 曼哈顿距离 | 我们 | 误杀合法布线（HyCUBE 有组合直通） |
| gemm 贪心放置 | 我们（无回溯） | 误报不可放置；10 次回溯后即可放置 |
| DP 候选集 | mapper | strict 拒绝合法 pin |
| `routeInfo -T` 被当作延迟 | 我们 | 8 条虚假 timing 冲突 |
| `DP0_I1` vs `I1` 端口名 | 我们 | verify 崩溃（`getInPort` 断言） |

**结论**：`validate()` 通过 ≠ mapper 能实现，两个方向都会错。

正在建的量化：verify × sim 的 2×2 混淆矩阵（一致通过 / 漏报 / 误杀 / 一致拒绝）。四格已撞见三格——SELECT 断言是"verify 过、sim 崩"，conv2d 反转是"verify 拒、sim 能过"，R3 是"我们拒、mapper 能布"。

## 5. `flex` 通道是必需品

所有 k>0 的实验里 `first_call_ok` 全为 False，至少 2 轮才收敛。首轮必然有 1–6 条边在端口图上不可实现（每张图都有，不是策略错误）。

## 6. 已确认的工具链缺陷

- mapper 报 `ok=true` 但不写 bitstream（`mults1 critical 50%`）；已加 `check_artifacts` 归为 `failure_class=toolchain`。
- `PathFinderMapper.cpp:3871` 内部断言崩溃（同上 case）。
- `fft` 的 DFG 使 mapper 抛 `std::logic_error`（疑似空 bank 属性）。
- `mac` 在 II=4 时 simulator 断言崩溃，II=5 时 167/0 通过。

## 7. 仿真真值

> **2026-10-27 更正：本节数字目前无法复现。** `work/` 下只剩 array_add 一条 trace，其余 kernel 在重跑中全部 `no-trace`；仓库其它位置也没有 trace。这些数字来自已不存在的中间产物，暂作作废处理，见第 12 节。


8/10 kernel 零失配：array_add 387/0、array_cond 243/0、conv2d 523/0、conv3 163/0、trmm 5/0、gemm 783/0（II=5）、gemm_pace 723/0（II=9）、mac 167/0（II=5）。

hpcg 90/1、mults1 79/8 记 `unverified`：trace 是整程序数组快照，DFG 只覆盖最内层循环，循环体边界不对齐。**不计入 pass。**

## 8. 对叙事的含义

原先设想的"agent 把 II 压下来 / 比 A* 布得更好"两条都不成立。目前站得住的是：

1. CGRA 编译链里 II 由结构决定，控制只能作用于可布性与耗时；
2. 静态验证双向不可靠，必须与仿真配对，2×2 矩阵可量化；
3. 全量 pin 是错的工作点；选择性 pin 可行但需证明"选得比 mapper 好"。

LLM 的待验证定位：**不是布得更好，而是在静态模型判不了的地方，用更少的仿真次数找到可用解。**

## 9. 未决

- 廉价规则放置器（ASAP%II + bank）与 `none` 的 lat 差距——决定 LLM 是否有空间。
- 真模型端点：A/B 两臂目前全部数据来自规则基线。
- hpcg / mults1 的 trace 对齐修复。
---

## 10. LLM 放置（真端点，10 kernel）

| kernel | strict 接受 | strict lat | repair 接受 | repair lat | 仿真 |
|---|---|---|---|---|---|
| array_add | 20/20 | 21 | 20/20 | 21 | **abort**（CGRATile.cpp:175） |
| array_cond | 25/25 | 30 | 25/25 | 30 | abort |
| trmm | 11/11 | 12 | 11/11 | 12 | **verified** 15 cyc |
| conv3 | 28/28 | 25 | 28/28 | 25 | abort |
| gemm | 15/26 | — | 20/26 | 25 | **verified** 38 cyc |
| conv2d | 8/39 | — | 33/39 | 36 | abort |
| gemm_pace | 6/49 (II=9) | — | 27/49 | 54 | abort |
| hpcg / mac / mults1 | 失败 | — | 失败 | — | 无 |

基线（`-m 0`）：10/10 可映射，8/10 仿真零失配。

**"mapper ok" 是假判据**：四个"全接受"里三个一跑就崩。按三裁判统计，mode3 下 44/50 格落了 bitstream，三方一致通过的只有 5 格——用它当成功率会报出 88% 的假阳性。

**verify 的漏报为 0**：凡 verify 放行且落了 bitstream 的格子，仿真全过；但它误杀 19–27/50。方向单一：只冤枉对解，不放过错解。早期 `20→19` 那条"verify 过、仿真崩"已撤回——是脚本漏了 `--pins_mode`，pins 被静默忽略。

**思考通道是承重的**：该模型把 `reasoning_content` 当草稿纸，默认会写 20K 字符以上，`max_tokens` 被吃光后 `content` 为空（`finish_reason=length`）。关掉思考（`thinking:{"type":"disabled"}`）后调用变快、变便宜，但放置质量整体崩掉：strict 接受从 4/10 降到 0/10，最好的一格只有 17/24。所以数据必须标注思考开/关，两者不可混用。

**定向修复**：在反馈里显式给出 `CONFLICT nodes` 并要求"其余节点逐字不动"，模型确实只动被点名的节点（gemm、mults1 各移动 1 个，且都落在名单内）。但单点移动不足以让 strict 通过——被点名的节点往往本身就没有合法 DP 候选。说明"改一个点"与"让整张图可布"不是同一件事。

## 11. 目前的结论边界

能站的：
1. II 由结构决定，布局改不了；
2. "mapper ok" 不能当成功判据，必须过仿真；
3. verify 在这批算例上零漏报、高误杀，适合当保守闸门而非裁判；
4. 全量 pin 是错的工作点；选择性 pin 可接受但无收益；
5. 直接让 LLM 出全图放置，功能性 2/10，延迟约为基线 2 倍。

不能站的：LLM 能比 A* 布得更好；反馈重试能修复映射；pin 能压 II。
## 12. 证据完整性更正（2026-10-27）

**一、LLM 响应空转。** 审计 48 份缓存响应，10 份 `content` 为空。原因是推理模型把 token 预算耗在 `reasoning_content`（单次 2 万字符以上），`max_tokens=4096` 用尽后正文为空、`finish_reason=length`。评测把空串记成"模型给不出放置"，属于装置故障。已改：预算抬到 18432、空响应重试、`finish_reason`/`empty` 入日志。

**二、重跑后的可映射性（II 固定，`work/eval-llm2`）。**

| kernel | strict | lat | repair | lat | 基线 lat |
|---|---|---|---|---|---|
| array_add | 20/20 | 22 | 20/20 | 22 | 11 |
| trmm | 11/11 | 12 | 11/11 | 12 | 6 |
| gemm | 20/26 | — | 24/26 | 33 | 13 |
| conv2d | 29/39 | — | 38/39 | — | 13 |
| conv3 | 3/28 | — | 22/28 | 26 | 13 |
| array_cond | 9/25 | — | 46/25* | 26 | 13 |
| mults1 | 5/25 | — | 22/25 | 30 | — |
| hpcg / gemm_pace / mac | 失败 | — | 失败 | — | — |

\* `46/25` 说明 `pins_applied` 仍会把超出节点数的计数报出来，是待修 bug，不是模型行为。

strict 可映射 2/10、repair 可映射 7/10；**凡成功者延迟都是基线的 1.8–2.6 倍**。

**三、功能性验证仍缺位。** 10 个 kernel 只有 array_add 有 trace，其余 `sim=no-trace`。32 位 trace 生成被 `gcc-multilib`/`libc6-dev-i386` 缺失阻塞（需 sudo 或 docker）。因此现在能引用的只有"可映射性 + 延迟"，**没有任何一条 LLM 放置经过仿真确认**；此前记录的 trmm/gemm "verified" 一并作废，等 trace 重建后重测。

**四、教训。** 这类评测里，"工具没跑"和"跑了但失败"必须分开记：空响应、缺 trace、缺 bitstream 都曾以"失败"的形态进入表格。先把 `status` 拆成 `ok / no-trace / no-binary / abort / timeout`，再谈结论。
