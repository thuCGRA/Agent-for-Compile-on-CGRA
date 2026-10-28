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

## 13. 验证器校准：用 mapper 自己的解当标尺（2026-10-28）

**方法**（`scripts/exp_verifier_mutation.py`，不依赖 trace）：对 10 个 kernel 先让 mapper 自己布一版（method 3 + 空 pins + repair，等价 PathFinder 但会写 report），把它当作"一定可布"的标尺解；再注入 4 类结构违规（LOAD 挪到非内存 PE、依赖距离压成 0、坐标重复、越界），看 `validate()` 能不能拒。

**先说一个方法学陷阱。** 第一版我按请求的 II=4 去校验，得到"5/10 标尺解被拒、gemm_pace 报 28 条错"。改成用 report 里 mapper **实际用的 II** 后，gemm_pace 的 28 条全部消失。**II 取错会让假阳性翻十倍**，之前的"高误杀"结论里至少有一部分是这个原因。

**修正 II 后的结果。**

| kernel | 实际 II | 标尺解被拒 | 注入检出 |
|---|---|---|---|
| array_add | 4 | 0 | 4/4 |
| array_cond | 4 | 3 | 4/4 |
| conv2d | 4 | 3 | 4/4 |
| conv3 | 4 | 1 | 4/4 |
| gemm | 4 | 0 | 3/4 |
| gemm_pace | 9 | 0 | 3/4 |
| hpcg | 4 | 0 | 3/4 |
| mac | 4 | 2 | 4/4 |
| mults1 | 4 | 0 | 4/4 |
| trmm | 4 | 0 | 2/3 |

- **误杀率 4/10**，全部 9 条都是同一句：`child phase X is 0 cycle(s) after parent`。
- 结构违规（bank / dup / bounds / dep0）检出 **100%**；唯一漏的是 `swap_t`——但交换两个节点的相位本来就可能是合法的，这是变异设计问题，不算验证器弱。

**这条误杀说明什么。** HyCUBE 存在同周期旁路（组合直通），所以"相位差 >= 延迟"作为硬约束是**不成立**的；它恰好也是上一轮 LLM 放置被大面积误拒的同一个原因。而如果把这条规则降级成告警，`dep0` 这类注入就会同时失去检出——**静态规则在这里无法区分"合法的同周期旁路"和"把依赖压成 0 的违规"，这是模型的表达力上限，不是实现 bug。**

结论：验证器只能当**预筛**（省 mapper 时间），不能当判据；真正的判据只能是 mapper 的布线结果，加上 trace 的仿真。

**下一步（待 sudo 解开 trace）**：对同一批变异跑 mapper，量"验证器拒 / mapper 也拒"的重合度，算出预筛到底省了多少时间、漏放了多少。

## 14. 第一次带 trace 的功能验证（2026-10-28）

`gcc-multilib` 装上后 10 个 kernel 都有了 trace（1–4 条）。用 LLM 的 repair 放置回放，`placer=fixed` 直接进 mapper，绕开静态校验，让模拟器说话：

| kernel | 结果 | 证据 |
|---|---|---|
| trmm | **通过** | Matches 5 / Mismatches 0，15 cycles，II=4 lat=12 |
| array_add | 失败 | `CGRATile.cpp:175` SELECT 同周期收到两个操作数，PE(1,0) cycle 14 |
| gemm | 失败 | 同上，同一断言 |
| conv3 | 失败 | 同上，同一断言 |
| conv2d | 失败 | mapper `PathFinderMapper.cpp:3895` 断言（打印 bitstream 时） |
| mac / hpcg | 失败 | mapper 路由失败，4 轮重放无解 |
| array_cond / mults1 / gemm_pace | 未完成 | 批量跑超时或未落 sim.txt |

**唯一通过的是 trmm，规模也最小（11 节点、5 个访问）。** 其余四个进入仿真的 kernel，三个倒在同一个断言上。

**这个断言值得单独说。** `SELECT` 的两个操作数必须在不同周期到达同一 PE——HyCUBE 的输入寄存器一周期只能收一个。四台 kernel 全都栽在这里，且失败点高度一致（PE(1,0)、cycle 14 附近），说明这是 LLM 放置的系统性习惯：把 SELECT 的两个父节点排在相邻相位，看起来"差一拍"，但经过布线跳数之后，两个值在同一周期抵达。

**静态校验看不见它。** 我对同样的 10 份 pins 跑 `validate()`：array_add / gemm / conv3 **零错误**，却全部在仿真中崩掉；反过来 array_cond / mults1 / gemm_pace 各有 1–2 条错误（重复坐标），与仿真结果也没有对应关系。

原因很直接：**这个约束依赖布线距离，不依赖相位差**。两个父节点相位差 1，但一个走 1 跳、一个走 3 跳，到达时刻就相同了。放置层的静态模型根本拿不到这个量。

**所以验证的层级要换。** 现在做的是"放置级静态校验"，正确的位置是"**布线后校验**"：mapper 已经写出 `routeInfo` 和每跳的延迟，用真实路径算到达周期，再判操作数互斥——这仍然是静态的（不用仿真），但必须在布线之后。这也正好解释了为什么 verify 在放置层既拦不住真错误（零漏报是假象，只是没能力报告），又误杀合法解。

**下一步**：把校验器从 `validate(placement)` 改成 `validate(routes)`，读 `routeInfo.log` 的逐跳信息；先用 trmm 的通过样本和 array_add 的崩溃样本对拍，确认它能同时给出"通过/拒绝"且理由与仿真一致。

