# ARM B — verify 与仿真：双裁判混淆矩阵

两类裁判：`verify`（静态）与 `hycube_simulator`（动态）。二者都不是权威，两者都错，
方向还可能相反。本文件把 10 个真实 kernel × 5 种配置（pins / k25 / k50 / k100 / rule）
共 50 格逐格判成四类。


> **口径更正（重要）**：本文件下面的第一版 50 格矩阵（`consistent_pass` 5 /
> `static_disagreement` 27 / `consistent_reject` 17 / `no_mapping` 1）是在
> `--pins_mode 3`（`PINS_REPAIR`）下取的。`PINS_REPAIR` 会把 pin 当修复提示、
> 允许改写，因此那张表回答的是"当修复提示时能救回多少"，
> **不是**"当硬约束时 pin 有多少能实现"。严格/偏好模式的对照见下面
> “三个 pins mode 并列”一节。

## 判法

- 每格同一份请求（同一 pins、同一 routes）跑两次：A = 不带 `--verify`，B = 带 `--verify`。
- 仿真喂 **A 的 bitstream**（未被 verify 干预的那一份），所以“verify 拒了”不会把被测对象藏起来。
- 固定 II=4。第一版矩阵用的 `--pins_mode 3` 是 `PINS_REPAIR`（修复提示，不是硬约束），
  严格/偏好模式的对照见下文；pins_mode 不显式给定时 mapper 会静默忽略 `--pins`
  （`pins_applied=0`），第一版矩阵也曾被这个坑污染，已在解析阶段改成硬错误。
- 单元格含义：

| 单元格 | 含义 |
|---|---|
| `consistent_pass` | verify 过、仿真 0 mismatch |
| `over_permissive` | verify 过、仿真报 mismatch —— 静态模型漏判 |
| `static_disagreement` | verify 拒、仿真 0 mismatch —— 静态模型误杀 |
| `consistent_reject` | verify 拒、仿真也失败 |
| `no_mapping` | 映射阶段就没出 bitstream，两裁判都没话说 |

## 结果（50 格）

| 单元格 | 格数 |
|---|---|
| consistent_pass | 5 |
| over_permissive | 0 |
| static_disagreement | 27 |
| consistent_reject | 17 |
| no_mapping | 1 |

`verify=True` 的 5 格全部仿真通过；`verify=False` 的 44 格里有 27 格仿真 0 mismatch。
**在这个网格上静态模型是偏严的，不是偏松的** —— 与 array_add 上 `(20,19)` 那条
`over_permissive`（单边合法、组合触发，见 ARM_B_REPAIR.md §7.1）方向相反。两张表必须一起看。

### 误杀按首条冲突归类

| 首条冲突 | 格数 | 其中仿真通过 |
|---|---|---|
| `hint_route_unrealisable`（注入路线在端口图上不可实现） | 16 | 11 |
| `pin_infeasible_op`（pin 到了不支持该算子的 FU） | 10 | 8 |
| `port_name_unknown`（无法解析该边的端口名） | 8 | 1 |
| `port_shared`（同一端口同周期承载两个来源） | 5 | 5 |
| `pin_slot_conflict`（同一 PE 同周期的 datapath 已被占用） | 4 | 0 |
| `operand_select`（SELECT 两个操作数同周期到达） | 2 | 2 |
| **合计** | **45** | **27** |

读法：27 格误杀里 19 格是**请求本身不可实现**（注入路线不可实现 / pin 打在不支持该算子的 FU 上），
8 格是映射后检查（`port_shared`、`operand_select`）命中。前者说明“拒”的信息量在于**请求**，
后者才是**解**。

## 逐 kernel

| kernel | pins | k25 | k50 | k100 | rule |
|---|---|---|---|---|---|
| array_add | pass | pass | dis | dis | dis |
| array_cond | dis | dis | dis | dis | dis |
| conv2d | dis | dis | dis | dis | dis |
| conv3 | pass | dis | dis | dis | dis |
| gemm | dis | dis | dis | dis | dis |
| gemm_pace | rej(mm2) | rej | rej | rej | dis(mm0) |
| hpcg | rej(mm1) | rej | rej | rej | rej |
| mac | rej(abort) | rej | rej | rej | dis(mm0) |
| mults1 | rej(mm8) | rej | rej | rej | no_mapping |
| trmm | pass | pass | dis | dis | dis |

`pass`=consistent_pass，`dis`=static_disagreement，`rej`=consistent_reject。

`hpcg`(mm1)、`mults1`(mm8)、`gemm_pace`(mm2) 的 mismatch 数在 5 个配置里几乎恒定，
与覆盖度无关 —— 这是 kernel/trace 层面的问题，不是布线质量的问题，不能计入布线结论。

`rule` 行（廉价放置 `t = ASAP % II`、访存节点贴 bank、无 A* 运行）说明一件事：
**廉价放置经常连 pin 都放不合法**（`pin_infeasible_op` 8 格来自这里），
但 `mac`/`gemm_pace` 的 rule 行反而映射成功并仿真通过 —— 它们用 pin 行时因
`pin_slot_conflict`/`port_name_unknown` 被拒。放置质量与“可验证性”不是单调关系。

## 口径警告（写结论时必须带上）

1. A 与 B 两次运行的求解器有随机性，两次可能落成**不同的解**；因此
   `static_disagreement` 严格说是“verify 拒了它自己那份解”，不能直接宣布为
   “某条检查误杀”。要坐实误杀需要确定性种子或单解复算，这是下一步。
