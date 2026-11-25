# 08 · `--routes` 走廊与 CEGIS 回灌：做了什么，以及为什么没赢

> 一句话结论：两个交付物都实现并跑通了，但**都没有改善结果**。原因是同一个——
> `mapper` 拒绝外部放置的地方**早于**它们介入的位置。这一节把这个结论连同证据记下来，
> 免得以后再花一轮去试同样的方向。

## 1. 两个交付物

### 1.1 `--routes` 走廊引导

| 层 | 文件 | 作用 |
| --- | --- | --- |
| C++ | `src/mapper/include/morpher/util/util.h` | `--routes <file>` 参数 |
| C++ | `src/mapper/include/morpher/mapper/HeuristicMapper.h` | `struct RouteGuide { std::set<std::pair<int,int>> pes; int maxHops; }` + `guides` |
| C++ | `src/mapper/src/mapper/HeuristicMapper.cpp` | `loadRoutes()`：每行 `src dst max_hops n_pes x0 y0 ...` |
| C++ | `src/mapper/src/mapper/PathFinderMapper.cpp` | `LeastCostPathAstar` 里按 `(生产者, 消费者)` 过滤跨 PE 的下一跳 |
| C++ | `src/mapper/src/CGRA_xml_compiler.cpp` | 启动时 `loadRoutes()` |
| py | `tools/ilp/routes_to_guide.py` | `routes.json` → 走廊文本 |
| py | `tools/pins/add_routes_hook.py` | 幂等打上述 C++ 补丁 |

**为什么给走廊而不是逐端口路径**：`mapper` 要在真实端口图上自己再落一次位
（`NORTH_XBARI`、四个输入寄存器、`newNodeDPOffCP`/`endPortCP` 冲突端口集），
这才是结果能成为合法 HyCUBE 映射的原因。给逐端口路径等于绕过它的端口记账。

**为什么走廊需要 `--slack`（默认 1）**：`ilp_router.py` 在 `(x, y, t)` 粒度建模，
把端口争用折叠掉了。粗模型里"直穿一个 PE"的值，在细模型里可能必须绕到邻居
再回来才能拿到空闲输入端口。`slack=0` 会误杀合法路由；`slack>=4` 退化成整阵列。

### 1.2 CEGIS 回灌循环

`tools/ilp/cegis.py`：放置解 → `ilp_router` 判可布 → 不可行则加 nogood 重解，
最多 `--rounds`（默认 8）轮。

nogood 是**整个放置点**的子句（禁止全体节点的这一组具体槽位）。任何"只禁部分
节点赋值"的放宽都不 sound：布线不可行是联合约束，单独禁掉某几个节点的槽位
不能排除同一批冲突的其他组合。

### 1.3 顺带修的可复现性问题

`build_and_solve` 原本硬编码 `solver.parameters.num_search_workers = 8`。8 个 worker
下 CP-SAT 每次返回不同的（同样好的）放置，于是同一条命令两次跑出
`pins_only=OK` 和 `pins_only=FAIL`，对照表根本无法解释。改为读
`CP_SAT_THREADS`（默认 8）/ `CP_SAT_SEED`（默认 0）。

## 2. 实测：两个交付物都没有赢

`scripts/exp_routes.sh` 的口径是**固定同一个放置**（`pins` 文件不变），只切换
`--routes` 的有无与 slack。这样差异只能来自走廊。

| kernel | 节点 | II | CEGIS | pins only | slack 0 | slack 1 | slack 2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| array_add | 20 | 4 | OK（2 轮） | FAIL | FAIL | FAIL | FAIL |
| trmm | 11 | 4 | OK（2 轮） | FAIL | FAIL | FAIL | FAIL |

四列完全相同，且 `pins_only` 自己就 FAIL。日志给出直接原因：

```
PIN: node 1 (ADD) pin (1,0,2) not among 11 candidate(s); fail
route estimation failed...
Map Failed!.
```

## 3. 为什么：拒绝发生在 `estimateRouting` 里面

打开 `ROUTE=1` 看候选漏斗（`scripts/diag_candidates.sh`）：

```
Candidate Dests = 8        # 第一轮只估到 8 个（父/子都没落位）
Candidate Dests = 63       # 第二轮起：4x4 x II=4 的全部 (x,y,t)
Candidate Dest =PE_X1|_Y0|-T2.FU0.DP0     # ← pin 要的 (1,0,2) 确实在候选里
par Estimating Path ... ,6,,parent_node = 12
```

`(1,0,2)` 是 63 个候选之一，`estimateRouting` 也为它跑过 A* 估计，但最终只有
**11 个**候选进了 `estimatedRoutes`，它不在里面。计数：`par Estimating Path` 8914 次、
`par Estimate Path Failed` 86 次 —— 每个候选都要对**所有父边**和**所有已落位子边**
逐一估通，任何一条估不通就整条候选被丢。

`applyPins` 是在这之后才跑的，它看到的"11 个候选"已经是被 `estimateRouting`
淘汰过的残集。所以：