## 15. 布线级校验：拿到真值，但模型还没对准

用 `SIMULATOR=1` 跑崩掉的 array_add，模拟器在断言前打印了完整现场：

```
kII =14,PC =2,y=0,x=1
NPB=0,OP=SELECT,CONSTVALID=0,CONST=0
P=INV,I1=0,I2=1,...
```

即 PE(1,0) 在第 14 周期执行 SELECT，`I1` 与 `I2` 同时有效，触发 `CGRATile.cpp:175` 的 `!(I1isvalid && I2isvalid)`。

这条规则只对 mux 类操作成立（SELECT/CMERGE 用 P 在 I1/I2 之间选），普通 ALU 两个操作数同周期到达是合法的——这正是放置级 `validate()` 看不见、也不该管的东西。

`scripts/verify_routes.py` 按这个理解实现：解析 `routeInfo.log`，取每条边的终点操作数槽位与时间槽，只在 SELECT/CMERGE 上判“两操作数同槽位”。

结果：array_add / gemm / conv3 都在 PE X1Y0、t=0 命中，与模拟器的 PE(1,0) 一致；**但 trmm 也被命中 2 条，而 trmm 仿真通过**。说明 `routeInfo.log` 里的 `T` 是配置槽位而不是绝对周期，且同一父边会在 WR_RI/WR_RO 多级重复出现，直接当成到达时刻会重复计数。

结论：判断的**对象**找对了（SELECT 的两个操作数槽位），**时间轴**还没对准。下一步不再从 `routeInfo` 反推，而是直接用模拟器的逐周期打印标定到达时刻，再回到静态侧拟合。

## 16. 借用 Morpher 自带的放置器：SA 放置 + A* 布线，6/10 功能通过

不再自己写 SA，而是直接用 mapper 的内置放置器（`-m 1` = SAMapper，`-m 2` = LISAMapper），
用 `--dump-placement` 把最终放置读回来，作为 B 臂的布局来源（`placers/morpher.py`，
提交 `362b406`）。

| kernel | SA 放置 + A* 布线 | 功能验证 |
|---|---|---|
| array_add | II=4 lat=11 | 387/0 |
| array_cond | II=4 lat=13 | 243/0 |
| trmm | II=4 lat=6 | 5/0 |
| conv2d | II=4 lat=15 | 523/0 |
| conv3 | II=4 lat=13 | 163/0 |
| gemm | II=4 lat=13 | 783/0 |
| mac | 布局合法 | 失配 |
| mults1 | 布局合法 | 8 处失配（trace 对齐已知问题） |
| hpcg | 布局合法 | 1 处失配（同上） |
| gemm_pace | SA 产出 t=4，超出 II=4 的相位范围，被校验器拒绝 | — |

对照 LLM 放置的同一批 kernel：只有 trmm 通过，其余倒在 `CGRATile.cpp:175` 或 mapper 断言。

这是目前最硬的一组对照：**同一后端、同一 II、同一 trace，SA 放置 6/10 功能正确，LLM 放置 1/10。**

两条已知边角：
- `gemm_pace` 的 SA 不遵守 `-i 4` 的相位上界（给出 t=4）；这是 Morpher 侧的行为，校验器拦下是对的，但说明"借来的放置器"不能当作无条件正确。
- `mac/mults1/hpcg` 的失配与 LLM 臂同源（trace 对齐），不是放置差异。

## 17. LLM 放置为什么失败：两次修复实验把原因指到"接口"上

先分清三类失败，它们不是一回事：

| 失败 | 数量 | 现场 |
|---|---|---|
| 映射阶段就拒绝 | 5/10 | strict 模式下 `no candidate DP matches pin`，LLM 给的 (x,y) 在 mapper 的 DP 候选集里不存在 |
| 映射成功、仿真崩溃 | 3/10（array_add / gemm / conv3） | `CGRATile.cpp:175`，SELECT 的两个操作数同周期到达 |
| 映射与仿真都过 | 1/10（trmm） | — |

### 修复尝试一：只改相位，保留 LLM 的空间选择

对每个 SELECT/CMERGE，把两个父节点的相位差从 1 拍改成 2 拍（`scripts/exp_select_fix.py`）。

- array_add：原本 II=4 lat=22 崩溃 → 改完**映射直接失败**；
- gemm / conv3：本来映射就失败，改完仍失败。

相位一动，原本可行的 pin 组合就被破坏了。说明时间与空间在这里是耦合的，单独修时间不成立。

### 修复尝试二：空间用 LLM，相位用 DFG 的 ASAP%II

- array_add / conv3 / conv2d 三格**全部映射失败**（strict 拒绝 pin）。

这条最有信息量：把相位换成规则给的合法值之后仍然映射不上，说明**卡住的是空间部分**，不是时间。

### 结论

失败链条是：

1. LLM 选 (x,y) 时不知道 mapper 的 DP 候选集，严格模式下 50% 的节点直接不可实现；
2. 侥幸可实现的那些，相位又是按"差一拍"的直觉给的，经布线跳数放大后撞上 SELECT 的互斥；
3. 两个修复方向都撞在同一个地方：**只要 pin 是精确的 (x,y,t)，任何单侧修正都会让另一侧失效**。

所以问题不在提示词，也不在模型能力，而在**接口**：精确 pin 是一个过强的承诺，LLM 无法可靠地兑现。证据也支持这个判断——唯一被 100% 接受且零代价的，是"只钉内存节点 + repair 模式"这种弱承诺。

下一步该做的是弱约束接口（区域、bank、相对次序），而不是继续改提示词让模型猜得更准。
