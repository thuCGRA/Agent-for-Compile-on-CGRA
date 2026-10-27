# ARM B — verify 与仿真：双裁判混淆矩阵

两类裁判：`verify`（静态）与 `hycube_simulator`（动态）。二者都不是权威，两者都错，
方向还可能相反。本文件把 10 个真实 kernel × 5 种配置（pins / k25 / k50 / k100 / rule）
共 50 格逐格判成四类。

## 判法

- 每格同一份请求（同一 pins、同一 routes）跑两次：A = 不带 `--verify`，B = 带 `--verify`。
- 仿真喂 **A 的 bitstream**（未被 verify 干预的那一份），所以“verify 拒了”不会把被测对象藏起来。
- 固定 II=4，`--pins_mode 3`（硬 pin）。pins_mode 不显式给定时 mapper 会静默忽略 `--pins`
  （`pins_applied=0`），第一版矩阵就是被这个坑污染的，已修。
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
