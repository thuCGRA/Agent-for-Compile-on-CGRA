# 05 · ILP / CP-SAT 映射（搬运自 CGRA-ME 的思路）

## 一句话

用一个精确求解器（OR-Tools CP-SAT）回答「给定 II，是否存在一套放置+调度的方案」，
把结果作为 `--pins` 回灌给真实的 `cgra_xml_mapper`，由它做 A* 布线、由仿真器做最终判定。

**求解器不产生最终答案，只产生候选。** 只有仿真 `Mismatches::0` 才算映射成功。

## 为什么要它

`cgra_xml_mapper -m 0`（PathFinder）是贪心启发式：按拓扑序放节点、逐边 A*、失败就 II++ 重来。
它在三个 kernel 上从不回溯，所以它的答案看起来"权威"，但没有任何东西告诉我们
**同一个 II 下是否还有别的解、或者这个 II 到底可不可行**。

ILP/CP-SAT 的价值不在快（它慢得多），而在于它能把

* "搜不到解" 和
* "确实无解"

分开。这是启发式永远给不出的信息。

## 代码位置

| 文件 | 作用 |
| --- | --- |
| `tools/pins/add_pins_hook.py` | 给 mapper 打 `--pins` / `--pins_mode` 钩子（幂等，8 处编辑，带 `PINHOOK` 标记） |
| `tools/pins/extract_pins.py` | 从 `*.routeInfo.log` 抽出 placement，写成 pins 文件（唯一能拿到相位的产物） |
| `tools/ilp/ilp_mapper.py` | CP-SAT 求解器 |

依赖：`ortools`（本 worktree 用独立 venv `.venv`，不碰 `cgra` / `famou` / `cgra-tc`）。

```bash
python3 -m venv .venv && .venv/bin/pip install ortools
.venv/bin/python tools/ilp/ilp_mapper.py --dfg out/array_add/dfg.xml \
    --arch out/array_add/arch.json --time-limit 60 -o /tmp/pins.txt
bash scripts/build.sh            # 带 --pins 钩子重编 mapper
```

## mapper 侧的钩子

`--pins <file>` 每行 `idx x y t`，`#` 开头为注释，`t < 0` 表示只约束空间位置。
`--pins_mode`：

| 值 | 语义 | 行为 |
| --- | --- | --- |
| 0 | off | 完全忽略 pins（默认，与上游一致） |
| 1 | strict | 每个节点都必须命中 pin；不在候选集里就整体失败 |
| 2 | spatial | 只比 `(x, y)`，相位仍由 mapper 决定 |
| 3 | prefer | 命中则收窄候选集，没命中就保留全部候选继续 |
| 4 | repair | 同 3，但日志措辞区分（给 agent 用） |

钩子插在 `PathFinderMapper::Map()` 里 `estimateRouting()` 成功之后、真正选 dest 之前
（`src/mapper/src/mapper/PathFinderMapper.cpp:1442` 附近）：

```cpp
isEstRouteSucc = estimateRouting(node, estimatedRoutes, &failedNode);
// PINHOOK: honour an externally supplied placement
if (isEstRouteSucc && !applyPins(node, estimatedRoutes)) {
    failedNode = NULL;
    isEstRouteSucc = false;
}
```

`applyPins` 把 `estimatedRoutes` 整个 pop 出来、按坐标过滤、再推回。
strict 模式下 pin 不在候选集里直接 `return false`；prefer/repair 则把全部候选推回。

## CP-SAT 模型

变量（每个节点一组）：

```
at[n][s]   bool    节点 n 占用槽位 s，s = (PE, phase)
pe[n]      int     由 at[] 的和约束出来
ph[n]      int     同上
h[e]       int     边 e 的跳数（目标函数用）
d[e]       int     边 e 路由自身消耗的周期数
```

约束：

```
sum_s at[n][s] == 1                     每个节点恰好放一次
sum_n at[n][s] <= 1                     每 PE 每相位至多一个算子
at[n][s] == 0                           op 不被该 PE 支持 / bank 不匹配
AddAllowedAssignments(pe_u, ph_u, pe_v, ph_v, h_e, d_e)   时序 + 距离表
```

目标：`min sum(10 * h_e + d_e)` —— 先压布线压力，再压路由延迟。

## 时序规则（本工程最花时间才搞对的部分）

真实映射器里，相位推进只按**生产者**的算子延迟：

```cpp
// PathFinderMapper::estimateRouting，`dest` 是生产者所在 datapath
int latency = parentFU->supportedOPs[node->op];
```

并且 PE 之间的传输在**同一个 cycle 内完成**（HyCUBE 的组合旁路），
所以一条边的完整规则是

```
t_v = (t_u + L_u + d + nextiter * II) mod II,    d ∈ {0, 1}
```

