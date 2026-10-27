# 臂 A：LLM 布局 + PathFinder 布线（M1+）

状态：**可运行**。`array_add` 端到端 II=4、Mismatches::0；坏 pin 被本地校验拦下并带错误重问；
同 pins 两次运行哈希一致。10 个 kernel 的 map 级评测 + pin 密度消融（k=0/25%/50%/100% ×
degree/critical/bank）已完成，`-m 0` 基线同表对照。II=3 实验（§2.4）给出明确负结果：
**II 由 mapper 的 RecMin 决定，pin 无法把 II 拉到 3**。Router 契约已定稿为
`route(placement, facts, arch, ii, budget) -> MapReport`（见 §3）。

## 1. 怎么跑

环境已就绪（cgra / cgra-tc 两个 conda 环境、预编译 mapper 与 simulator）。src 布局未装包，
用包装脚本或显式 `PYTHONPATH`：

```bash
cd /home/steve/cac-armA
scripts/run.sh run --arm A --kernel array_add          # 或 PYTHONPATH=src python -m cgra_agent.cli run ...
```

接真模型只换三个环境变量，代码不动：

```bash
export CGRA_LLM_BASE_URL=https://api.example.com/v1
export CGRA_LLM_API_KEY=sk-...
export CGRA_LLM_MODEL=deepseek-chat
PYTHONPATH=src python -m cgra_agent.cli check          # 探活
```

其它入口：

```bash
# 不做布局，回放一份 pins（调试/对照）
scripts/run.sh run --arm A --kernel array_add --pins-file /home/steve/llm-lg-demo/pins_m0.json

# 只跑 LLVM 段，打印 facts
PYTHONPATH=src python -m cgra_agent.cli facts --kernel array_add --work work/facts

# pin 密度消融（本文件 §2.2；贪心参考布局，不需要 trace）
PYTHONPATH=src python scripts/eval_density.py

# II=3 实验（本文件 §2.4）
PYTHONPATH=src python scripts/eval_ii.py

# 单点：只钉 50% 的关键路径节点
scripts/run.sh run --kernel array_add --placer greedy --pin-ratio 0.5 --pin-rule critical

# 单元测试（不需要工具链）
python -m pytest -q
```

槽位：`--placer {llm,greedy,fixed}`、`--router pathfinder`；B 臂接自己的实现见
`docs/ARM_A_MODULES.md`。退出码 0 = 映射且仿真通过（或映射成功但没有 trace），2 = 同一含义但
`unverified`，1 = 失败。有 trace 时 S6 仿真，无 trace 记 `status=ok, sim="skipped(no trace)"`。

每次运行落在 `work/armA/<kernel>/<时间戳>/`：`config.yaml`、`facts.json`、`pins.json`、
`report.json`、`sim.txt`、`bitstream.bin`、`llm_calls.jsonl`、`run.json`、`run.log`。

## 2. 结果

### 2.1 端到端（array_add，4x4）

| 路径 | II | lat | Matches | Mismatches | cycles | LLM 调用 |
|---|---|---|---|---|---|---|
| smoke 基线（method 0，无 pin） | 4 | 11 | 243 | 0 | 134 | - |
| A 臂回放已知 pins（strict） | 4 | 11 | 243 | 0 | 133 | 0 |
| A 臂 + mock（先坏 pin 后好 pin） | 4 | 11 | 243 | 0 | 133 | 2 |

* 坏 pin 恢复：第 1 次回答把 node 0 放到 `x=9`，`validate` 报
  `node 0 at (9,2): outside the 4x4 grid`，错误原文回给模型；第 2 次通过。
* 可复现：同一份 pins 连跑两次，`binary_sha256=ab64645e…`、pins/report 的 sha256 均相同。
* mapper 1.0–1.5s，DFG 0.1–0.5s，整条链约 5s。`python -m pytest -q`：24 passed。

### 2.2 pin 密度消融（核心实验）

布局来源固定为贪心参考布局（与 LLM 无关），比较的是 **pin 密度** 本身：k ∈ {0, 25%, 50%, 100%}，
三种写死的选点规则（`src/cgra_agent/placers/pin_select.py`）：`degree`（出入度）、
`critical`（延迟加权关键路径，slack=0）、`bank`（跨 bank 的 LOAD/STORE 及一跳邻居）。
k=0 即 `-m 0` 基线；k=100% 先 strict(1)、失败再 repair(3)；中间档一律 repair(3)。
完整 146 行数据：`work/eval-density/density.csv`（`best.json` 是每格最优）。

每格为「II/lat」，base = `-m 0`：

