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
`pe_mem`/`pe_alu` 坐标、`xdim/ydim`、`var_bank`（变量 → bank）、算子延迟表。

`check.py` 判两件事：

1. **硬约束**（违反即 0 分）：每个节点恰好一个位置、`(x,y,t)` 不重复、
   越界、访存算子必须落在 `pe_mem` 而算术算子必须落在 `pe_alu`。
2. **相位与绕路**：一条边 `p→c`，父节点第 `t_p` 拍出结果，经 `lat_p` 拍延迟和
   `h` 跳传输，必须在子节点执行的那一拍到达；`h` 还要满足
   `h ≡ (到达相位 - t_p - lat_p) (mod II)` 且 `h >= 曼哈顿距离`。
   多出来的 `h - 曼哈顿距离` 就是**绕路**，越少越好。
   SELECT / CMERGE 有两个操作数，两条父边必须落在 `t` 和 `t-1` 两拍
   （否则功能单元拿不到两个操作数——这是 HyCUBE 的真实限制，
   morpher 的模拟器会在这上面断言）。

分数：

```
score_kernel = 0.6 / (1 + cost_ii4 / 20) + 0.4 / (1 + cost_ii3 / 20)   # 只有合法才计分
combined_score = mean(score_kernel)   over {array_add, mac, trmm}
cost = 绕路总跳数 + 3 × 端口超额（同一 PE 同一拍进入超过 4 条边）
```

基线 `init.py`（拓扑序 + 深度定列 + bank 定行 + 相位贪心）得 **0.6012**，三个 kernel
在 II=4 和 II=3 上都合法。所以 0.60 是"起点"，1.0 是上界。

> **必须知道的局限**：`cost` 是**绕路的静态下界**，不是真实布线结果。
> `check.py` 说合法，不等于 morpher 的 PathFinder 一定布得出来。
> 在 `~/cac` 的实测里，纯静态判据与真实仿真的分歧率很高（mapper 说 OK 但仿真
> 失配的格子占 88%）。所以这里的分数只能用来**排序候选**，
> 真结论必须回到 `scripts/map.sh` + `scripts/sim.sh` 验证。
> 把 `check.py` 换成真实路由器（时间扩展网络 + rip-up & retry）是下一版的事。

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
