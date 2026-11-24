# 00 · 里程碑与当前状态

本分支（`agent-compiler`）只做一件事：**用 agent 调度一个确定性映射后端，把 mapping 做得更快更稳**。
进化算法那条线在 `evo-mapper` 分支，两边共用 `core/` 契约层。

---

## 总原则（决定后面所有取舍）

1. **LLM 不给坐标。** 上一个工程（`~/cac`）的结论：10 个 kernel 里只有 2/10 能靠"LLM 直接出 (x,y)"全接受，
   9 个 kernel 上 0 次靠反思救回，每轮只挪 1–2 个节点。所以坐标级决策不是 agent 该干的事。
2. **agent 的动作是"策略级"的**：选后端、给约束、划区域、定 II、决定何时停、读诊断。
3. **判据只有仿真器。** G1（`hycube_simulator` 零失配）是唯一真值；G2/G3/G4 都是前置过滤，不能替代 G1。

---

## 四道门（Gates）

| 门 | 名字 | 判据 | 代价 | 能否替代 G1 |
|---|---|---|---|---|
| G1 | 功能等价 | 仿真 `Mismatches::0` 且 `Matches>0` | 1–3 s | 就是真值 |
| G2 | 资源合法 | `core.check` 记账：槽位/端口/链路/op 支持/内存算子位置 | <1 ms | 否，会误杀 |
| G3 | 时序可行 | mapper 自报的 unmapped/conflict/overuse + 覆盖节点数 | 0 | 否 |
| G4 | 质量 | `cost = detour + hops + select_pairs` | 0 | 否，只用于排序 |

**为什么 G2 不能替代 G1**：`~/cac` 那次实测，`mapper_ok` 与仿真一致率只有 5/50，拿 mapper 自报的成功当成功率会报 88% 假阳性。

---

## M0 · 地基（已完成）

目标：把 `acc-thu` 的三件套接成可编程后端，并让 SA 真的能退火。

- [x] `core/`（从 `acc-thu/evo/` 提升）：`build_problem` / `check` / `score_placement` / `kernel_score`
- [x] `tools/mapper.py`：`run_mapper(kernel, backend, ii, ...) -> MapRun`，每次清空 workdir
- [x] `tools/sim.py`：`run_sim(...) -> SimRun`
- [x] `tools/probe.py`：把一次尝试压成给 agent 看的摘要
- [x] `backends/`：`pathfinder` / `sa`（统一 `Backend.run(prob, ii, hints)`）
- [x] `gates/`：G1–G4 四个独立函数 + `Attempt` 聚合
- [x] `scripts/m0_baseline.py`：baseline 表
- [x] C++ 补丁 `0002`：SA 落盘放置 + `--sa_force_anneal` / `--sa_optimize` / `--sa_budget`

### M0 的四个硬结论

**① 上游 SA 是假退火，有三处早退，不是一个。**

`SAMap()` 先调 `initMap()`（贪心 PathFinder 放置），一旦可行就 `return true`，温度循环一次都不跑。
这就是横评里 "SA 与 PathFinder 的 II/lat 逐项相同" 的原因。
打掉第一处后还有第二处（`inner_map()` 里可行即停）、第三处（温度循环末尾 `if (isCurrMappingValid()) break;`）。
三处全关（`--sa_optimize`）之后才真的跑满 149 个温度台阶（200 → 10）。

**② 真的退火了，但几乎没收益。**

array_add：14900 次移动、245 s，cost 只从 167 降到 165（2 次改进：move 0 的 167、move 14 的 165）。
3 个 kernel 全开 optimize 要 15 分钟。这说明**上游 SA 的邻域算子（随机挑节点 + 只取候选表第一个）质量太差**，
这才是 agent 有活干的地方——不是"多给点算力"，而是"换个邻域"。

**③ II 在这个 benchmark 上不可优化。**