| kernel | base | degree 25/50/100 | critical 25/50/100 | bank 25/50/100 |
|---|---|---|---|---|
| array_add | 4/11 | 4/13 · 4/9 · 4/11 | 4/11 · 4/11 · 4/11 | 4/11 · 4/11 · 4/11 |
| array_cond | 4/13 | 4/16 · 4/19 · 4/19 | 4/15 · 4/13 · 4/19 | 4/16 · 4/19 · 4/19 |
| conv2d | 4/15 | 4/17 · 4/16 · 4/23 | 4/15 · 4/18 · 4/23 | 4/18 · 4/21 · 4/23 |
| conv3 | 4/13 | 4/12 · 4/12 · 4/16 | 4/13 · 4/13 · 4/16 | 4/16 · 4/16 · 4/16 |
| gemm | 4/13 | 5/17 · 5/17 · 5/17 | 5/13 · 5/13 · 5/17 | 5/17 · 5/17 · 5/17 |
| gemm_pace | 4/13 | 4/14 · 4/27 · 4/27 | 4/13 · 4/18 · 4/27 | 4/36 · 4/27 · 4/27 |
| hpcg | 4/11 | 4/14 · 4/14 · 4/14 | 4/11 · 4/12 · 4/14 | 4/14 · 5/11 · 4/14 |
| mac | 4/10 | 4/10 · 4/10 · 4/12 | 5/10 · 4/10 · 4/12 | 4/10 · 4/11 · 4/12 |
| mults1 | 4/16 | 4/20 · 4/19 · 4/23 | 4/16 · 4/16 · 4/23 | 4/21 · 4/21 · 4/23 |
| trmm | 4/6 | 4/6 · 4/6 · 4/6 | 4/6 · 4/6 · 4/6 | 4/6 · 4/6 · 4/6 |

结论：

* **全量 pin 是负收益**。100% 档在 30 个 (kernel, rule) 组合里只有 6 个 strict 能过
  （array_add、trmm），其余 24 个必须 repair；lat 在 8/10 个 kernel 上劣于基线（三种规则一致），
  conv2d 15→23、gemm_pace 13→27、mults1 16→23。mapper 耗时也涨：conv2d 2.4s→12s，
  gemm_pace 12s→70s。密集 pin 让 PathFinder 在自己更擅长的搜索里没法回退。
* **20–50% 是安全区，但不总是更好**。25% critical 在 10 个 kernel 里 7 个与基线持平、3 个更差、
  0 个更好；50% degree 有 2 个更好（array_add 4/11→4/9、conv3 4/13→4/12）、6 个更差。
  没有任何一档在所有 kernel 上优于基线——A 臂的价值不在「钉得更多」，而在「钉哪几个」。
* **选点规则有细分信号**：critical 明显比 bank 稳（25%：7 持平 vs bank 3 持平 7 更差）；
  degree 在 50% 偶尔能赚（array_add、conv3）；bank 的局部性收益被 repair 的丢弃抵消。
* k=25% 时 `pins_applied`（见 §3）常大于 `pins_total`，例如 conv2d degree 90/10 —— 说明 mapper
  在内部多轮放置里反复尝试这些 pin，而不是「10 个 pin 全被采纳一次」。
* 明确回答「LLM 该管多少」：本数据支持 **20–50% 的关键节点**（critical/degree 混合）而不是全量；
  下一步用真模型在 25%/50% 两档做 A/B，而不是 100%。

### 2.3 map 级评测（旧表，保留供对照）

A 臂用 `--placer greedy` 代替 LLM（本机无模型端点；这是**参考布局**，不代表模型质量）：

| kernel | nodes | edges | ResMII | A 臂 strict | A 臂 repair | `-m 0` 基线 |
|---|---|---|---|---|---|---|
| array_add | 20 | 23 | 1 | ok II=4 lat=11 | ok II=4 lat=11 | II=4 lat=11 |
| gemm | 26 | 31 | 1 | ✗ mapper 拒绝 STORE pin | ok II=5 lat=17 | II=4 lat=13 |
| hpcg | 24 | 30 | 1 | ✗ Route Failed（II=4/5/6/8） | ok II=4 lat=14 | II=4 lat=11 |
| trmm | 11 | 12 | 1 | ok II=4 lat=6 | ok II=4 lat=6 | II=4 lat=6 |
| array_cond | 25 | 32 | 1 | ✗ Route Failed（II=4） | ok II=4 lat=19 | II=4 lat=13 |
| fft | 67 | 93 | 1 | ✗ mapper 崩溃 | ✗ | ✗ |

### 2.4 II=3：pin 能不能把 PathFinder 从 II 的死局里拉回来？

对 10 个 kernel 强制 `ii_start=3`（比基线低一档），k ∈ {0, 25%, 50%} × {degree, critical}。
k=0 是 `-m 0` 基线；k>0 用贪心参考布局 + 固定选点、repair(3)。完整 60 行：`work/eval-ii/ii3.csv`。

结论是**不能**，而且原因在 mapper 内部，不在 pin：

