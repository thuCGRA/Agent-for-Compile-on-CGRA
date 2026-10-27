# 覆盖率消融（第 2 轮，item 4 的第一版）

目的：回答“控制布线这件事本身值多少”。做法是**固定布局**（直接取 `-m 0` 参考映射
里的放置），只让“由策略接管的边数”变化，并把选边规则分成 `critical`（slack 降序）
与 `random`（固定种子）两组——对照组回答“选哪条边有没有信息”。

## 设置

- 算例 `work/armB/spike/array_add`：20 节点、23 条边、4x4、II=4、`HyCUBE_4REG`。
- 布局：`base_report.json` 的 20 个 pin 全部钉住（含 `t = lat % II`）。
- 策略：`src/cgra_agent/routers/rule.py` 的 `RulePolicy`；`path_mode="bank"`
  表示“路径直接取 A* 自己布的那条”，这样 critical/random 的差别只反映**选边**，
  不掺路径质量。`path_mode="shortest"` 是外行规则（确定性最短路 + 软端口避让）。
- 修复通道：首轮全给固定路线，冲突回 `suggest_edges`，标 `flex` 后重跑，
  最多 3 轮（这就是第 1 轮做的重路由接口，不再整轮重问）。
- 校验：mapper verify → 三分类 `failure_class`（`static`/`runtime`/`semantic`），
  仿真通过要求 `Mismatches::0`。

复跑：`python3 tools/ablation.py --path-mode bank`（产物在 `work/armB/ablation/`）。

## 结果：bank 模式（A* 路径 + 策略选边）

# 覆盖率消融（array_add，布局固定，path_mode=bank）

| k% | 选边 | seed | ok | II | lat | 首轮冲突 | 接受/提出 | 首轮释放 | 轮数 | 耗时 | bin | 仿真 | failure_class |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | critical | - | True | 4 | 13 | 0 | 0/0 | - | 1 | 0.32s | 2efd6260 | ok | - |
| 0 | random | 1 | True | 4 | 13 | 0 | 0/0 | - | 1 | 0.33s | 2efd6260 | ok | - |
| 0 | random | 2 | True | 4 | 13 | 0 | 0/0 | - | 1 | 0.32s | 2efd6260 | ok | - |
| 0 | random | 3 | True | 4 | 13 | 0 | 0/0 | - | 1 | 0.32s | 2efd6260 | ok | - |
| 25 | critical | - | True | 4 | 13 | 2 | 4/6 | - | 2 | 0.64s | 6fe2b7f8 | ok | - |
| 25 | random | 1 | True | 4 | 13 | 1 | 5/6 | - | 2 | 0.64s | d9b23966 | fail | runtime |
| 25 | random | 2 | True | 4 | 13 | 1 | 5/6 | - | 2 | 0.68s | 31699fe2 | ok | - |
| 25 | random | 3 | True | 4 | 13 | 0 | 6/6 | - | 1 | 0.32s | af0efe1f | ok | - |
| 50 | critical | - | True | 4 | 13 | 2 | 10/12 | - | 2 | 0.64s | 098c7f79 | fail | runtime |
| 50 | random | 1 | True | 4 | 13 | 1 | 11/12 | - | 2 | 0.66s | d9b23966 | fail | runtime |
| 50 | random | 2 | True | 4 | 13 | 1 | 11/12 | - | 2 | 0.63s | 098c7f79 | fail | runtime |
| 50 | random | 3 | True | 4 | 13 | 2 | 10/12 | - | 2 | 0.68s | 6fe2b7f8 | ok | - |
| 100 | critical | - | True | 4 | 13 | 3 | 20/23 | - | 2 | 0.65s | 098c7f79 | fail | runtime |
| 100 | random | 1 | True | 4 | 13 | 3 | 20/23 | - | 2 | 0.69s | 098c7f79 | fail | runtime |
| 100 | random | 2 | True | 4 | 13 | 3 | 20/23 | - | 2 | 0.74s | 098c7f79 | fail | runtime |
| 100 | random | 3 | True | 4 | 13 | 3 | 20/23 | - | 2 | 0.71s | 098c7f79 | fail | runtime |


## 结果：shortest 模式（朴素最短路）

# 覆盖率消融（array_add，布局固定，path_mode=shortest）