`d = 0` 是纯组合通路，`d = 1` 是穿了一级寄存器。

这条规则是**从 mapper 自己的解反推并逐边验证**出来的，不是猜的：

| kernel | 边数 | `d ∈ {0,1}` 成立 | 用目的端延迟 |
| --- | --- | --- | --- |
| array_add | 23 | **23/23** | 20/23 |
| mac | 28 | **28/28** | 25/28 |
| trmm | 12 | 11/12 | 10/12 |

trmm 的那 1 条例外是 `u=3 -> v=2`（`OLOAD`，`t_u=0, t_v=1, L=2`，残差 3），
属于多周期访存算子的相位回绕，`--route-cycles 2` 可以把 `II-1` 也放进候选。

**踩过的三个错**（都留了痕迹，别再犯）：

1. `<Output>` 正则要求 `NPB` 必填 → 丢掉 17/23 条边。DFG 里两种写法并存：
   `<Output idx="12" nextiter="0" NPB="0" type="P"/>` 与 `<Output idx="1" nextiter="0" type="I1"/>`。
2. `RecMinII` 首版算错（array_add 报 1，真值 4）。正确做法：枚举简单环，
   `delay = sum(lat)`、`dist = sum(nextiter)`、`need = delay if dist == 0 else ceil(delay/dist)`，取最大。
3. 假设每跳消耗 1 周期（`t_v = t_u + L + h`）→ 三个 kernel 全 INFEASIBLE。
   证据链：只加放置约束时 OPTIMAL，一加时序就 INFEASIBLE。

## 实测（`ilp-mapper` 分支，4×4 HyCUBE，II=4 起点）

| kernel | 节点 | CP-SAT 结果 | 耗时 | strict (mode 1) | prefer (mode 3) |
| --- | --- | --- | --- | --- | --- |
| array_add | 20 | FEASIBLE，hops=10 | 60 s | **II=4，`243/0`** | II=4，`243/0`，0 pin 被拒 |
| mac | 22 | FEASIBLE，hops=15 | 63 s | 失败（node 4 相位不符） | **II=4，`167/0`**，2 pin 被拒 |
| trmm | 11 | **OPTIMAL**，hops=3 | 6.8 s | 失败（node 1 相位不符） | II=5，`5/0`，5 pin 被拒 |

基线（PathFinder 自解）三个 kernel 都是 II=4、`Mismatches::0`。

**怎么读这张表**

* array_add 的 strict 通过是关键证据：**一整套外部算出来的放置+相位被 mapper 原样接受并仿真通过**。
  这说明 pins 接口是真的接上了，不是摆设。
* mac 的 strict 失败是相位问题：`estimateRouting` 给每个候选 DataPath 带的是
  **mapper 自己算出来的相位**，我们的相位一旦和它不同就被过滤掉。mode 3 放弃相位后立刻通过。
* trmm 的 mode 3 反而把 II 从 4 推到 5：我们的空间放置让它有 5 个 pin 无法兑现，
  被迫换位置后布线更紧。**ILP 的解不比 PathFinder 好**，这在当前成本模型下是预期的 ——
  模型里没有端口容量、没有链路容量、没有寄存器复用。

## 时序模型：bypass 与 register 两种织物

CP-SAT 里一条边 `u -> v` 的代价是 `L_u + d`，`d` 是**路由本身**吃掉的周期数。
`d` 能取什么值，取决于织物能不能把 PE 输出组合直通到下一个 PE：

- `--timing bypass`（默认，HyCUBE）：输出可以不经寄存器、穿过 crossbar 直接进下一个 PE，
  所以 `d` 在 `{0, 1}` 里自由取，**与跳数无关**。在 array_add 基线上量了全部 23 条边，
  `(hops, d)` 分布 = `{(0,0):7, (0,1):1, (1,0):5, (1,1):1, (2,0):4, (2,1):3, (3,0):2}`
  —— 3 跳的边同样有 `d = 0`。硬套"每跳一拍"这三条边直接无解。
- `--timing register`（常规 CGRA）：每个 PE 输出都打一拍，`h` 跳就是 `h` 拍，
  同 PE 内部也要一拍，故 `d == max(h, 1)`。

实测（II 从 4 起，`--time-limit 45`，`--route-cycles 1`）：

| kernel | timing | CP-SAT | strict 回灌 | prefer 回灌 | 仿真 |
| --- | --- | --- | --- | --- | --- |
| array_add | bypass | FEASIBLE hops=10 d=6 | II=4 | II=4 | `243/0` |
| array_add | register | FEASIBLE hops=19 d=26 | 失败 | II=5 | `243/0` |
| mac | bypass | FEASIBLE hops=15 d=7 | 失败 | II=4 | `167/0` |
| mac | register | FEASIBLE hops=26 d=32 | 失败 | II=5 | `167/0` |
| trmm | bypass | OPTIMAL hops=3 d=5 | 失败 | II=5 | `5/0` |
| trmm | register | OPTIMAL hops=7 d=13 | 失败 | II=5 | `5/0` |