| kernel | RecMin II | UserGiven II | mapper 实际 target II | k=0 结果 | pin 25%/50% |
|---|---|---|---|---|---|
| array_add | 4 | 3 | 4 | ok II=4 lat=11 | ok（II 仍是 4） |
| trmm | 4 | 3 | 4 | ok II=4 lat=6 | ok（II 仍是 4） |
| mults1 | 4 | 3 | 4 | ok II=4 lat=16 | ok（II 仍是 4） |
| conv3 | 4 | 3 | 4 | ok II=4 lat=13 | ok（II 仍是 4） |
| gemm / hpcg / array_cond / mac / conv2d | 4 | 3 | 4 | ok II=4 | 贪心布局在 II=3 不可行 |
| gemm_pace | 9 | 3 | 9 | ok II=9 lat=13 | 贪心布局在 II=3 不可行 |

机制：mapper 日志第一行就是
`Initial target II = 4 (ResMin II = 2, RecMin II = 4, UserGiven II = 3)`——
它取 `max(UserGiven, RecMin)`，**RecMin II 是图/架构的结构下界，pins 只约束位置、不改变 RecMin**。
所以 II=3 对 9/10 个 kernel 结构性不可达（gemm_pace 的 RecMin 是 9），pin 密度无法改变这一层。
能钉的 4 个 kernel 在 II=3 下，贪心布局也放不下（依赖延迟 + 3 个相位），pinned 尝试直接
`placement_not_found`（启发式没找到，不是穷举证明的 `placement_infeasible`，见 §2.6）；
而允许 mapper 自己升到 RecMin 后，所有 kernel 本来就能映射。

副产品：60 行里 `mults1 critical 50%` 一次 mapper 内部断言崩溃
（`PathFinderMapper.cpp:3871 printHyCUBEBinary: Assertion 'pe->getSingleRegPort("ER_RI")...' failed`），
report 写 ok 但没有 bitstream；已作为工具链 bug 记录，不影响结论。

**对 A 臂的意义**：「pin 救 II」在 vendor mapper 里不成立——II 由 RecMin 锁死，pin 只能影响
同一 II 内的放置/布线成功率。要让 pin 改变 II，得先改 RecMin 的算法（例如允许它向上/向下试探），
那是工具链改动，不是布局提示能做的事。

### 2.5 同 II 下的可布性：pin 到底买到什么（固定 II=4）

数据不新跑，直接把 §2.2 的 II=4 行重排：`work/eval-density/density.json` →
`scripts/analyze_density.py` → `work/eval-density/same_ii.csv`（每 kernel×rule×k 一行，
含 `mappable`、`first_call_ok`、`lat`、`pins_applied`、`mapper_time_s`、`failure_stage`）。
mapper 会把 II 下限提到 RecMin，所以 `gemm_pace` 的实际 target 是 **9**（其余 4），表按实际 II 标。

`ok`=第一次 mapper 调用就过；`ok*`=strict 首调失败、repair 第二次才过；`FAIL`=两次都没过；
`—`=我们的贪心参考布局在 II=4 没放下（`placement_not_found`：启发式未找到，不是穷举证明的
`placement_infeasible`；§2.6 有诊断）；`base` 行为 `-m 0`：

| kernel | base | degree 25/50/100 | critical 25/50/100 | bank 25/50/100 |
|---|---|---|---|---|
| array_add | ok | ok / ok / ok | ok / ok / ok | ok / ok / ok |
| gemm | ok | — / — / — | — / — / — | — / — / — |
| hpcg | ok | ok / ok / ok* | ok / ok / ok* | ok / FAIL / ok* |
| trmm | ok | ok / ok / ok | ok / ok / ok | ok / ok / ok |
| array_cond | ok | ok / ok / ok* | ok / ok / ok* | ok / ok / ok* |
| mac | ok | ok / ok / ok* | FAIL / ok / ok* | ok / ok / ok* |
| mults1 | ok | ok / ok / ok* | ok / ok / ok* | ok / ok / ok* |
| conv3 | ok | ok / ok / ok* | ok / ok / ok* | ok / ok / ok* |
| conv2d | ok | ok / ok / ok* | ok / ok / ok* | ok / ok / ok* |
| gemm_pace | ok | ok / ok / ok* | ok / ok / ok* | ok / ok / ok* |

聚合（分母 = 该档真正发生 mapper 调用的 kernel 数，gemm 因放置器放不下被排除）：

| rule | k | 可布 (ok/calls) | 首调即过 | 平均 mapper 时间 | lat vs base（好/平/差） |
|---|---|---|---|---|---|
| degree | 25% | 9/9 | 9/9 | 6.3s | 1 / 2 / 6 |
| degree | 50% | 9/9 | 9/9 | 4.1s | 2 / 2 / 5 |
| degree | 100% | 9/9 | 2/9 | 12.2s | 0 / 0 / 9 |
| critical | 25% | 8/9 | 8/9 | 3.1s | 0 / 7 / 1 |
| critical | 50% | 9/9 | 9/9 | 5.4s | 0 / 6 / 3 |
| critical | 100% | 9/9 | 2/9 | 12.3s | 0 / 0 / 9 |
| bank | 25% | 9/9 | 9/9 | 4.4s | 0 / 3 / 6 |
| bank | 50% | 8/9 | 8/9 | 3.6s | 0 / 2 / 6 |
| bank | 100% | 9/9 | 2/9 | 12.6s | 0 / 0 / 9 |