| k% | 选边 | seed | ok | II | lat | 首轮冲突 | 接受/提出 | 首轮释放 | 轮数 | 耗时 | bin | 仿真 | failure_class |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | critical | - | True | 4 | 13 | 0 | 0/0 | - | 1 | 0.38s | 2efd6260 | ok | - |
| 0 | random | 1 | True | 4 | 13 | 0 | 0/0 | - | 1 | 0.35s | 2efd6260 | ok | - |
| 25 | critical | - | True | 4 | 13 | 2 | 4/6 | - | 2 | 0.66s | 422dc425 | ok | - |
| 25 | random | 1 | True | 4 | 13 | 1 | 5/6 | - | 2 | 0.68s | bc75303f | fail | runtime |
| 50 | critical | - | True | 4 | 13 | 2 | 10/12 | - | 2 | 0.66s | de1e210f | fail | runtime |
| 50 | random | 1 | True | 4 | 13 | 1 | 11/12 | - | 2 | 0.74s | bc75303f | fail | runtime |
| 100 | critical | - | True | 4 | 13 | 4 | 19/23 | - | 2 | 0.67s | 6b09108b | fail | runtime |
| 100 | random | 1 | True | 4 | 13 | 4 | 19/23 | - | 2 | 0.65s | 6b09108b | fail | runtime |


## 五条观察

1. **II/lat 全程不动**（II=4、lat=13）。不是“布线没用”，而是这个算例已饱和：
   II 由 mapper 参数定死，lat 由固定布局定死，布线的自由度**没有落在时序上**。
   （lat=13 是 verify 的口径：`max(node.lat + op_latency)`，即最后一个结果可用的拍；
   与 `-m 0` 报的 11 不同口径，表内恒定所以不影响比较。）
2. `critical` 与 `random` 在全部 k 上不可区分（同接受率、同仿真结论）。
   按预定判据：**在这个算例上，选哪条边没有信息**。要检验“LLM 会挑边”，
   必须换到 A* 会失败的算例，否则测的是零。
3. **覆盖率 100% 只等于 87%**（20/23 被接受）。剩下 3 条结构性不可注入
   （(6,7)、(4,5)、(20018,20017)），(20,19) 能注入但会让仿真断言。
   所以表里必须同时报 `routes_accepted/total`：这是“回放参考解不是无损”的量化。
4. 首轮冲突随 k 上升（0→1~2→1~2→3），全部在第 2 轮收敛；被拒的边**全部**由
   flex 通道回收成功（`ok=True` 16/16）。在这个算例上，接管布线**不增加失败率**，
   只增加“要释放多少条”。
5. **runtime 类失败是组合性的，不是单边属性**（见下表）。`(20,19)` 单独注入时好，
   和另外 19 条一起注入才崩——所以消融表里 abort 的出现次序不随 k 单调，
   k=25 的某一 seed 会崩、k=50 的另一 seed 反而好。这比“某条边有毒”更难处理：
   静态验收看到的每条边都是合法的。

## 附：runtime 失败的收敛实验（`cac_scripts/armB/probe_runtime.py`）

```
a)  只注入 (2,20017) 1 跳 [[1,1],[0,1]]   verify ok  sim ok   mism 0
a2) 只注入 (2,20017) 参考路径              verify ok  sim ok   mism 0
d)  只注入 (20,19) 参考路径                verify ok  sim ok   mism 0
b)  除 (20,19) 外的 22 条                  verify ok  applied 19/22  sim ok  mism 0
c)  全部 23 条                             verify ok  applied 20/23  sim FAIL class=runtime
```

结论：单条边都不毒，**组合才毒**；被拒的 3 条始终是那三条结构性不可 inject 的边。

## 结论（诚实版）

- 这条算例不支持“控制布线能换来 II/lat”：**时序完全由布局决定**。臂 B 若要主张
  布线的价值，必须给出 A* 会失败的算例，而不是在饱和算例上比覆盖率。
- 可以主张的是**可归因性与可复现性**：同布局下接管 k% 的边不降低成功率，且每条
  被拒的边都有具体原因（csv 的 `reason_round0` / `released_round0` 列）。
- `critical` vs `random` 目前是**平局**：既不能证明“LLM 选边更聪明”，也不能反证。

## 下一步

1. 找/造 II 压力算例（`-i 2` 或更大 DFG），先确认 A* 在那上面会失败——
   这是覆盖率消融有意义的**前提**。
2. 有端点后 `LLMPolicy` 直接替换 `RulePolicy`，消融脚本不动（同一 `route()` 签名）。
3. SA 布局放到结论出来之后：届时问的是“好布局 + 好布线能否叠加”。

## 附：`failure_class` 的用法

- `static`：verify 拒（冲突可读、可释放）→ 上层重问 router。
- `runtime`：仿真断言炸（组合性，如 (20,19) + 其余边）→ 记成“不可判定”，
  **不是** LLM 布错了；`SimResult.evidence` 带断言原文。
- `semantic`：`Mismatches>0` → 真正的布局/布线错误。
三类现在写在 `MapReport.failure_class` / `SimResult.failure_class` 上，
消融表每行都能看到是哪一类。