读法：register 模型的 hops 与 d 都明显更大（array_add 10/6 → 19/26），
但 II=4 上仍 FEASIBLE —— 说明模型能表达"保守织物"；
bypass 多出来的自由度正是 HyCUBE 那条组合路径。
两种模型的解都能被真实 A* 布线 + 仿真接受（prefer 模式），
所以"是否利用组合直通"目前只影响 II 与拥塞，不影响正确性。

## 已知限制（别当成 bug 去修）

1. **相位归 mapper 所有**。想在 strict 模式下稳定命中，就得让 CP-SAT 复刻
   `estimateRouting` 的候选集生成逻辑（包括它依赖已映射节点状态的增量行为），
   那等于重写调度器。当前不划算。
2. **成本模型太薄**。`10*h + d` 只压线长，不压端口/链路/寄存器。要真打赢 PathFinder，
   得把 `PathFinderMapper` 的拥塞与端口占用建进模型，或者改成
   "ILP 给上界 + 真实 router 仲裁"的迭代结构（CGRA-ME 的 `CompositeMappers` 就是这么干的）。
3. **规模**。20–22 节点、II=4 已经要 60 s 才 FEASIBLE（没证最优）。节点数翻倍会爆。
   真正的用处是**小图仲裁**（≤20 节点）和**不可行性证明**，不是替代启发式。
4. **没有端口/链路容量**，所以解出来的 hops 数字没有物理意义，只是相对压力的代理。

## 下一步（按价值排序）

1. 把 ILP 定位从"求解器"改成"仲裁器"：只在启发式失败时进场，回答"这个 II 到底有没有解"。
   这是它唯一不可替代的能力。
2. 若要它真正参与寻优，先补端口容量约束（每 PE 每相位出端口数），再看能否在 60 s 内解完。
3. SA 搬运（用户原始需求的另一半）留待下一轮。

## 参考

CGRA-ME v2.0.1 的 ILP 实现（`src/core/ILPMapper.cpp`，13 组约束、`computeIIS()` 求最小不可行子系统）
**只读思路，未拷代码** —— 它是"仅限非商业非营利"许可，而本工程是 MIT。
CP-SAT 建模是本工程自己写的。

---

## 8. 真布线层：`tools/ilp/ilp_router.py`

`ilp_mapper.py` 只回答"每个节点放在哪个 PE、哪个相位"，对每条边只记一个跳数 `h`
和一个路由周期数 `d`，**从不说明值实际经过哪些 PE**。真正的走线仍由 mapper 的
A*（`PathFinderMapper::estimateRouting`）逐节点贪心生成，所以一个在聚合意义下
合法的放置，仍可能被 mapper 拒掉——这正是 strict 模式下 mac/trmm 报
`pin (1,1,1) not among 16 candidate(s)` 的原因。

`ilp_router.py` 补上这一层：**给定放置，把每条边真正布出来，或者证明布不出来。**

### 8.1 资源图：直接由 `PE.CONNECTIONS` 推出

从 `arch.json` 的 `PE.CONNECTIONS` 逐条读出 HyCUBE 的微架构：

```
FU0.DP0_T -> {NORTH_O, EAST_O, WEST_O, SOUTH_O, TREG}      # 到四个输出口不花周期
X_I       -> {XR, X_XBARI}                                  # XR 是 1 拍输入寄存器
X_XBARI   -> {FU0.DP0_I1, FU0.DP0_I2, FU0.DP0_P, *_O}
TREG      -> {FU0.DP0_I1, FU0.DP0_I2, FU0.DP0_P, *_O}
```

一个值从 FU 输出口出发，经输出口同拍到达邻居的输入口，再穿交叉开关到 FU 输入端，
**全程同一拍**。唯一能花掉一拍的地方就是过一次寄存器：输出侧的 `TREG`，或输入侧的
`NR/ER/WR/SR`。于是资源图的节点是 `(x, y, t)`（`t` 对 II 取模），只有两种弧：

| 弧 | 形状 | 代价 |
| --- | --- | --- |
| bypass | `(x,y,t) -> (x±1,y,t)` / `(x,y±1,t)` | 1 hop，0 cycle |
| register | `(x,y,t) -> (x,y,t+1)` | 0 hop，1 cycle |

一条 DFG 边 `u -> v` 就是一单位流，从**生产者的输出时刻** `(t_u + lat_u) mod II`
出发，流到消费者所在 PE 的相位 `t_v`。因此流经过的 register 弧数恒等于
`ilp_mapper.py` 的 `d`，bypass 弧数恒等于 `h`——两层用的是同一个时序模型。

