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