- `--routes` 的过滤点在 `LeastCostPathAstar` 里，**确实**会被 `estimateRouting` 调用到，
  但走廊只能让候选**更少**，不能让被淘汰的候选复活；
- 更根本的是，`estimateRouting` 是**逐节点贪心**的：它按拓扑序一个节点一个节点地
  定放置 + 定路由，前面节点的选择不可回溯。`ilp_router` 能证明"这组 `(x,y,t)` 存在
  完整布线"，是因为它把**所有边**的寄存器数当变量一起解；`estimateRouting` 没有
  这个自由度。

这也解释了之前表 E 的现象：CP-SAT 放置的 9 个 kernel 里，全局布线 9/9 OPTIMAL，
而 mapper 的 A* 只吃下 4/9 —— 差的 5 个不是"放置不可布"，是"贪心逐节点估不出"。

## 4. 顺带发现：`ALLPHASE` 只解决了一小半

`estimateRouting` 末尾有一段：

```cpp
if (minLatDests[top.dest] == minLatSucc || !changed)
    estimatedRoutes.push(top);
```

它只保留**最早到达相位**的候选，later-phase 目的全部丢掉。已改成
`pinsMode != 0` 时保留全部候选（`pinsMode == 0` 老路径不变，基线回归
`array_add 243/0`、`mac 167/0`、`trmm 5/0` 逐项一致）。

但这一改**并没有**让 pin 通过：候选数从 11 涨到 11（II=4 时仍是 11），因为真正
的淘汰发生在更早的逐边 A* 估计里，不是这段相位过滤。这段修改保留的价值是
"pins 模式不再额外丢候选"，不是"修好了 strict pin"。

## 5. 要真正用上外部放置，还差什么

按代价从低到高：

1. **`--routes-only` / 路由注入模式**（推荐先做）。给 mapper 一份
   `(node → (x,y,t))` + `(edge → 端口路径)`，跳过 `estimateRouting` 与 `Route()`，
   直接 `assignPath` + 发 bitstream。旧工程 `~/cac` 的 B 臂 `--verify` 模式就是
   这个形状，且已验证"回放参考路线得到的 bin 与 PathFinder 自己产出的逐字节相同"。
   这条路径能把"放置是否可布"的判定权完全交给 `ilp_router`，mapper 只负责发射。
2. **让 `estimateRouting` 支持回溯**（或按 `ilp_router` 给出的每边寄存器数作为
   初始 `minLatDestVal` 提示），减少贪心一次定死的影响。
3. **把布线约束并进放置模型**（CGRA-ME `ILPMapper` 的做法）。4×4 上变量约
   5900 布尔，可行但慢；这是"ILP 可行 ⟹ 存在真实布线"的唯一途径。

## 6. 复现

```bash
cd ~/acc-thu-ilp
source scripts/lib.sh && activate_tc && set +eu
export CP_SAT_THREADS=1            # 可复现
bash scripts/inject_mem.sh array_add 2 2048 4x4
.venv/bin/python tools/ilp/cegis.py --dfg out/array_add/dfg.xml \
    --arch out/array_add/arch.json --ii 4 --rounds 8 \
    --pins-out /tmp/p.pins --routes-out /tmp/p.routes.json
.venv/bin/python tools/ilp/routes_to_guide.py /tmp/p.routes.json --slack 1 \
    --xdim 4 --ydim 4 -o /tmp/p.guide
# 注意：必须在干净目录里 cp 真实 dfg/arch，用软链会 SIGSEGV
mkdir -p /tmp/m && cp out/array_add/dfg.xml out/array_add/arch.json /tmp/m/ && cd /tmp/m
$OUT/cgra_xml_mapper -d dfg.xml -j arch.json -x 4 -y 4 -i 4 -t HyCUBE_4REG -m 0 \
    --pins /tmp/p.pins --pins_mode 1 --routes /tmp/p.guide
```

诊断候选漏斗：

```bash
ROUTE=1 $OUT/cgra_xml_mapper -d dfg.xml -j arch.json -x 4 -y 4 -i 4 \
    -t HyCUBE_4REG -m 0 --pins /tmp/p.pins --pins_mode 1 > route.log 2>&1
grep -c "Candidate Dest =" route.log        # 9364
grep -c "par Estimate Path Failed" route.log # 86
grep -m3 "not among" route.log               # PIN: node 1 ... not among 11 candidate(s)
```

## 7. 已知限制

1. 走廊的 `maxHops = n_hops + slack` 对大图过紧/过松都难调；`slack=4` 时 A* 会
   退化成全阵列搜索并超时（已实测 180 s 未返回）。
2. CEGIS 的 nogood 只禁"这一个完整放置点"，收敛可能很慢；大图上未测。
3. `--routes` 目前只在 `LeastCostPathAstar` 里过滤，**没有**影响
   `estimateRouting` 的候选淘汰逻辑，这是它救不了 strict pin 的直接原因。