`II = max(recII, resII, userII)`，3 个 kernel 的 RecMin 都是 4，所以 `-i 3` 会被抬回 4。
`core/score.py` 里 0.4 权重的 II=3 项目前恒为 0，**质量指标必须重做**（改用 cost / lat / cycles）。

**④ `mac` 的 SA 失败是上游缺陷，不是我们引入的。**

`hycube_simulator: CGRATile.cpp:168: Assertion '!(I1isvalid && I2isvalid)' failed.` —— SELECT 同拍双操作数。
PathFinder 的同一个 kernel 是过的，两者 bin 不同（`0a9d3917…` vs `4c44bec7…`）。

### M0 baseline 表（II=4）

| kernel | backend | G1 | G2 | G3 | lat | cost | 耗时 |
|---|---|---|---|---|---|---|---|
| array_add | pathfinder | PASS | PASS | PASS | 0 | 36.0 | 1.7 s |
| array_add | sa（早退） | PASS | PASS | PASS | 11 | 36.0 | 1.7 s |
| array_add | sa（optimize） | PASS | PASS | PASS | 11 | 35.0 | 245.2 s |
| mac | pathfinder | PASS | PASS | PASS | 11 | 44.0 | 3.9 s |
| mac | sa（optimize） | **fail** | PASS | PASS | 13 | 30.0 | 346.8 s |
| trmm | pathfinder | PASS | PASS | PASS | 0 | 18.0 | 1.1 s |
| trmm | sa（早退） | PASS | PASS | PASS | 6 | 18.0 | 1.2 s |
| trmm | sa（optimize） | PASS | PASS | PASS | 6 | 26.0 | 333.1 s |

注意 mac 与 trmm：**cost 降低但 lat 变差**（mac 11→13、trmm cost 18→26）。SA 的 `getCost()` 里根本没有延迟项，
所以"cost 更低"不等于"更好"。→ 质量指标要同时看 cost 和 lat，不能只看 cost。

### ⑤ 给 SA 更多预算，我们的指标反而变差（预算不单调）

| kernel | budget=200 | budget=1000 | budget=4000 | budget=0（跑满） |
|---|---|---|---|---|
| array_add | cost 36.0 / 6.5 s | cost **41.0** / 26.0 s | cost 36.0 / 73.6 s | cost 35.0 / 245.2 s |
| trmm | cost 18.0 / 7.7 s | cost **22.0** / 30.9 s | cost 16.0 / 97.6 s | cost 26.0 / 333.1 s |

（全部 G1 PASS、II=4、lat 不变：array_add 11、trmm 6）

这**不是 bug，是目标函数错位**：
- mapper 的 `getCost()` = `unmapped*100 + congestion*10 + conflict*10 + port_usage`（只看"能不能映射"）
- 我们的 cost = `core.check` 的 `detour + hops + select_pairs`（看"映射得好不好"）

两个目标不同，所以"mapper 认为更优的快照"在我们这里可能更差，而且**预算越多越能暴露这个错位**。
trmm 在跑满时 cost 26 比 budget=200 的 18 更差，说明它在 149 个温度台阶里持续朝 mapper 自己的目标走。

→ **这直接给出 agent 的第一个有意义的动作**：不是"给 SA 加预算"，而是"把我们的目标喂进 SA 的接受准则"（M1 的钩子 5：改 `getCost` 或加 `whetherAcceptNewMapping` 的外部评分）。
→ 也说明**"加算力"这条最容易想到的路已经被证伪**，agent 必须换邻域/换目标，而不是调预算。

---

## M1 · 约束接口（已完成，提交 `8eba382`）

目标：让 agent 能"告诉后端该怎么做"，而不是自己给坐标。

- [x] `--constraints <json>`：`{pin_mode, pins: {idx: [x,y]}, forbidden: {idx: [[x,y],...]}}`（patch `0003`）
- [x] PathFinder 候选集裁剪：`estimateRouting` 里候选生成后、`getLatCandDests` 之前过滤
- [x] SA 侧同样过滤：`getRandomDPCandidate()` 两处（退火邻域原本完全不受约束，是一半生效的坑）
- [x] `--sa_seed`（patch `0004`）：上游四个随机点全是真随机源，不钉种子任何对比都不成立
- [x] SA 空候选表保护（patch `0005`）：约束把候选表过滤空后 `.front()` 是 SIGSEGV，
      之前一直被误读成"映射失败"
