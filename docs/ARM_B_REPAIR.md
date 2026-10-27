# 臂 B 第二轮：撕裂-重路由接口（mapper `--verify` 侧）

结论：mapper 现在能**把上游给的 placement+route 落到端口图上，并且把落不下的边按规则分类报回来**；
调用方（LLM/placer/agent）读报告，把被点名的边加进 `flex` 列表重跑一次，A* 接管这几条边，
其余固定路线一条都不用动。整条链路（含二进制打印与 `hycube_simulator`）已经跑通，见 §3。

## 1. 输入里新增的东西

`routes.json` 增加一个可选的 `flex`：

```json
{
  "flex": [[9, 10], [4, 5]],
  "routes": [{"from": 11, "to": 12, "path": [[0, 1], [1, 1]]}]
}
```

- `flex` 里的边 = **放弃自己的提示路线**，交给内部 A* 重路由；即使它同时出现在 `routes` 里，
  也只会被当成文档留存（`flex_rerouted` 会 +1），不会被逐跳校验。
- `routes` 里没出现的边本来就走 A*（上一轮行为不变）。

## 2. 报告里新增的字段

| 字段 | 含义 |
|---|---|
| `flex_total` / `flex_rerouted` | 这次请求释放了几条边、其中几条真的由 A* 重路由 |
| `conflicts[].edges` | 这条冲突涉及的全部边（`[[from,to], ...]`），端口双占时可能多于一条 |
| `conflicts[].suggest` | `{"edge":[from,to], "action":"flex"}` 或 `"reroute"`：调用方该把哪条边放掉 |
| `suggest_edges` | 上面这些建议边的去重清单，**直接抄进下一次的 `flex` 即可** |

`action` 的语义：

- `flex`：这条边这次是固定路线，但它落不下——把它放进 `flex` 重跑；
- `reroute`：这条边已经在 `flex` 里了，还是落不下——需要别的边让路（或多给一轮）。

## 3. 实测（`array_add`，II=4，HyCUBE_4REG，4×4，MTP=1）

测试脚本 `/home/steve/cac_scripts/final_demo.py`，日志在 `work/armB/logs/fi_*.log`。

| 用例 | 输入 | 报告 | 二进制 | 仿真 |
|---|---|---|---|---|
| A 参考路线回放 | 19 条工具链自己的路线全部固定 | ok，19/19 | `aef09f9b0062` | **Mismatches::0** |
| B 再加 6→7 固定 | 20 条固定 | **not ok**，点名 `9→10`，`action=flex` | 无 | 无 |
| C 释放建议边 | B 的 `suggest_edges` 进 `flex` | ok，19/20，`flex_rerouted=1` | `9bc8ab0a2826` | **Mismatches::0** |
| B' 再加 4→5 固定 | 20 条固定 | **not ok**，点名 `4→5`，`action=flex` | 无 | 无 |
| C' 释放 4→5 | 同上 + `flex` | ok，19/20，`flex_rerouted=1` | `aef09f9b0062` | **Mismatches::0** |

一次典型的失败文本：

```
route: the given route cannot be realised on the port graph: no legal port path
from (1,0) to (0,3) over 4 hop(s) (illegal turn, occupied port, or a wait in the
register file of more than 2 cycle(s))
```

## 4. 这一轮踩出来的限制（都要写进给下游的说明）

1. **23 条参考路线只有 19 条能注入。** 剩下 4 条：6→7 / 4→5 / 20018→20017 被 §4.3 的寄存器上限
   和端口占用挡住；20→19 能被端口图接受，但 `hycube_simulator` 在 `SELECT` 上断言
   （`!(I1isvalid && I2isvalid)`）——端口图**看不到** `SELECT` 只能有一个有效操作数这条语义约束。
   所以"参考路线逐跳回放"不是无损的，能回放的那部分必须过仿真才算数。
2. **归因会漂移。** B 里真正插不进去的是 6→7，但报告点名的是 9→10：固定边按 DFG 顺序先到先得，
   后到的边把先到边的端口挤掉，报告只能说出"哪条边落不下"。释放 `suggest_edges` 一定收敛
   （C 里确实绿了），但"谁该让路"不是唯一的——这是固定顺序落线的固有性质，不是 bug。
3. **寄存器等待上限仍然是 2**（`REG_HOP_CAP`）。把它放到 12 能接受更多路线，但仿真给出
   `Mismatches::20`：长等待链打印出的二进制和仿真语义不一致。以正确性优先，保持 2，
   长 slack 边走 `flex` 让 A* 自己找短路线。
4. 仿真器日志里没有 `Mismatches::` 行时，按 abort 处理（`Assertion ... failed`）；
   正常结束一定是 `Matches::N, Mismatches::0`。

## 5. 调用方的最小循环