结论（对「布局=可布性」这个假设）：

* **II=4 下 pin 没有扩大可布集合**。基线 10/10 全部可映射；25–50% 档也没有映射出任何一个
  基线映射不了的 kernel（gemm 除外，见下）；100% strict 反而只有 2/9 首调成功。
  在这批 kernel 上，「LLM 买可布性」**不成立**——至少不能靠 4x4、RecMin II=4 的数据证明。
* **全量 pin 的代价是首调成功率与时间**：strict 2/9，repair 虽把可布拉回 9/9，但平均 mapper
  时间 12.2–12.6s，是 25–50% 档的 2–3 倍；lat 在 9/9 上全部劣于基线（与 §2.2 一致）。
* **25–50% 是「不亏可布性」的控制旋钮**：首调成功率 8–9/9（两处单次失败：hpcg bank-50、
  mac critical-25），时间与基线同量级；lat 大多持平或略差，只有 degree-50% 有 2 个赢
  （array_add 11→9、conv3 13→12），但它也有 5 个更差——仍是「钉哪几个」的问题，不是「钉多少」。
* **gemm 的 `—` 已诊断，不是「不可布」**：`-m 0` 在 II=4 映射成功（lat=13），把 mapper 自己的
  放置 dump 出来用我们的 `validate()` 校验，**0 error、11 条 R3 告警**——约束不严于 mapper，
  是贪心参考布局的搜索不完备（§2.6）。所以 gemm 不能作为「pin 买可布性」的正证据，也不能作为
  反证；结论只保留「这批数据里 25–50% pin 没扩大可布集合」。

### 2.6 gemm 的 `—` 是搜索不完备，不是不可布（诊断）

父代理指出：mapper `-m 0` 在 II=4 能映射 gemm，而我们的贪心报放不下，两者同时成立只有
「贪心不完备」或「校验比 mapper 严」两种解释。做了三步诊断：

1. 给 vendor mapper 加 `--dump-placement <json>`（`include/morpher/util/util.h` 注册，
   `CGRA_xml_compiler.cpp` 在映射成功后导出 `{node,op,x,y,t,lat}`），重建
   `toolchain/out/cgra_xml_mapper`。`-m 0 -i 4` 在 gemm 上 Map Success II=4 lat=13，
   dump 出 26 个节点的完整放置（`work/diagnostics/gemm_m0_placement.json`）。
2. 用 `validate(facts, dump_pins, 4)` 校验：**ok=True, errors=0, warnings=11**（全部是 R3
   跳数告警）。→ 我们的静态约束没有比 mapper 严（R3 那次是过约束，这次不是）。
3. 定位贪心失败点：`no legal slot for node 9 (op=MUL, asap=8) at II=4`，seed 0–11 全失败；
   mapper 把 node 9 放在 `(x=1,y=0,t=2)`。贪心按 `asap%II` 的固定相位、单趟无回溯，
   父节点的相位选择把 MUL 锁死 → **启发式不完备**。

结论与处理：

* `placement_infeasible` 只留给**穷举证明**；启发式失败一律 `placement_not_found`
  （`cli export-placement`、`eval_ii` 同步改名）。这条状态语义会一路影响结论可信度：
  gemm 这一格不能被当成「pin 不是可布性杠杆」的证据。
* 记成「静态模型过约束」的**反面**案例：这次约束是对的，错在搜索。以后遇到
  `placement_not_found`，先跑 `scripts/diag_placement.py --dfg ... --arch ... --ii ...`
  看 mapper 是否给出了我们漏掉的布局。
* 若要补 gemm 的 pinned 数据，需要带回溯/随机的放置器（或真模型），不是改校验规则。

复现：

```
python scripts/diag_placement.py --dfg work/eval-density/gemm/degree-0.0/gemm_PartPredDFG.xml     --arch work/eval-density/gemm/degree-0.0/gemm_mem.json --ii 4     --out work/diagnostics/gemm_m0_placement.json
# -> validate: ok=True errors=0 warnings=11
```

### 2.7 仿真验证（生成 trace）：8/10 kernel 全对，hpcg/mults1 是 trace 对不齐

trace 打通后（§7），对每种成功映射都跑了仿真（`work/simv/<k>-<tag>.log`）。0 失配的：

| kernel | II | lat | Matches | Mismatches | cycles |
|---|---|---|---|---|---|
| array_add | 4 | 11 | 387 | 0 | 134 |
| array_cond | 4 | 13 | 243 | 0 | 86 |
| conv2d | 4 | 15 | 523 | 0 | 30 |
| conv3 | 4 | 13 | 163 | 0 | 38 |
| trmm | 4 | 6 | 5 | 0 | 9 |
| gemm | 5 | 13 | 783 | 0 | 44 |
| gemm_pace | 9 | 13 | 723 | 0 | 3600 |
| mac | 5 | 10 | 167 | 0 | 94 |

两个失败：

