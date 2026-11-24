# M2：agent 骨架（三层 + 四门 + 归因），以及在 3 个 kernel 上的实测

> 一句话：**流水线跑通了，策略没有赢面**。
> LangGraph 循环、LLM 在环、四道门、Auditor（注入题 10/10）、经验库都到位，
> 但在当前 3 个 kernel 上，任何 agent 策略都只能追平 PathFinder，
> 代价是 2–4 倍调用次数和 10–100 倍墙钟。这不是实现问题，是 M1.5 已经测出来的结构问题。

## 1. 三条不可越过的边界

1. **LLM 不给坐标**。M0 之前的历史数据：让模型直接吐 `(x,y,t)`，10 个 kernel 只有 2/10 全接受，
   且 9 个 kernel 上 0 次靠"看反馈重试"救回来，每轮只挪 1–2 个节点。
2. **判据只有仿真器**。mapper 自己打印 `Map Success` 不算过（旧工程实测 88% 假阳性）。
   `Attempt.ok` 硬编码为 `G1 and G2`，G1 就是 `hycube_simulator` 的 `Mismatches::0`。
3. **动作是策略级**。agent 能做的只有：换后端、给约束（pins/forbidden）、调 II、喊停。
   它不能编辑映射文件，不能改 DFG。

## 2. 文件与职责

| 文件 | 职责 | 关键点 |
|---|---|---|
| `agents/engine.py` | 一次"尝试"= 后端跑一次 + 四门判定 | `run_attempt()` 是唯一碰 mapper/仿真器的地方；后端抛异常也算一次尝试，记成 `backend_error`，不崩循环 |
| `agents/audit.py` | 失败 → `{gate, cls, suspects, advice}` | 只引用四门结论，不自己发明判据；`CGRATile.cpp:168/175` → `select_same_cycle`，`352/358/364` → `input_reg_overwrite`，`285` → `unknown_opcode`，`CGRA.cpp:214/215` → `missing_trace` |
| `agents/policy.py` | `ScriptedPolicy`（确定性阶梯，对照组+兜底）/ `LLMPolicy`（真端点） | LLM 输出必须过白名单校验；**装置失败与决策差分开记账**（`self.failures`） |
| `agents/graph.py` | LangGraph `StateGraph`：`plan → execute → plan…` | `stop_on_first_ok` 默认关；预算 `used >= budget` 硬停；`_manual_loop` 是 langgraph 缺失时的等价兜底 |
| `experience/store.py` | SQLite `cases(...)`，`record/recall/stats` | 按 `(n_nodes±4, ii)` 召回；不存 placement |
| `cli_agent.py` | `run` / `recall` | `--policy scripted|llm --no-sim --no-store --no-thinking` |
| `scripts/m2_agent.py` | 四臂对照表 | `pathfinder / sa / scripted / llm` |
| `scripts/m2_audit.py` | M2 验收 | 10 道注入题 + `--live` 两道真现场 |

### 动作空间（当前实现）

```json
{"action": "set_backend", "backend": "pathfinder|sa", "params": {...}, "ii": 4}
{"action": "constrain", "pins": {"3": [1, 2]}, "forbidden": [[7, 0, 1]]}
{"action": "stop", "why": "..."}
```

`pins` 只带 `(x,y)`，**不带相位**：相位是 mapper 从数据到达时间推出来的结果，
钉住它等于砍掉一半搜索空间（旧工程实测：全 pin 2/10 可映射，只给 (x,y) 是 7/10）。

`params` 白名单：`optimize / budget / select / pick / cost_mode / force_anneal`，
且要过 `engine._norm_params()` 归一化——否则 "pathfinder 带 SA 参数" 会被记成
一次不同的尝试，去重失效、白烧预算（踩过）。

## 3. M2 验收：Auditor 归因

`python scripts/m2_audit.py` → **注入题 10/10 正确**（验收线 ≥8/10）：

| 注入 | 期望 | 结果 |
|---|---|---|
| `CGRATile.cpp:168` / `:175` | `G1/select_same_cycle` | ✅ ✅ |
| `CGRATile.cpp:352` / `:364` | `G1/input_reg_overwrite` | ✅ ✅ |
| `CGRATile.cpp:285` | `G1/unknown_opcode` | ✅ |
| `CGRA.cpp:214` | `G1/missing_trace` | ✅ |
| 仿真失配无断言 | `G1/sim_mismatch` | ✅ |
| 后端没产出二进制 | `G1/no_binary` | ✅ |
| 槽位+算子冲突 | `G2/resource:*`，suspects=[3,7,9] | ✅ |
| mapper 拒绝放置 | `G3/mapper_rejected` | ✅ |