```
run mapper --pins placement.json --routes routes.json --report r.json --verify
if r.ok:  done
else:     routes.flex += r.suggest_edges;  rerun   # 一轮就够，实测收敛
```

`flex` 只增不减：一轮之后 A* 拥有的边永久归 A*，再次请求时它们不算冲突。

## 6. Python 侧入口

`src/cgra_agent/mapper.py` 把上面这套东西包成对象：

```python
m = Mapper()                              # home 默认 /home/steve/cac-armB
res = m.verify(spike, DFG, MEM, TRACE, ALLOC, placement, routes)
if not res.report.ok:
    routes.flex += res.report.suggest_edges   # 报告说放谁就放谁
    res = m.verify(...)                        # 第二轮绿
sim = m.simulate(res.binary, spike, TRACE, ALLOC)   # SimResult(ok, mismatches)
```

`m.repair(...)` 直接做这件事并返回每轮的 `RepairStep`。

`tools/repair_demo.py` 是可直接跑的验收：19 条参考路线回放 → ok/0 mismatch；
加 6→7 或 4→5 固定 → 一轮释放 → 又绿。当前输出：

```
A_clean19   ok=True applied=19 flex=0 sim=Mismatches::0
B_plus_6_7  round 1 releasing [(9, 10)]   ok=True applied=19 flex=1 sim=Mismatches::0
B_plus_4_5  round 1 releasing [(4, 5)]    ok=True applied=19 flex=1 sim=Mismatches::0
3/3 cases green with Mismatches::0
```

## 7. 仿真器断言的分类（哪些能进 verify）

`grep -n "assert(" toolchain/vendor/simulator/src/*.cpp` 的活跃断言逐条归类：

| 断言 | 位置 | 静态可判？ | 处置 |
|---|---|---|---|
| `!(I1isvalid && I2isvalid)`（SELECT 双操作数） | `CGRATile.cpp:175` | **部分** | 同周期到达 → `checkOperandExclusivity()` 报 `op` 冲突；跨周期"有效位残留"不可判，见下 |
| `P/I1/I2.empty()`（输入寄存器被覆盖） | `CGRATile.cpp:352/358/364` | 是 | `checkInputExclusivity()`：同一 REGI 端口同一周期两个不同源 → `op` 冲突 |
| `default: assert(false)` 未知 opcode | `CGRATile.cpp:285` | 否，且不可达 | mapper 只把 op 放在支持它的 FU 上，二进制不会出现未知编码 |
| `assert(false)` 端口编号 | `CGRA.cpp:430` | 否，且不可达 | 内部编码路径，verify 不产生 |
| 文件打不开 | `CGRA.cpp:75/181/214/215` | 环境 | 与映射无关 |
| `assert(connectedTiles[...])` 邻接 tile 存在 | `CGRATile.cpp:579..697` | 否，由端口图保证 | 越界方向没有端口，路由走不过去 |
| 其余（`//` 注释掉的） | `CGRATile.cpp` 多处 | — | 已失效 |

### 7.1 残余的不可静态检查项

`array_add` 的 SELECT（node 0）在参考解里两个操作数**本来就不同拍到达**：
`12->0` 到 3（exec 3），`13->0` 到 7。仿真不报错是因为 tile 在 3 拍执行时
只看到 I1 有效，取了 I1 分支。

注入 `20->19` 后仿真在 `CGRATile.cpp:175` 断言，但两次映射里 PE(1,1) 的
操作数寄存器写入表**逐条相同**——也就是说断言不是"同周期到达"引起的，
而是 SELECT 实际执行的那一拍变了（取决于运行期谓词何时成立），这一拍上
两个寄存器都还持有值。**这一层 mapper 看不到**：它只有静态的到达周期，
没有执行门控信息。

结论：verify 覆盖"同周期到达"这一充分条件（可复现：把 node 12 和 node 0
都钉到 7 拍 → `op [[12,0],[13,0]] SELECT node 0 gets both operands in cycle 7`），
残余的跨周期情形交给仿真兜底，且**已知会以 abort 而不是 report 的形式出现**。
上层 agent 若需要机器可读的结论，应把"仿真 abort"映射成一条不是预期的
失败（当前 `SimResult.evidence = "simulator aborted: Assertion ..."`）。

## 8. 第二轮发现的 verify 限制：寄存器长链会 abort，不能进 verify

全程 pin（全部节点给定坐标与相位）时，`mac` / `mults1` 在 0.37s 内崩：

