# evo —— 用伐谋（Famou 2.0）演化 CGRA 映射算法

这一层把「写映射算法」变成「搜映射算法」：用百度伐谋的 LLM 驱动演化框架，
让模型反复改写 `evo/init.py` 里 `# EVOLVE-BLOCK` 之间的代码，
每次改写都拿 acc-thu 的真实 DFG 打分，分数高的留下继续变异。

和 `~/cac` 那条线的关系：`cac` 让 LLM **直接产出映射结果**（一次调用给一整张
(x,y,t) 表），`evo` 让 LLM **产出映射算法**（一个 Python 函数），后者对新的
kernel 不用再调模型。这条线属于 `evo-mapper` 分支。

## 一、目录

```
evo/
├── cgra.py          DFG XML + arch.json → 演化程序看到的 prob 字典
├── check.py         空间硬约束（完整性/边界/算子-PE 类型/bank 可达/PE 容量）
├── harness.py       跑演化程序 → 钉进真 mapper → 跑真仿真器 → 打分
├── init.py          初始程序（基线贪心放置器，EVOLVE-BLOCK 在此）
├── evaluator.py     伐谋要求的 evaluate(path_user_py) 薄壳
├── table.py         三路对照表：morpher 自己的解 vs 演化程序的解（同口径打分）
├── disagree.py      静态判定 vs 真 mapper vs 真仿真器的分歧表
├── verify_against_mapper.py  从 out/<k>/m0/ 抠出 morpher 自己的解放进同一套适应度
├── verify_end_to_end.py      单个放置的端到端验证（钉 pin → map → sim）
├── report.py         生成伐谋自带的可视化报告（得分曲线/散点/进化路径）
├── config.example.yaml  伐谋配置模板（端点/密钥留空）
├── config.yaml      由 run.sh 从 credentials.env 生成，**不进仓库**
└── run.sh           读 credentials.env → 生成 config.yaml → 调 run_famou.py
```

`cac` 里的 `facts.py` / `validate.py` 在这条线上被 `cgra.py` / `check.py` 取代：
前者面向"塞进 prompt 的紧凑事实"，后者面向"能快速筛掉明显非法解"的入场券。

三个辅助脚本回答三个不同的问题：

| 脚本 | 问题 | 输出 |
|---|---|---|
| `table.py` | 进化出来的算法比 morpher 自己的差多少？ | 逐 kernel 的 II / lat / 仿真 / 分数，两边同口径 |
| `disagree.py` | 我们的静态判定错在哪？ | `check` vs `mapper` vs `sim` 三方分歧，标出问题类型 |
| `verify_against_mapper.py` | 适应度本身可信吗？ | morpher 自己的解在适应度下的分数（天花板） |
| `report.py` | 这次演化到底怎么走的？ | 可交互的 HTML 报告（曲线 + 散点 + LLM 写的进化叙事） |

## 二、伐谋的三件套契约

| 位置 | 要求 |
|---|---|
| `-p init.py` | 可演化代码必须夹在 `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` 之间 |
| `-e evaluator.py` | 必须有 `def evaluate(path_user_py) -> dict`，返回含 `combined_score`（float，越大越好）与 `validity`（0/1）；缺一个 `EvaluateModule` 直接 `raise ValueError` |
| `-c config.yaml` | `experiment:`（任务描述、迭代数、岛屿）+ `infrastructure:`（LLM 端点、存储、日志、执行环境） |

`strategy` 只能填**注册名**，不是 rollout 名。`examples/circle_packing/config.yaml`
里写的 `standard` 在本版没注册，直接跑会 `KeyError: Strategy 'standard' not found`。
可用值：`greedy`（= EliteSelect → MutationGenerate → EvaluateModule → LLMJudge，
最接近"标准演化"）、`initial_baseline`、`adaptive_cluster`、`creative_exploration`、
`multi_agent`、`pipeline`、`plan_driven`、`random`、`top_cluster` 等。

## 三、适应度（这是全文件最要紧的一节）

被演化的接口：

```python
def place(prob, ii) -> {node_idx: (x, y, t)}
```

`prob` 是从 `out/<kernel>/{dfg.xml,arch.json}` 解析出来的只读视图：
节点表（`idx/op/parents/children/const/var/is_mem/lat`）、边表、
`pe_mem`/`pe_alu` 坐标、`xdim/ydim`、`var_bank`（变量 → bank）、算子延迟表、
以及 `tight`/`loose`/`rec_min_ii` 三个 II 参数。