- **hpcg**：trace_1 下 90 matches / 1 mismatch（trace_2–4 分别 89/2、89/2、88/3）。
- **mults1**：79 matches / 8 mismatches。

失配地址集中在数组尾部/循环外数据。原因不是布局相位（同样的 placement 在 array_add 上
全对），而是 **trace 与 DFG 的循环体不对齐**：instrumentation 给的是整数组前后快照，DFG
只映射最内层循环（`NODES` 里的循环体）；array_add 的「整程序 = 循环体」恰好全对，hpcg/mults1
的循环体只覆盖部分数组。修法是按变量/循环裁剪 trace，或让 DFG 覆盖整个程序——本轮未做，
因此 hpcg/mults1 的仿真结果记为「未验证」，不进入错误率分母。

`gemm_pace` 的 II=9/3600 cycles 来自 RecMin II=9，不是布局失败。`mac` 的 167 匹配对应它较小的数组。

**后续（回溯版贪心，2026-10-27 晚）**：给贪心加 `max_backtracks=200`（默认，实际 gemm 用 10 次
就够）后，gemm 在 II=4 能放出来了：26 pin、0.00s、`validate()` 0 error。但 mapper 的 strict
模式仍拒绝 5 个 pin（`no candidate DP matches pin (x=3,y=0,t=1) for node 5 (op=ADD)`，
`estimateRouting` 只给 54 个候选、不含该 PE×相位）——mapper 的 DP 候选集比我们的 R1/R2/R4
窄，属于第三层约束（数据可用性），我们的 validate 不建模。repair 模式下 21/26 pin 被接受，
II=4 lat=14（`-m 0` 基线 lat=13）。所以 gemm 的 `—` 现在应读作「可放置，但 strict 全 pin
不可行」，不再读作「放不出」。

## 2.9 总表：pin 到底买到了什么（10 kernel）

数据 `work/master_table.csv`（`scripts/master_table.py` 生成）。`none` = `-m 0`；
`oracle` = mapper 自己 `-m 0` 的最终放置（`--dump-placement`，按 `t = lat % II` 转 pin）回灌 strict
（`--pins_mode 1`）；`memory` = 只钉该放置里的 LOAD*/STORE* 节点；`memory repair` 同样只钉内存节点但
用 `--pins_mode 3`。`accept` = pins_applied/pins_total。

| kernel | none: II/lat/time | oracle: accept, II/lat | memory strict: accept | memory repair: accept, II/lat/time | sim |
|---|---|---|---|---|---|
| array_add | 4/11/0.91s | 20/20, 4/11 | 1/6 | 6/6, 4/11/0.91s | verified 387/0 |
| array_cond | 4/13/1.55s | 25/25, 4/13 | 1/6 | 6/6, 4/13/1.53s | verified 243/0 |
| conv2d | 4/15/2.08s | 39/39, 4/15 | 1/8 | 8/8, 4/15/2.13s | verified 523/0 |
| conv3 | 4/13/1.38s | 28/28, 4/13 | 1/7 | 7/7, 4/13/1.34s | verified 163/0 |
| gemm | 4/13/3.19s | 26/26, 4/13 | 1/7 | 7/7, 4/13/3.51s | verified 783/0 |
| gemm_pace | 9/13/12.06s | 49/49, 9/13 | 1/7 | 7/7, 9/13/12.61s | verified 723/0 |
| hpcg | 4/11/5.32s | 24/24, 4/11 | 1/5 | 5/5, 4/11/5.17s | unverified 90/1 |
| mac | 4/10/1.29s | 22/22, 4/10 | 1/5 | 5/5, 4/10/1.32s | sim-crash (toolchain) |
| mults1 | 4/16/2.59s | 25/25, 4/16 | 1/4 | 4/4, 4/16/2.57s | unverified 79/8 |
| trmm | 4/6/0.43s | 11/11, 4/6 | 1/3 | 3/3, 4/6/0.41s | verified 5/0 |

读法：
- **oracle 与 none 的 II/lat/时间逐项相同**：这是必然的（oracle 就是 mapper 自己的解），
  它说明「全量 pin 的价值上限 = 0」——要证明 pin 有用，必须拿出比 mapper 自己更好的解，
  而不是把它的解喂回去。
- **memory strict 全部 1/N 失败**：只钉内存节点也会让 strict 无解，因为未钉节点的 DP 扩展
  与钉住的位置冲突。**这是第三层约束（DP 候选集）的又一次实证**，而且发生在「最该有用」的节点上。
- **memory repair 10/10 接受、II/lat/时间与 none 持平**：这是「选择性 pin」的第一个正证据——
  选有结构含义的节点（内存节点位置本来被 bank 绑死），代价接近零；比按 degree/critical 排序选点
  更可能成立。
- **hpcg、mults1 标 unverified**：trace 是整程序的数组快照，DFG 只覆盖最内层循环（hpcg 的
  `please_map_me` 在内层 j 循环里，mults1 的循环体是 i=1..18），两者循环体边界不对齐；
  90/1 与 79/8 的失配是 trace 生成方式造成的，不是布局相位错。**不计入 pass**。