## 4. 实测（II=4，`work/m2_smoke.json` + `work/m2_llm.json`）

| kernel | 臂 | 合法 | 最好 cost | lat | 调用次数 | 墙钟 |
|---|---|---|---|---|---|---|
| array_add | pathfinder | ✅ | **36** | 0 | 1 | 1.6 s |
| array_add | sa(optimize,1000) | ✅ | 36 | 11 | 1 | 18.3 s |
| array_add | scripted agent | ✅ | 36 | 0 | 4 | 56.7 s |
| array_add | llm agent | ✅ | 36 | 0 | 2 | 232.0 s |
| mac | pathfinder | ✅ | **44** | 11 | 1 | 3.5 s |
| mac | sa(optimize,1000) | ❌ | (43) | 14 | 1 | 27.0 s |
| mac | scripted agent | ✅ | 44 | 11 | 4 | 88.3 s |
| trmm | pathfinder | ✅ | **18** | 0 | 1 | 1.0 s |
| trmm | sa(optimize,1000) | ✅ | 20 | 6 | 1 | 23.5 s |
| trmm | scripted agent | ✅ | 18 | 0 | 4 | 46.7 s |
| trmm | llm agent | ✅ | 18 | 0 | 2–3 | 52.1 s |

（`mac` 的 LLM 臂未跑：单次思考调用可达 3–4 分钟，3 个 kernel 全跑要半小时以上，
在结论已经清楚的情况下不划算。）

## 5. 结论（这一节的结论是负面的，但它是可引用的）

1. **基础设施全部跑通**：LangGraph 循环、真 LLM 在环、四门级联、Auditor、经验库，
   端到端 3 个 kernel 都能走完，没有一次因装置问题丢掉；
2. **agent 一次都没赢过**。三个 kernel 的最好 cost 全部等于 PathFinder 基线；
   SA 在 mac 上还从"能过"变成"不过"（cost 43 但 G1 挂）；
3. **代价是实打实的**：4 次调用 vs 1 次，56–88 s vs 1.6–3.5 s；LLM 臂 232 s 换 36 分；
4. **原因在 M1.5 已经量过**：SA 的邻域算子结构性无效（300 次移动只赚 1 点，
   最高代价是热启动的 3 倍），所以 `set_backend/params/budget/seed/select/pick/cost_mode`
   这些旋钮的上限就是"等于热启动 = PathFinder"。LLM 无论怎么选，都选不出一个不存在的解。
5. 因此 **M3 不能继续在"选后端/调参"上加东西**，必须实现两个真正改变搜索空间的动
   作：**A2 区域分解**（只让后端动子图，把大问题切成可解的小问题）与
   **A3 更聪明的硬约束**（用 failed 解的信息构造 forbidden，而不是只钉 pins）。

## 6. 复现

```bash
cd ~/acc-thu-agent
PY=/home/sutong/venvs/agent/bin/python

$PY scripts/m2_audit.py                     # 10/10
$PY scripts/m2_agent.py --arms pathfinder,sa,scripted --json work/m2_smoke.json
$PY scripts/m2_agent.py --arms llm --kernels array_add,trmm --budget 3 --json work/m2_llm.json
$PY cli_agent.py run --kernel mac --policy scripted --budget 4   # 单条看细节
$PY cli_agent.py recall --kernel mac
```

## 7. 已知限制

- **3 个 kernel 的统计功效为零**。任何"agent 比基线好 x%"都不该从这张表里读出来；
  它的用途是验证流水线与判据，不是比较算法。
- **II 恒为 4**（三个 kernel 的 RecMinII 都是 4），`II = max(recII,resII,userII)`
  把用户给的 3 抬回 4。所以"抬 II"这个动作在 array_add/trmm 上是空操作，
  只在 mac 上有效（抬到 5 能避开 SELECT 同拍断言）。
- **`cost` 只看 detour/hops/select_pairs**，不含延迟和功耗。SA 用另一套目标
  （`unmapped*100 + congestion*10 + conflict*10 + shaping`），两者错位——
  这正是"预算越多 SA 越差"的原因。
