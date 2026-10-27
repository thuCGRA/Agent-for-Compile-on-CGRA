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