### 8.2 组播：容量按生产者算，不按边算

基线 `routeInfo.log` 里 node 0 的三个孩子共享**同一条第一跳**
`PE_X1_Y2-T0.EAST_O -> PE_X2_Y2-T0.WEST_I`。所以一个生产者的值可以在一条弧上广播，
两个不同生产者则不行。实现上用 `y[prod][arc]` 布尔变量做或运算，每条弧
`sum(不同生产者) <= cap`。这等价于 CGRA-ME `ILPMapper` 里的扇出变量 `S`。

### 8.3 用法

```bash
# 布一个给定放置
.venv/bin/python tools/ilp/ilp_router.py \
    --dfg out/mac/dfg.xml --arch out/mac/arch.json \
    --pins /tmp/pins_mac_bypass.txt --ii 4 --time-limit 90 \
    -o /tmp/routes_mac.json

# 与 mapper 自己的 A* 逐边对比空间跳数
.venv/bin/python tools/ilp/ilp_router.py ... \
    --compare-routeinfo out/mac/m0/dfg.xmlarch.json_MTP=1_Iter=1.routeInfo.log
```

`--pins` 的格式是每行 `idx x y t`，与 `tools/pins/extract_pins.py` 的输出一致；
`t < 0` 的相位未知项不能布线，会直接报错。`--link-capacity` / `--reg-capacity`
默认 1，`--max-hops` 默认 4。

### 8.4 三组实测

**(a) 与 `ilp_mapper.py` 的代价函数逐项吻合（5/5）**：同一放置下，router 解出的
register 总数正好等于 `ilp_mapper` 目标里的 `d`，hops 等于 `h`：

| kernel / 模式 | ilp_mapper `h/d` | ilp_router `hops/regs` |
| --- | --- | --- |
| array_add bypass | 10 / 6 | 10 / 6 |
| mac bypass | 15 / 7 | 15 / 7 |
| mac register | 28 / 32 | 28 / 32 |
| trmm bypass | 3 / 5 | 3 / 5 |
| trmm register | 7 / 13 | 7 / 13 |

**(b) 与 mapper 的 A* 在同一放置下逐边对比空间跳数**（`--compare-routeinfo`）：
CP-SAT 布出的路径**不劣于** A*，且多数边更短。差异边全是 CP-SAT 更短：
mac `20019` 5 vs 9、trmm `7` 2 vs 4、trmm `20008` 4 vs 6。三者总耗时 0.2–1.1 s。

**(c) 决定性结果——被 strict 拒掉的放置其实可布**：把 mapper strict 模式拒绝过的
放置喂给 router，全部 **OPTIMAL**，用时不到 1 秒：

| kernel | 放置来源 | mapper strict | ilp_router |
| --- | --- | --- | --- |
| mac | `--timing bypass` | `pin (1,1,1) not among 16 candidate(s)` 失败 | hops=15 regs=7，0.56 s |
| trmm | `--timing bypass` | `pin (0,2,3) not among 14 candidate(s)` 失败 | hops=3 regs=5，0.21 s |

结论：**这些放置本身是可布的，瓶颈在 mapper 逐节点贪心的 A\* 估计，不在放置。**

### 8.5 已知限制（必须诚实标注）

1. **端口级争用被折叠了**。容量按"PE 间链路 / 该 PE 的寄存器"计，没有区分
   `NORTH_XBARI` / `EAST_XBARI` 等具体交叉开关端口，也没区分输入侧 `NR/ER/WR/SR`
   与输出侧 `TREG` 的独立容量。所以模型对端口冲突是**略乐观**的——它比原来的
   "只知道跳数"严格得多，但还不是逐端口精确。
2. **不产生 mapper 能读的布线文件**。acc-thu 的 mapper 只接受 `--pins`（放置），
   没有"按给定路线落位"的入口（cac 里的 `--verify` 模式没有搬过来）。所以 router
   的路径目前只用于**判定与量化**，不是直接驱动 mapper。
3. 组播是"同生产者免费共享弧"，但没有建模"一个值可以同时送往多个方向的多个端口"
   的端口数上限。
4. 大规模 kernel 上变量数为 `边数 × 弧数`，弧数 ≈ `4·x·y·II + x·y·II`；4×4、II=4
   约 256 条弧，够用；8×8 或大 II 会明显变慢。

### 8.6 下一步（按价值）

1. 把 router 做成 `ilp_mapper.py` 的**可行性过滤器**：放置解出来 → 布线 → 不通就
   作为 no-good 回灌（两阶段闭环，就是 CGRA-ME `ILPHeuristicMapper` 的做法）。
2. 给 mapper 加 `--routes`（按外部路线落位），让 router 的路径真正驱动仿真。
3. 把端口级容量加进资源图（限制每个 crossbar 输入端的扇入）。