```
cgra_xml_mapper: .../arch/Module.cpp:606: CGRAXMLCompile::Port*
CGRAXMLCompile::Module::getInPort(std::__cxx11::string): Assertion `false' failed.
```

日志里能看到的诱因是 A* 自己的解用了 **≥3 段寄存器跳**（`[route] 17->0 (30 ports)` 这类
TREG_RO/TREG_RI 链）。`-m 0` 产出这种解没问题，但 verify 模式下重走路径时
`getInPort("TREG_...")` 找不到对应入口而断言。

结论与用法：

- 这类解 **不是** verify 能校验的输入，归入已知不可静态检查（同 §7 的 runtime 残余）；
- 上层应先判断"这份解要不要 3 段以上寄存器"，需要就把它标 flex 交给 A* 自己布，
  而不是送进 `--routes`；
- 该断言是 abort 而非 report，机器可读的出口仍是退出码 + 日志文本。

复现：`python3 tools/ablation_fixii.py --stage full`，看
`work/armB/fixii/mac/pins/r1/pins_r1.log`。

## 9. `getInPort` 断言已修：从 abort 变成冲突报告，剩下的静态盲区

§8 记的 `Module.cpp:606 getInPort` 断言不再是 abort。两处修改都在 vendored mapper 里，
随 `0002-mapper.patch` 走：

1. `Module::lookupInPort(name)`：与 `getInPort` 同样的查表，但查不到返回 `NULL` 而不是断言；
2. `lookupDestPort(src, dst)`：先按 DFG 里的 operand 槽名查（`I1`/`I2`/`P`），失败再试
   `DP0_` 前缀（数据通路的实际命名），仍失败才交给调用方报冲突。

修复前后（`mac`，全 pin，II=4）：

| | 修复前 | 修复后 |
|---|---|---|
| verify 结果 | `Assertion 'false' failed`，进程 abort（rc=134） | `ok=false`，1 条 `route` 冲突（rc=1，19 ms） |

剩下的静态盲区：`mac` 的 `(8,0)`、`mults1` 的 `(12,0)` 报
`cannot resolve the port names for this edge`。这两条边的目的节点（`idx=0`）在 DFG 里
没有可用的 operand 槽名，也就是说**静态元数据不足以判断工具链会把这个操作数绑到哪个
物理端口**。这不是"解非法"，是"静态判不了"——所以 verify 只报冲突、不去猜。

对上层的意思：`mac` / `mults1` 在 II=4 下**不可 verify**（不是因为布线质量），
要么换 II、要么只用仿真兜底，要么等 DFG 生成端把 operand 槽名补全。
`array_add` / `conv2d` / `conv3` 不受影响。

## 10. 静态模型错位清单

静态 verify 与真实硬件行为不一致的全部已知实例，集中在此。每一条都是一个**建模误差**，不是 bug 修复记录。

| # | 错位 | 现象 | 根因 | 处理 |
|---|---|---|---|---|
| 1 | R3 相位假设 | 早期把 routeInfo 的 `-T` 当作 latency 传给 pins，20 条 pin 只剩 15 条、8 个 timing 冲突 | `-T` 是**相位** `lat % II`，不是延迟；pins 只该给 `t`，延迟由 mapper 定 | `tools/ablation_fixii.py` 只传 `t`；`cac_scripts/armB/pincheck.py` 固化对照 |
| 2 | 工具链假 ok | conv2d 的 pins 配置报 `ok=true` 但仿真失败（早期一版） | 当时的 verify 不做操作数检查，静态模型比硬件宽松 | 增加 `checkOperandExclusivity` / `checkInputExclusivity` |
| 3 | `-T`/lat 混淆的第二个出口 | `lat` 列在 `-m 0` 与 `-m 3 --verify` 下不同（11 vs 13） | 前者是 A* 报的 lat，后者是 `max(node.lat + op_latency)`，两个量 | 文档统一口径，禁止跨表比较 |
| 4 | `DP0_I1` vs `I1` 前缀 | `getInPort` 断言崩溃（mac/mults1） | DFG 的 `getOPtype` 返操作数槽名 `I1`，模块端口叫 `DP0_I1` | `Module::lookupInPort` 双名尝试，找不到返回 NULL（不再断言） |
| 5 | 回放有损 | 23 条参考路线只能注入 19 条；本网格 16 格被判“端口图不可实现” | routeInfo 只给 PE 坐标序列，丢掉了端口级的复用/寄存器信息 | 报告 `routes_accepted / routes_total` + 每格 `reject_kind`，不宣称 100% 覆盖 |
| 6 | pins 静默失效 | 不给 `--pins_mode` 时 `--pins` 被完全忽略（`pins_applied=0`），报告仍 `ok=true` | mapper 默认 `pins_mode=off` | 所有跑批显式 `--pins_mode 3`；confusion.py 已固化 |
| 7 | SELECT 跨周期残留 | 两个操作数不同周期到达、但运行时 SELECT 在两者都仍有效的周期执行 → 仿真 abort，静态判不了 | 需要运行时谓词求值 | 仅在 ARM_B_REPAIR.md §7.1 记录为“不可静态检查” |

第 5、6 条属于**接口/口径**错位而非硬件建模错位，但它们同样会伪造结论，所以一并列出。