- **mac 标 sim-crash（toolchain）**：II=4 下 simulator 断言 `!(I1isvalid && I2isvalid)` 崩溃
  （两条指令抢同一 PE 槽）。II=5 时同一 kernel 能 167/0 通过（见 §2.7），属工具链/映射器问题，
  不计入任何一臂的错误率。

## 3. 管线与设计决策

```
init -> dfg -> facts -> [place -> validate -> map -> sim] -> report
                            ^          |        |      |
                            +----------+--------+------+  带证据重问（有界）
```

* **LLVM 段不是搜索变量**：`tools/dfg.py` 里 clang/opt/DFG pass/update_mem_alloc 是字面量，
  输入只有 kernel 配置与 vendor 源码。DFG 产物按 *function* 命名（`<func>_PartPredDFG.xml`），
  不是按 kernel 名——实测出来的坑。
* **LLM 只产布局**：契约 `{"pins":[{"node","x","y","t"}],"reasoning","ii"}`；`tools/validate.py`
  先做便宜的检查（R1 一 PE 一相位、R2 op 支持、R4 边界/相位、R5 bank、常量 27bit、容量）。
* **R3 已降级为告警（父代理实测证据）**：B 臂在 verify 模式注入 3 跳绕行、相位余量 0，
  仿真仍 `Mismatches::0`；`9→10`（2 跳）、`11→12`（3 跳）slack=0 也合法。HyCUBE 一拍内组合
  转发，**跳数不是相位下界**。硬约束只保留依赖下界：`(t_child - t_parent - ni*II) mod II ≥ lat_parent`
  （ALU 1 拍、LOAD/STORE 2 拍）。R3 与 R6 只作为端口风险提示。
* **pin 密度开关**：`--pin-ratio/--pin-rule` 在 validate 前按规则削 pin（默认 1.0 = 全 pin）。
  k<1 自动切 repair；k=1 先 strict。规则写死在 `placers/pin_select.py`，脚本不能手挑节点。
* **`pins_applied` 的语义**：mapper 报告里的 `pins_applied` 是**它内部各轮放置尝试累计成功次数**，
  不是去重节点数，所以会出现 73/26、107/49 这种大于 `pins_total` 的值。消融表里用它衡量
  「pin 被反复尝试」的成本；`run.json` 同时给出 `pins.applied_semantics` 说明。
  没有跨 II 累计（method 3 不扫 II），也没有跨重试累计（每次 mapper 调用独立进程）。
* **II 阶梯**：固定 `[4,5,6,8]`（配置键 `ii_ladder`），首个成功即停，报告 ii_start/ii_final；
  不走贪心/桥接，四臂可同阶梯对比；facts 后用 `min_ii` 兜底跳到首个 ≥min_ii 的阶梯值。
* **失败分桶**：validate≤2、placement≤3、route≤3、sim≤1，余 3 机动；桶不互相挪用，
  桶空即放弃该 II 并升阶梯。总预算 `max_llm_calls=12`、`max_rounds=4`、
  `kernel_walltime_s=1200`，工具调用按剩余时间截断。
* **trace 三级策略**（`tools/trace.py`）：(a) 配置 `memtraces`/`--trace`；(b) 仓库自有
  `<kernel>_trace_*.txt`；(c) 报错并写明需要 `gcc-multilib libc6-dev-i386`。禁止引用外部 marl 目录。
* **槽位化**：`placers/__init__.py` 与 `routers/__init__.py` 是工厂；pipeline 只依赖
  `interfaces.Placer`/`Router` 协议。A 臂专属与共享工具的分界见 `docs/ARM_A_MODULES.md`。
* **尺寸无关的布局导出**（B 臂用）：`cli export-placement --dfg ... --arch ... --placer greedy
  --ii 4 --export-placement out/pins.json`。xdim/ydim 全部来自 arch JSON（PE 列表），同一生成器可跑
  4x4/3x3/3x2；失败时写 `{"status":"placement_not_found","error":...}` 并退出 2（不是空 pins
  糊过去）。状态名只用于启发式失败；`placement_infeasible` 留给穷举证明（§2.6）。
  也接受 B 文档里的无子命令写法 `cli --dfg ... --export-placement ...`。
* **工具链失败不计入臂的错误率**：`run_mapper` 在「report 说 ok 但没有 bitstream」或「rc<0 崩溃」时
  设 `failure_class="toolchain"`、`ok=False`；pipeline 见到 toolchain 直接 abort，不占
  placement/route 桶、不再重问，CDD 记 `layer="failure"`。`failure_class` 取值
  `static|runtime|semantic|toolchain`（见 `interfaces.MapReport`/`CDD`）。
