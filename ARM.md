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
`placement_infeasible`；而允许 mapper 自己升到 RecMin 后，所有 kernel 本来就能映射。

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
`—`=放置器在 II=4 压根放不下（没有 mapper 调用）；`base` 行为 `-m 0`：

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
* **唯一可能真有可布性价值的是 gemm**：`-m 0` 能映射（II=4 lat=13），但我们的贪心参考布局
  在 II=4 放不下 26 个节点/4 个相位（三规则都报 `placement_infeasible`），所以表里是 `—`。
  如果真模型能在 II=4 放出合法布局而映射成功，那才是「布局买到可布性」——这需要真模型数据。

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
  4x4/3x3/3x2；失败时写 `{"status":"placement_infeasible","error":...}` 并退出 2（不是空 pins 糊过去）。
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

1. **trace 生成被机器阻塞**。生成需要 32 位 i386 运行时（`gcc-multilib` + `libc6-dev-i386`），
   本机没有且无 sudo。因此除 array_add 外都只能到 S5，`sim="skipped(no trace)"`。
2. **fft excluded: upstream mapper crash on empty bank attribute**。DFG 能生成（67 节点/93 边），
   但 mapper 在任意 II 崩溃 `std::logic_error: basic_string::_M_construct null not valid`
   （method 0 与 pinned 均崩），疑似 fft 的 arch/DFG 里有空字符串属性。是工具链 bug，不修、不报。
3. **A 臂 map 级与消融都用贪心参考布局**，不是 LLM；strict/repair 差异说明「合法 pin」与
   「mapper 可实现的 pin」不是一回事（STORE 的端口/相位、route 拥塞）。
4. **R5 只是建议**：`update_mem_alloc.py` 生成的 arch 无 `CGRA.CONNECTIONS`，推不出 PE→bank；
   mapper 也不按 bank 限制放置，最终裁判是仿真。
5. **仿真失败归因很粗**：只有「相位错」一条 CDD，未把失配地址映射回具体节点/边。
6. `max_rounds=4` 与重试预算会互相挤压；未装包时需 `PYTHONPATH=src`（`pyproject.toml` 已备好）。
7. 缺失 kernel 已有替代（见 §5），但替代 kernel 没有 trace，只能到 S5。
8. **mapper 不接受低于 RecMin 的 II**（§2.4）：`-i 3` 会被 `max(UserGiven, RecMin)` 提升到 4/9，
   且 method 3 也不跳 II。pinned 实验如果想考察「同 II 内的布线成功率」，必须在 RecMin 及以上做。
9. `mults1 critical 50%` 触发 mapper 内部断言崩溃（`PathFinderMapper.cpp:3871`），report 声称 ok
   但无 bitstream；工具链 bug，不修。A 侧已用 `failure_class="toolchain"` 把它挡在错误率之外。
10. §2.5 的 gemm 行没有 pinned 数据：贪心参考布局在 II=4 放不下（`placement_infeasible`），
    不是 pin 失败；补测需要真模型或更强的放置器。

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
2. 解 trace 生成（装 `gcc-multilib libc6-dev-i386` 或换机器），把 10 个 kernel 推到 S6。
3. 把「dense pin 是负收益」做成 prompt 约束：要求模型只给 20–50% 的关键节点，其余留给 PathFinder。
4. CDD 细化：用 `SIMULATOR=1` 的 `kII/PC/x/y/OP` 定位到具体 PE，把责任分到节点或边。
5. 合并：按 `docs/ARM_A_MODULES.md` 注册 B 臂的 `placers/sa.py`、`routers/llm.py`。
6. 「pin 救 II」方向已封闭（§2.4）；若要继续 II 维度，唯一入口是改 RecMin 算法（工具链改动），
   或者在同 RecMin 下比较 pinned/unpinned 的布线耗时与首次成功率。
