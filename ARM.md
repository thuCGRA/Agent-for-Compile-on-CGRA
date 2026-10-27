# 臂 A：LLM 布局 + PathFinder 布线（M1+）

状态：**可运行**。`array_add` 端到端 II=4、Mismatches::0；坏 pin 被本地校验拦下并带错误重问；
同 pins 两次运行哈希一致。10 个 kernel 的 map 级评测 + pin 密度消融（k=0/25%/50%/100% ×
degree/critical/bank）已完成，`-m 0` 基线同表对照。

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