- [x] 验收：`array_add/mac/trmm × pathfinder/sa` 六格全 PASS（T1 pin 命中 / T1b 单点位移 /
      T3 forbidden / T4 矛盾拒绝 / T5 双 pin 同 PE 拒绝）

**M1.5 / M1.5b（见 `docs/02-m15-neighbourhood.md`）**：给 SA 加了 `--sa_select` / `--sa_pick` /
`--sa_cost_mode` 三个开关并全部实测，结论是**都不值得改**——base 在热启动可行时完全确定，
启发式的收益落在种子噪声内，换代价函数没有任何 kernel 变好。退火轨迹显示
300–367 次移动里只有 0–4 次让代价低于热启动，最高代价是热启动的 3.2 倍：
**上游 SA 的瓶颈是邻域算子结构性无效，不是调参**。

## M2 · 诊断 + agent 骨架（已完成，提交 `23ccdb8`，详见 `docs/03-m2-agent.md`）

- [x] `agents/audit.py`：读 `Attempt` 的失败证据 → `{gate, cls, suspects, advice}`
- [x] 验收：10 个人工注入的错误，根因定位 **10/10**（验收线 ≥8/10）
- [x] `agents/engine.py` / `agents/policy.py` / `agents/graph.py`（LangGraph `StateGraph`）
- [x] `experience/store.py`（SQLite `cases` 表）
- [x] `scripts/m2_agent.py` 四臂对照 + `scripts/m2_audit.py`
- **M2 的负结果**：pathfinder/sa/scripted/llm 四臂在 3 个 kernel 上，
  agent 的最好 cost 全部等于 PathFinder 基线，代价是 2–4 倍调用与 10–100 倍墙钟。

## M3 · 预算曲线与真正的动作（下一步）

M2 证明了"选后端/调参"没有赢面，所以 M3 的重点不是加旋钮，而是**加改变搜索空间的动作**：

- [x] `agents/graph.py`（LangGraph `StateGraph`）已就位，可直接复用
- [ ] **A2 `set_region`**：只让后端动子图（后端加 `--region` 参数，mapper 侧约束候选集）
- [ ] **A3 更聪明的 `forbidden`**：用失败解的信息构造禁止集，而不是只钉 pins
- [ ] 主图：横轴 mapper 调用次数 / 墙钟，纵轴合法率 / cost / lat
- [ ] 对照：PathFinder、修好的 SA、random-restart、固定脚本、agent

## M4 · 多步放置 + 经验库

- [x] `experience/store.py`（SQLite）已在 M2 落地，字段为 `kernel/n_nodes/n_edges/ii/backend/params/ok/cost/lat/gate/cls/note`
- [ ] 经验检索接入策略（`recall` → prompt 里的"相似实例历史"）
- [ ] 动作 A5（`propose_local`，仅 ≤8 节点）
- [ ] 消融：去经验检索 / 去 Auditor / 关 A5 / 单步 vs 多步

---

## 已知风险

| 风险 | 现状 | 应对 |
|---|---|---|
| 只有 3 个 kernel，统计功效不够 | `acc-thu/kernels/` 只有 array_add/mac/trmm | M3 前扩到 10（`~/cac/work/kernels/` 有全套） |
| II 恒为 4，无优化空间 | 已确证 | 质量指标改 cost/lat/cycles |
| 真 ADL 缺 PE→bank 连线 | `update_mem_alloc.py` 只写 `DATA_LAYOUT` | 用 `var_bank` 约定顶上，写进文档 |
| `build/` 不能跨目录复用 | CMakeCache 记绝对路径 | 换目录/切分支先 `rm -rf build` |