* **Router 契约定稿**（父代理批准改一次）：
  `route(placement: Placement, facts: dict, arch: dict, ii: int, budget: dict) -> MapReport`，
  5 个位置参数、无关键字别名。`facts["paths"] = {"dfg","arch_json","mem_alloc","workdir"}`
  给 router 文件位置；`budget = {"pins_mode", "timeout_s"}`；`placement.pins` 为空走 `-m 0`。
  `MapReport` 新增 `binary`/`binary_sha256`，router 负责写 bitstream，pipeline 只按字段取文件。
* **LLM 路径脚手架**：`--placer llm` 在无 key 时用 `scripts/mock_replay.py`（坏 pin→好 pin）
  跑通，输出与 `greedy/fixed` 同一套 `Placement`（`pins[] + ii + note + source`），预算/重试/落盘
  行为一致；真模型只需 `export CGRA_LLM_{BASE_URL,API_KEY,MODEL}`。mock 回放 2 次调用可达
  II=4 lat=11 243/0/133。

## 4. 已知限制

1. **trace 已能生成（§7）**：用 dfggen 的 instrumentation runtime + `scripts/gen_trace.sh`
   为 9 个 kernel 生成了 `work/traces/<k>/<k>_trace_1.txt`。宿主程序是原生 x86_64（本机
   有 32 位 libc 但缺 32 位 libstdc++ 头 `/usr/include/c++/13/32`，instrumentation.cpp 编不过
   `-m32`）；trace 只记录 4 字节数据，宿主字长不影响仿真。array_add 用生成 trace 跑出
   `Matches::387 Mismatches::0`，与 README 的期望值一致。fft 仍因 mapper 崩溃排除。
2. **fft excluded: upstream mapper crash on empty bank attribute**。DFG 能生成（67 节点/93 边），
   但 mapper 在任意 II 崩溃 `std::logic_error: basic_string::_M_construct null not valid`
   （method 0 与 pinned 均崩），疑似 fft 的 arch/DFG 里有空字符串属性。是工具链 bug，不修、不报。
3. **A 臂 map 级与消融都用贪心参考布局**，不是 LLM；strict/repair 差异说明「合法 pin」与
   「mapper 可实现的 pin」不是一回事（STORE 的端口/相位、route 拥塞）。
4. **R5 只是建议**：`update_mem_alloc.py` 生成的 arch 无 `CGRA.CONNECTIONS`，推不出 PE→bank；
   mapper 也不按 bank 限制放置，最终裁判是仿真。
5. **仿真失败归因很粗**：只有「相位错」一条 CDD，未把失配地址映射回具体节点/边；hpcg/mults1 的失配是 trace 与 DFG 循环体不对齐（§2.7），不是布局错误，但当前 CDD 仍会把它们报成相位问题。
6. `max_rounds=4` 与重试预算会互相挤压；未装包时需 `PYTHONPATH=src`（`pyproject.toml` 已备好）。
7. 缺失 kernel 已有替代（见 §5）；9 个 kernel 现在都有 trace（§7），`gemm_pace` 的 RecMin II
   是 9，比其它 kernel 高一代。
8. **mapper 不接受低于 RecMin 的 II**（§2.4）：`-i 3` 会被 `max(UserGiven, RecMin)` 提升到 4/9，
   且 method 3 也不跳 II。pinned 实验如果想考察「同 II 内的布线成功率」，必须在 RecMin 及以上做。
9. `mults1 critical 50%` 触发 mapper 内部断言崩溃（`PathFinderMapper.cpp:3871`），report 声称 ok
   但无 bitstream；工具链 bug，不修。A 侧已用 `failure_class="toolchain"` 把它挡在错误率之外。
10. §2.5 的 gemm 行没有 pinned 数据：贪心参考布局在 II=4 搜不到（`placement_not_found`）。
    已诊断：mapper 的合法放置经我们 validate 为 0 error，是搜索不完备而非不可布（§2.6）；
    补测需要带回溯/随机的放置器或真模型。

11. **vendor mapper 的 `--dump-placement` 补丁不在 git 里**（`toolchain/vendor/` 被 ignore）：
    源码改动用 `scripts/patch_mapper_dump.py` 幂等重放，重建用 `scripts/rebuild_mapper.sh`；
    当前 `toolchain/out/cgra_xml_mapper` 已带 dump 功能，换机器/重拉 vendor 后要先跑这两个脚本。
12. **时间戳 bundle 不是唯一副本**：`work/armA/<k>/<ts>/` 下 dfg/ 与 bitstream 可能被其它臂的
    探针覆盖（同 DFG 同 II 但映射有随机性）。稳定输入与评测口径用 `work/kernels/<k>/`
    （`scripts/export_kernels.py` 生成），时间戳 bundle 只作调试。

13. **`validate()` 通过 ≠ mapper 能实现，两个方向都会错**。三层约束已各出现一次：

    | 案例 | 谁更严 | 现象 | 后果 |
    |---|---|---|---|
    | R3 相位差 | 我们过严 | 误杀合法布线 | 已降级为 warning |
    | gemm 贪心 | 我们搜索不完备 | 误报不可放 | 改 `placement_not_found` + 回溯 |
    | DP 候选集 | mapper 更严 | strict 拒合法 pin | 全量 pin 在 strict 下不可行 |

    直接结论：**全量 pin 是错的工作点**——strict 会被 DP 候选拒，repair 牺牲 lat，时间还翻倍。
    对 B 的口径：`oracle pins` 只能用 mapper 自己的放置，且它不带来 II/lat 收益（见 §2.9）。