2. `over_permissive = 0` 只覆盖本网格；array_add 的 `(20,19)` 组合触发仍是最强的反例。
3. 覆盖度 k 的注入路线取自 A* 自己 routeInfo 的 PE 序列（`bank`），
   其中 16 格被判“端口图上不可实现” —— 说明**回放参考解本身有损**，
   与 `routes_accepted / routes_total` 必须同表报告的要求一致。

数据：`work/armB/confusion/confusion.csv`（50 行，含 `verify_conflicts`、`reject_kind`、`first_msg`），
每格目录下另有 `noverify.log` / `verify.log` / `sim.log` / `*_report.json`。
脚本：`tools/confusion.py`（跑），`cac_scripts/armB/post.py`（归类）。

## 确定性：27 格先被拆成 7 + 1 + 19

“verify 拒、仿真过”只有在两次运行判的是**同一份解**时才有意义。`tools/determinism.py`
对每个相关单元格用完全相同的 pins+routes 连跑两次，比较 `placement`、`routeInfo`、bitstream sha256：

- `pins` / `k25` / `k50` / `k100` 四种配置：24 格**全部逐字段相同**（确定性成立）；
- `rule` 配置：8 格**全部不同**（廉价放置把 mapper 推进了带随机性的搜索路径）。

于是 27 格 `static_disagreement` 重新分成：

| 结论 | 格数 | 判据 |
|---|---|---|
| `request_infeasible` | 19 | 首条冲突是 `hint_route_unrealisable` / `pin_infeasible_op`：拒的是**请求**，不是解 |
| **`false_kill`** | **7** | 确定性成立 + 首条冲突是 `port_shared`(5) / `operand_select`(2)：同一份仿真 0-mismatch 的解被静态判死 |
| `undecidable` | 1 | `port_name_unknown`：verify 拿不到端口名，它没有判，只是没话说 |
| `ambiguous` | 0 | 确定性不成立且无法归入上面几类 |

合计 27，与 `consistent_pass` 5 格相加正好是矩阵里的 32 格可比单元。

`false_kill` 的 7 格：`array_cond` pins/k25（SELECT 同周期）、`conv2d` pins/k25/k50、`conv3` k25/k50（端口同周期两来源）。
这 7 格是可以直接写进结论的：**静态检查在这 7 个实例上把一份硬件接受、仿真零 mismatch 的解判为非法**。

`rule` 行的不确定性本身也是结论：廉价放置的输入相同、输出不同，说明它在 mapper 内部触发了非确定性搜索；
后续凡是以 `rule` 行为依据的数字都需要多跑几次取中位数（与固定 II 表的口径一致）。

## kernel 级归因：hpcg / mults1 不是布线问题

`-m 0`（工具链自带 A*，无 pins、无 routes，II 自由）+ 同一批 trace：

| kernel | 基线 II | 基线 mismatch | 结论 |
|---|---|---|---|
| array_add（对照） | 4 | 0 | 工具链自身在这套 trace 上是对的 |
| hpcg | 4 | 1 | **kernel/trace 级**：工具链自己的解就 mismatch |
| mults1 | 4 | 8 | **kernel/trace 级**：同上 |
| gemm_pace | 9 | 0 | 不是 kernel 级：它在矩阵里的 mm=2 来自 II=4 下的受限映射 |

所以 `hpcg`、`mults1` 在总表里标 `unverified(kernel-level)`，不参与布线结论；
`gemm_pace` 仍需按布线问题处理（它在 II=9 才干净，说明 II=4 对它本身就很紧）。

## 三个 pins mode 并列（50 格 = 10 kernel × {pins,k25,k50,k100,rule}）

`LLMMapper.h` 里 `PinsMode` 有四个值：0 off / 1 strict / 2 prefer / 3 repair。
同一批 pins+routes、同一个二进制、同一批 trace，只换 mode：

| mode | consistent_pass | over_permissive | static_disagreement | consistent_reject | no_mapping |
|---|---|---|---|---|---|
| 1 strict | 5 | 0 | 19 | 12 | 14 |
| 2 prefer | 5 | 0 | 19 | 12 | 14 |
| 3 repair（第一版表） | 5 | 0 | 27 | 17 | 1 |

- **strict 与 prefer 逐字节相同**：50 行的 CSV `diff` 为空，pin 应用数、route 应用数、
  二进制 sha 全部一致。在这批实例上这两档没有区别，只有 repair 会改行为。
- repair 的差别体现在两处：`no_mapping` 14 → 1（rule 行与 gemm_pace 的行被救回来），
  以及 `static_disagreement` 19 → 27（救回来的解里有一批被 verify 拒）。
- 所以“pin 是硬约束时 LLM 的 pin 有多少能实现”这个问题，看 19/12/14 这一列；
  “pin 当修复提示时能救回多少”看 27/17/1。两张表不能混算比例。

## rule 行的方差（4 次重复）

`tools/rulevar.py`，每个 kernel 用同一份 cheap placement（`t = ASAP % II`，内存节点放 bank）
跑 4 次，记录 (verify, sim verdict, cell)：

| mode | 4 次结论是否翻转 | 说明 |
|---|---|---|
| 2 prefer | 0/10 翻转（全部 `no_mapping`） | cheap placement 在硬约束下**根本映射不出来**：pins 只被接受 12/20…28/49，且最终无二进制 |
| 3 repair | 8/8 格翻转（确定性探针） | repair 允许改写 pin，mapper 进入非确定性搜索 |

结论要分两层写：
1. **cheap placement 的 pins 在 strict/prefer 下不可实现**（这不是"更慢"，是 `no_mapping`），
   与 LLM 放置的 4/10 strict 成功率属同一类现象；
2. 之前观测到的 `rule` 行不确定性是 **repair 模式的性质**，不是 cheap placement 本身的性质。
   引用 `rule` 行数字时必须写明 mode。
