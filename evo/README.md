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
├── check.py         资源/相位检查 + 绕路代价（适应度的核心）
├── harness.py       跑演化程序、算分（II=4 与 II=3 各评一次）
├── init.py          初始程序（基线贪心放置器，EVOLVE-BLOCK 在此）
├── evaluator.py     伐谋要求的 evaluate(path_user_py) 薄壳
├── config.example.yaml  伐谋配置模板（端点/密钥留空）
├── config.yaml      由 run.sh 从 credentials.env 生成，**不进仓库**
└── run.sh           读 credentials.env → 生成 config.yaml → 调 run_famou.py
```

`cac` 里的 `facts.py` / `validate.py` 在这条线上被 `cgra.py` / `check.py` 取代：
前者面向"塞进 prompt 的紧凑事实"，后者面向"能在毫秒内评几万个候选"的适应度。

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

### 两档 II：紧档 = RecMinII，松档 = 紧档 + 1

三个 kernel 的 **RecMinII 都是 4**（实测 `scripts/map.sh <k> 0 3`，morpher 打印
`Initial target II = 4 (ResMin II = 2 , RecMin II = 4, UserGiven II = 3 )`）。
RecMinII 的定义照抄 `src/mapper/src/mapper/HeuristicMapper.cpp:2002`
的 `getRecMinimumII()`：对所有 `child->ASAP <= node->ASAP` 的反向边取
`node->ASAP - child->ASAP + 1` 的最大值。

所以「II=3 冲刺目标」是**不存在的**（早期版本给了它 40% 权重，等于让 LLM 花四成
力气追不可达解）。现在是 `tight = max(rec_min_ii, 1)`、`loose = tight + 1`。

### 合法 = 静态约束过 **且** 真的布得出来

`check.py` 判静态部分（便宜）：

1. **硬约束**：每个节点恰好一个位置、`(x,y,t)` 不重复、越界、
   `ii >= rec_min_ii`、算子必须在其所在 PE 类型的算子表里。
   注意 ALU 与 PE_MEM **都能做算术**（`FU` 28 ops、`FU_MEM` 35 ops，后者只多
   7 个纯访存算子），差别只在延迟（ALU 1 拍、PE_MEM 2 拍）。
2. **相位与绕路**：一条边 `p→c` 的跳数 `h` 必须满足
   `h ≡ (t_c - t_p - lat_p) (mod ii)` 且 `h >= 曼哈顿距离`。
   SELECT / CMERGE 的两条父边必须落在 `t` 和 `t-1` 两拍
   （HyCUBE 的 FU 一拍只能收一个操作数，morpher 模拟器会在这上面断言）。

`router.py` 判动态部分（时间扩展图上的真布线）：每个单元 `(x,y)`、每条链路每个相位
只能过一个值，传输途中允许在 PE 寄存器里原地等拍（等拍不占链路）。
`route(prob, place, ii)` 返回 `{ok, unrouted, detour, hops_total}`。

**为什么必须接 router**：同一份 II=4 放置，静态分说基线处处更好，router 说
`array_add` 根本布不出来。

| kernel | morpher 真解 | 贪心基线 `init.py` |
|---|---|---|
| array_add | 可布 detour=38 | **不可布**（静态 cost 12） |
| mac | 可布 detour=48 | 可布 detour=45 |
| trmm | 可布 detour=20 | 可布 detour=15 |

### 分数

```
per_kernel = 0.85 / (1 + detour_at_tight / 20)   # 仅当紧档"合法且可布"
           + 0.15                                # 仅当松档也合法
combined_score = mean(per_kernel over {array_add, mac, trmm})
validity       = 紧档可布的内核数 / 3
```

`detour_at_tight` 是紧档下所有边绕路跳数之和（真实布线走出来，不是下界）。

基线 `init.py` 得 **0.399084**，`validity = 0.667`（array_add 在 II=4 布不出来，
只拿到松档的 0.15）。把 morpher 自己的解当作"程序"来评分是 **0.322701**、
`validity = 1.0` —— 它三个都布得出来但绕路更多。

> **还剩下的局限**：`router.py` 是**我们自己的**布线器，不是 morpher 的 PathFinder，
> 也不是模拟器。它能回答"这个放置在这个抽象模型下布不布得出来"，但
> **不能代替 `scripts/sim.sh` 的功能验证**（数值对不对只有仿真器知道）。
> 拿进化出的最优解跑一遍 `map.sh` + `sim.sh` 仍然必须有。

## 四、环境
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

## 七、已知限制

* **适应度是静态代理**，没有真实布线/仿真（见第三节的警告框）；
* 只看 3 个 kernel（`array_add` / `mac` / `trmm`），加 kernel 要改 `harness.KERNELS`，
  并且先跑 `scripts/run_all.sh` 生成 `out/<k>/{dfg.xml,arch.json}`；
* 评测是纯 Python、每次 ~50 ms–0.5 s，`max_workers` 可以开大；
* 演化程序在子进程里跑（`harness.run_program`），超时 120 s 直接判 0，防止死循环
  卡住整轮；
* 岛屿并行度受 LLM 端点并发限制，实测 2 worker 时每轮 150–250 s。