## 5. benchmark 集合（替换 md/stencil/spmv）

`toolchain/vendor/mapper/applications/` 里只有预生成的 XML（无 `.c`），无法走 LLVM 段；
改从 `toolchain/vendor/dfggen/benchmarks/` 的源码里挑，全部用 `main` 或内核函数作 DFG 入口。
`-m 0` 基线映射成功的 5 个新 kernel（均已加 `configs/*.yaml`）：

| kernel | 来源 | nodes/edges | `-m 0` 基线 |
|---|---|---|---|
| mac | `cgrame_microbench/mac/mac.c` | 22/28 | II=4 lat=10 |
| mults1 | `cgrame_microbench/mults1/mults1.c` | 25/32 | II=4 lat=16 |
| conv3 | `cgrame_microbench/conv3/conv3.c` | 28/32 | II=4 lat=13 |
| conv2d | `pace_benchmarks/conv2d/no_unroll_2x2_filt/convolution2d.c` | 39/46 | II=4 lat=15 |
| gemm_pace | `pace_benchmarks/gemm/gemm_no_unroll_flattened/gemm.c` | 49/72 | II=4 lat=13 |

* `opencgra_benchmarks/fir` 的 DFG pass 直接 OOM（rc=-6），不用；`morpher_benchmarks/gemm` 也 OOM，
  用 woset `gemm_1`。
* 替换后仍保留原 6 个：array_add、gemm、hpcg、trmm、array_cond（fft 排除）。

## 6. 下一步

1. 真模型在 **25%/50% critical(+degree)** 两档做 A/B，而不是 100%；统计首次合法率、调用数、
   II/lat、`pins_applied/pins_total`。
2. ~~解 trace 生成~~ 已完成（§7）并用它做了仿真验证（§2.7）：8/10 kernel 0 失配；
   hpcg/mults1 是 trace 与 DFG 循环体不对齐，需要按循环裁剪 trace 才算完。
3. 把「dense pin 是负收益」做成 prompt 约束：要求模型只给 20–50% 的关键节点，其余留给 PathFinder。
4. CDD 细化：用 `SIMULATOR=1` 的 `kII/PC/x/y/OP` 定位到具体 PE，把责任分到节点或边。
5. 合并：按 `docs/ARM_A_MODULES.md` 注册 B 臂的 `placers/sa.py`、`routers/llm.py`。
6. 「pin 救 II」方向已封闭（§2.4）；若要继续 II 维度，唯一入口是改 RecMin 算法（工具链改动），
   或者在同 RecMin 下比较 pinned/unpinned 的布线耗时与首次成功率。

## 7. trace 生成（本轮打通）

morpher 的 trace 是**插桩后的程序**跑出来的：dfggen 的 instrumentation runtime
（`toolchain/vendor/dfggen/src/instrumentation/instrumentation.cpp`）在循环边界打印每个变量的
`var_name,offset,pre-run-data,post-run-data`。`scripts/gen_trace.sh` 复现了 morpher
`benchmarks/*/run_pass.sh` 的 recipe：

```
clang -D CGRA_COMPILER -emit-llvm        # 内核源码
opt -gvn -mem2reg ... -simplifycfg       # 规范化
opt -load libdfggenPass.so -fn <func> -nobanks 2 -banksize 2048 -type PartPred -skeleton
g++ -std=c++14 -c instrumentation.cpp    # 原生 x86_64（无 32 位 libstdc++ 头）
llc k_inst.ll -o k_inst.o; g++ -no-pie k_inst.o instrumentation.o -o final
./final   # 在 memtraces/ 下写 trace，`main` 的返回值常是 checksum，退出码非 0 正常
```

`scripts/gen_traces_all.sh` 按 `configs/*.yaml` 批量跑，结果在 `work/traces/<kernel>/`：

| kernel | traces | 备注 |
|---|---|---|
| array_add | 1 | 用生成 trace 仿真 `Matches::387 Mismatches::0`（README 期望值） |
| array_cond | 1 | |
| trmm | 4（源 992 条中取前 4） | |
| hpcg | 4 | |
| gemm | 4（源 64 条中取前 4） | |
| gemm_pace | 1 | RecMin II=9 |
| conv3 / conv2d / mac / mults1 | 1 / 4 / 1 / 1 | |

`--trace` 显式传入时优先于三级查找；`run.json.sim.trace` 记录实际用了哪一条。
`fft` 仍因 mapper 崩溃排除，没有 trace。

仿真验证结论见 §2.7：8/10 kernel 0 失配（array_add 387/0 与 README 期望一致）；hpcg/mults1 的失配是 trace 与 DFG 循环体不对齐，记「未验证」而不是错误率。