### 接口：只给 (x, y)，相位交给 mapper

```python
def place(prob, ii) -> {node_idx: (x, y)}
```

**这是本设计最要紧的一条，也是踩过坑之后改的。** 相位不是映射器的输入：
`PathFinderMapper::estimateRouting` 里断言

```cpp
assert((minLatDestVal) % destPort->getMod()->getCGRA()->get_t_max() == destPort->getMod()->getPE()->T);
```

也就是"数据什么时候到达，PE 就在那一拍执行"。相位是数据到达时间的**结果**。
早期版本让演化程序自己给 `(x, y, t)`，三个 kernel 上 strict 钉相位被拒 **3/3**；
改成只钉 `(x, y)`（`t = -1` 表示由 mapper 决定）之后，同一个程序通过 **2/3**。

于是分工是：**演化程序决定空间，mapper 决定时间**。

### 打分：真 mapper + 真仿真器

```
该内核 0.00                             # 没过 check.py 的硬约束
该内核 0.15                             # mapper 布通，但仿真失配
该内核 0.15 + 0.85 / (1 + lat / 40)      # 布通且仿真零失配，lat 越小越高
combined_score = 三个内核的平均
validity       = 仿真零失配的内核数 / 3
```

`lat` 是 mapper 报告里的调度长度（最大绝对时刻），不是我们算的下界。

`check.py` 现在只做**空间**硬约束（便宜、必要、但不充分）：完整性、边界、
算子与 PE 类型、访存算子的 bank 可达性、每块 PE 最多 `ii` 个节点（它有 `ii` 个
相位槽位）。它不再是"合法"的判据，只是入场券。

mapper 单次预算 **20 秒**（`harness.MAP_TIMEOUT`）：好的放置 3–4 秒出结果，
坏的放置会一路算到 164 秒才放弃（实测 mac 的一次坏放置），超时按"没布通"算。
一次完整评测（三个 kernel，含 mac 超时）约 **30 秒**。

### 为什么删掉 router.py

`router.py` 曾是时间扩展图上的自研布线器，静态、毫秒级，用来当"能不能布"的判据。
它给出的判定与真 mapper **系统性不一致**：

| 对象 | 我们的 router | 真 mapper | 仿真 |
|---|---|---|---|
| morpher 自己的解 ×3 | 可布 | 通过 3/3 | 零失配 3/3 |
| 贪心基线（II=4） | 可布 3/3 | 通过 2/3 | 零失配 2/3 |
| 早期进化最优解 | 可布 3/3 | **通过 0/3** | 未测 |

原因是它允许"在 PE 寄存器里原地等一拍"（不占链路），这条放宽把大量实际布不出来
的放置判成可布。既然不能当判据、当预筛又会引入偏差，**就删了**（git 历史里还在）。

同理，静态代价（绕路跳数之和）也不能当分数：同一份 II=4 放置，静态分说贪心基线
处处优于 morpher 自己的解，真跑一遍却是基线在 mac 上布不出来。

这不是一个可以"再修修"的实现细节：任何不建模真实端口仲裁与配置包时序的静态判据，
都会朝"过于宽松"的方向错，因为放宽一条约束只增加可布解、减少不可布解。用它当适应度
等于给演化程序发一张空头支票。

### 校准锚点

用同一套适应度给 morpher 自己的解放打分（`evo/verify_against_mapper.py` 从
`out/<kernel>/m0/*.routeInfo.log` 抠出 `(x, y)`，相位丢弃）：

| 对象 | combined | validity | array_add | mac | trmm |
|---|---|---|---|---|---|
| **morpher 自己的解** | **0.845** | 1.000 | lat 11 | lat 10 | lat 6 |
| 贪心基线 `evo/init.py` | 0.564 | 0.667 | lat 12 | **超时** | lat 6 |

这张表是适应度可信的证据：真正的上游解拿到最高分，基线差在 mac。
也说明上限大概在 0.85 附近 —— 分数到 0.9 就意味着比 morpher 自己排得还紧。

### 一次评测里发生什么

```
place(prob, ii)                     # 演化程序的输出，纯 Python，无 IO
  → out/_evo_pins/fit_<kernel>_ii4.json     {"pins":[{node,x,y,t:-1}]}
  → scripts/map_pinned.sh <k> <pins> 1 4 4 4 _evo_fit_<k>_ii4
      cgra_xml_mapper -m 3 --pins_mode 1 --report pins_report.json
  → out/<k>/_evo_fit_<k>_ii4/pins_report.json      {ok, lat, stage, applied, ...}
  → scripts/sim.sh <k> 1 4096 _evo_fit_<k>_ii4    （仅在 mapper ok 时）
  → out/<k>/_evo_fit_<k>_ii4/sim_result.txt        Matches::N, Mismatches::M
```

`map_and_sim()` 每次评测前会**删掉旧目录**：目录名是固定的，超时留下的上一次
`pins_report.json` 会被当成本次的判决读进去（这个 bug 真发生过）。

## 四、环境

伐谋要求 Python ≥ 3.12，acc-thu 的 `cgra-tc`/`cgra` 都不满足，所以单开一个环境：

```bash
conda create -y -n famou python=3.12 --override-channels \
  -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main
conda activate famou
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  pydantic openai pyyaml tenacity jinja2 numpy langfuse==3.12.1 \
  cloudpickle httpx psutil hatchling editables famou-sdk
cd ~/famou-v2 && pip install --no-deps --no-build-isolation -e .
```

要点：

* `--override-channels` 不能省，否则 conda 报 ToS 未接受；
* 机器上 `pypi.org` 与 `pip.baidu-int.com` 都不通，只能走清华源；
* `requirements.txt` 自带 `--index-url https://pip.baidu-int.com/simple/`，别直接装；
* `editables` 要在 `pip install -e .` **之前**装好，否则 `ModuleNotFoundError`；
* `famou_sdk` 与 `langfuse` 必须装——`famou/modules/evaluate/__init__.py` 模块级
  import 了 `hybrid.py`，而后者 `import famou_sdk`；
* ray / semgrep / TA-Lib / fastapi / google-genai / scipy 不用装（代码里没有模块级
  import，ray 只在 RayBackend 内延迟 import）。

## 五、跑

```bash
# 真端点：从 ~/.config/cgra_agent/credentials.env 读 CGRA_LLM_BASE_URL / MODEL / API_KEY
bash evo/run.sh

# 重生成 config.yaml（换了端点或模型时）
bash evo/run.sh --regen

# 恢复一个实验
bash evo/run.sh -r famou_data/<experiment_id>

# 冒烟：不花钱，验证三件套能被加载、能跑完两轮
bash evo/run.sh -c evo/config.mock.yaml
```

`run.sh` 里把 `api_base` 补成 `/v1` 结尾（cac 的客户端约定：base 没有路径时补 `/v1`）。
`famou_data*/` 与 `config.yaml` 都在 `.gitignore` 里。

产物在 `famou_data/<experiment_id>/`：`programs/*.py` 是每一代程序，
`experiment.jsonl` / `experiment.log` 是逐轮指标，`llm_requests.log` 是每次请求的
token 与耗时（排查"空响应"就看这里：`response_tokens` 撞到 `max_tokens` 就是推理
通道吃光了预算）。

## 六、给 `~/famou-v2` 打的两个补丁

都在 `scripts/patch_famou.py`（幂等，可重复执行）：

1. `famou/controller/evolver.py`：rollout 失败时无条件调用
   `self.current_strategy.on_rollout_failed(result)`，但内置的 `StaticStrategy`
   （`greedy` 等静态策略都用它）没有这个方法 → 一次普通的生成失败就把整个实验打挂
   （`AttributeError: 'StaticStrategy' object has no attribute 'on_rollout_failed'`）。
   改成 `getattr(..., None)` + `callable()` 判空。
2. `famou/infrastructure/llm/openai_client.py`：推理模型可能把整个 `max_tokens`
   写进 `reasoning_content`，正文为空、`finish_reason=length`，上层看到空 code 就报
   `No code extracted from LLM response`，白等三分钟。加一次性兜底：正文为空且
   `finish_reason=length` 时，带 `extra_body={"thinking": {"type": "disabled"}}`
   再请求一次。

## 七、结果（II=4，3 个 kernel，8 轮 × 2 岛屿）

同一套适应度下的对比（`python evo/table.py`）：

| 对象 | combined | array_add | mac | trmm |
|---|---|---|---|---|
| **进化最优** `reference/best_20261123_1152.py` | **0.850** | lat 9 | lat 11 | lat 6 |
| morpher 自己的解 | 0.845 | lat 11 | lat 10 | lat 6 |
| 贪心基线 `evo/init.py` | 0.564 | lat 12 | **超时，布不出来** | lat 6 |

三个引擎全部布通且仿真零失配（`validity = 1.000`）。
进化出来的在 array_add 上比 morpher 少两拍（9 vs 11），
在 mac 上多一拍（11 vs 10），合起来略胜。

**这不是“比 morpher 好”的结论。** 一个由 LLM 写出来的局部搜索能达到
手写启发式的水平，但 0.005 的差距在 3 个 kernel 上不具备统计意义；
而且这里的“对手”只是 PathFinder 在 II=4 下的一次结果，不是它的最优。

### 进化出来的是算法，不是表

最优程序 419 行，把基线的单趟贪心换成**确定性多起点局部搜索**：
代价函数精确复算绕路 + 拥塞项，move + swap 邻域，硬上限每块 PE `ii` 个节点，
另加一条**软上限每块 PE 2 个节点**。后者是关键：基线把 PE 堆满后，
mapper 的相位搜索会爆炸——mac 就是这么超时的（实测单次 163 秒）。

模型改了一次代码，三个 kernel 都从"超时 / 布不出" 变成"布通 + 零失配"。

### 一个反例：适应度错了，演化就在优化错的目标

前一代最优解（`1_0_4_202611231115.py`，已删）在旧模型下得 0.837；
模型修正后（bank 行号从 `CGRA.CONNECTIONS` 读、`MEMORY_OPS` 补上 OLOAD/OSTORE）
它三个 kernel 全非法、归零。

而且过程完全看不出异常：分数一路从 0.56 涨到 0.84，每一步都“有改进”。
这是把真 mapper 接进适应度的最大理由：只有真工具能发现"模型说合法、硬件说不行"。

## 八、可视化报告

伐谋自带报告生成器（`~/famou-v2/api_server/report.py`），把一个实验目录变成
一张可交互的 HTML 面板：得分曲线、每个程序的散点、最优路径连线，
加上一段由 LLM 写的“这次搜索到底怎么走的”叙事（`evolution_map`）。

```bash
python evo/report.py                       # 最新一个实验
python evo/report.py --exp famou_data/<exp>
python evo/report.py --out out/report      # 默认就是这个
```

产出 `out/report/data.json`（聚合数据）与 `out/report/report.html`（自包含，无需服务器）。
报告里的叙事是真调用 LLM 生成的，所以跑一次要一分钟左右。

报告需要三样东西：实验目录（`programs/*.json` + `experiment_checkpoint_*.json`）、
一个 LLM 端点（叙事是生成的，不是模板）、一个输出目录。
端点读的是同一份 `~/.config/cgra_agent/credentials.env`，所以不用另外填。

## 九、已知限制

* **每次评测真跑 mapper 与仿真器**，一次约 30 秒（mac 在坏放置上会撞 20 秒超时）。相比纯静态的
  50 ms，这是为了让分数有意义而付的代价；也因此 `max_iterations` 不要开大。
* 只看 3 个 kernel（`array_add` / `mac` / `trmm`），加 kernel 要改 `harness.KERNELS`，
  并且先跑 `scripts/run_all.sh` 生成 `out/<k>/{dfg.xml,arch.json}`；
* 演化程序在子进程里跑（`harness.run_program`），超时 120 s 直接判 0，防止死循环
  卡住整轮；
* 岛屿并行度受 LLM 端点并发限制，实测 2 worker 时每轮 3–6 分钟；
* **适应度与 mapper 强相关**：同一份放置在上游 PathFinder 与本工程上的结果可能不同
  （`~/cac` 的 mapper 是有行为差异的 fork，`allowed_time_steps_for_connection`
  被改过）。分数只能在**同一份 mapper 二进制**下横向比较。
* `router.py` 已删除（判定过于宽松，见第三节）；`disagree.py` 保留了三方分歧的对照口径。
* `map_pinned.sh` 依赖 `-m 3`（PinnedMapper），而该能力目前靠 `scripts/patch_pinned_mapper.py`
  手动打进上游源码，还没落成 `toolchain/patches/0002-*.patch`。
