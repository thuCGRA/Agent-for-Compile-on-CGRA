# 臂 A：LLM 布局 + PathFinder 布线（M1+）

状态：**可运行**。`array_add` 端到端 II=4、Mismatches::0；坏 pin 被本地校验拦下并带错误重问；
同 pins 两次运行哈希一致。map 级（到 S5）已在 5 个 kernel 上跑通，`-m 0` 基线同表对照；
fft 因 mapper 自身崩溃未纳入（见 §4）。

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

# map 级评测：A 臂（参考用贪心布局）+ `-m 0` 基线，不需要 trace
PYTHONPATH=src python scripts/eval_map.py --kernels array_add,gemm,hpcg,trmm,array_cond

# 单元测试（不需要工具链）
python -m pytest -q
```

退出码：0 = 映射且仿真通过（或映射成功但没有 trace）；2 = 同一含义但由 `unverified` 状态返回；
1 = 失败。有 trace 时 S6 仿真，无 trace 时记 `status=ok, sim="skipped(no trace)"`，不谎报通过。

每次运行落在 `work/armA/<kernel>/<时间戳>/`（work/ 已 gitignore）：`config.yaml`、`facts.json`、
`pins.json`、`report.json`、`sim.txt`、`bitstream.bin`、`llm_calls.jsonl`、`run.json`、`run.log`。

## 2. 结果

`array_add` 端到端（4x4，II=4）：

| 路径 | II | lat | Matches | Mismatches | cycles | LLM 调用 |
|---|---|---|---|---|---|---|
| smoke 基线（method 0，无 pin） | 4 | 11 | 243 | 0 | 134 | - |
| A 臂回放已知 pins（strict） | 4 | 11 | 243 | 0 | 133 | 0 |
| A 臂 + mock（先坏 pin 后好 pin） | 4 | 11 | 243 | 0 | 133 | 2 |

* 坏 pin 恢复：第 1 次回答把 node 0 放到 `x=9`，`validate` 报
  `node 0 at (9,2): outside the 4x4 grid`，错误原文回给模型；第 2 次通过。
* 可复现：同一份 pins 连跑两次，`binary_sha256=ab64645e…`、pins/report 的 sha256 均相同。
* mapper 1.0–1.5s，DFG 0.1–0.5s，整条链约 5s。`python -m pytest -q`：19 passed。

### map 级评测（无需 trace，到 S5 为止）

A 臂用 `--placer greedy` 代替 LLM（本机无模型端点；这是**参考布局**，不代表模型质量）：

| kernel | nodes | edges | ResMII | A 臂 strict | A 臂 repair | `-m 0` 基线 |
|---|---|---|---|---|---|---|
| array_add | 20 | 23 | 1 | ok II=4 lat=11 | ok II=4 lat=11 | II=4 lat=11 |
| gemm | 26 | 31 | 1 | ✗ mapper 拒绝 STORE pin | ok II=5 lat=17 | II=4 lat=13 |
| hpcg | 24 | 30 | 1 | ✗ Route Failed（II=4/5/6/8） | ok II=4 lat=14 | II=4 lat=11 |
| trmm | 11 | 12 | 1 | ok II=4 lat=6 | ok II=4 lat=6 | II=4 lat=6 |
| array_cond | 25 | 32 | 1 | ✗ Route Failed（II=4） | ok II=4 lat=19 | II=4 lat=13 |
| fft | 67 | 93 | 1 | ✗ mapper 崩溃 | ✗ | ✗ |

* strict = `pins_mode 1`（默认，节点全 pin 时）；repair = `--pins-mode 3`，mapper 丢弃不可满足的 pin
  自行补全（`pins_applied` 分别为 20/73/23/11/48）。repair 的 II/lat 与基线接近或略差，
  说明「贪心 pin + repair」能落地但**没有**超过基线——这正是 LLM 布局要解决的问题。
* 全部 kernel 的 ResMII=1；ii_start 一律 4，阶梯 `[4,5,6,8]`，首个成功即停。

## 3. 管线与设计决策

```
init -> dfg -> facts -> [place -> validate -> map -> sim] -> report
                            ^          |        |      |
                            +----------+--------+------+  带证据重问（有界）
```

* **LLVM 段不是搜索变量**：`tools/dfg.py` 里 clang/opt/DFG pass/update_mem_alloc 是字面量，
  输入只有 kernel 配置与 vendor 源码。DFG 产物按 *function* 命名（`<func>_PartPredDFG.xml`），
  不是按 kernel 名——这是实测出来的坑。
* **LLM 只产布局**：契约 `{"pins":[{"node","x","y","t"}],"reasoning","ii"}`；`tools/validate.py`
  先做便宜的检查（R1 一 PE 一相位、R2 op 支持、R4 边界/相位、R5 bank、常量 27bit、容量）。
* **R3 已降级为告警（父代理实测证据）**：B 臂在 verify 模式注入 3 跳绕行、相位余量 0，
  仿真仍 `Mismatches::0`；`9→10`（2 跳）、`11→12`（3 跳）slack=0 也合法。HyCUBE 一拍内组合
  转发，**跳数不是相位下界**。硬约束只保留依赖下界：`(t_child - t_parent - ni*II) mod II ≥ lat_parent`
  （ALU 1 拍、LOAD/STORE 2 拍）。R3 与 R6 只作为端口风险提示。
* **pins_mode 选择**：全量 pin → `strict(1)`；少 pin → `repair(3)`；`--pins-mode` 可强制。
  repair 下的 `pins_applied/rejected` 进 report，可量化模型布局被采纳的比例。
* **II 阶梯**：固定 `[4,5,6,8]`（配置键 `ii_ladder`），不走贪心/桥接，四臂可同阶梯对比；
  facts 后用 `min_ii` 兜底跳到首个 ≥min_ii 的阶梯值。
* **失败分桶**：validate≤2、placement≤3、route≤3、sim≤1，余 3 机动；桶不互相挪用，
  桶空即放弃该 II 并升阶梯。总预算 `max_llm_calls=12`、`max_rounds=4`、
  `kernel_walltime_s=1200`，工具调用按剩余时间截断。
* **trace 三级策略**（`tools/trace.py`）：(a) 配置 `memtraces:` 或 `--trace`；(b) 仓库自有
  `<kernel>_trace_*.txt`；(c) 报错并写明需要 `gcc-multilib libc6-dev-i386`。禁止引用外部 marl 目录。
* **槽位化**：`placers/` 与 `routers/pathfinder.py` 分别实现 Placer/Router 协议
  （`place(facts,arch,ii,budget)` / `route(job,dfg,arch,ii,pins,pins_mode)`，与 `interfaces.py` 一致），
  pipeline 只依赖协议，合并时可替换实现而不动流程。

## 4. 已知限制

1. **trace 生成被机器阻塞**。生成需要 32 位 i386 运行时（`gcc-multilib` + `libc6-dev-i386`），
   本机没有且无 sudo。因此除 array_add 外都只能到 S5，`sim="skipped(no trace)"`；
   `eval_map.py` 明确只做 map 级对照。
2. **fft 无法评测**：DFG 能生成（67 节点/93 边），但 mapper 对 fft 在任意 II 都崩溃
   `std::logic_error: basic_string::_M_construct null not valid`（method 0 与 pinned 均崩），
   疑似 fft 的 arch/DFG 里有空字符串属性（bank1 变量名为空）。这是工具链 bug，不是管线问题。
3. **A 臂 map 级用的是贪心参考布局**，不是 LLM；上表的 strict/repair 差异说明
   「合法 pin」与「mapper 可实现的 pin」不是一回事（STORE 的端口/相位、route 拥塞）。
4. **R5 只是建议**：`update_mem_alloc.py` 生成的 arch 无 `CGRA.CONNECTIONS`，推不出 PE→bank；
   mapper 也不按 bank 限制放置，最终裁判是仿真。
5. **仿真失败归因很粗**：只有「相位错」一条 CDD，未把失配地址映射回具体节点/边。
6. `max_rounds=4` 与重试预算会互相挤压；未装包时需 `PYTHONPATH=src`（`pyproject.toml` 已备好）。
7. 缺失 kernel：`md`、`stencil`、`spmv` 在 vendor 与整台机器上都不存在；morpher 的 `gemm`
   还会让 DFG pass OOM，已改用 woset 的 `gemm_1`（同算法）。

## 5. 下一步

1. 解 trace 生成（本机装 `gcc-multilib libc6-dev-i386` 或换一台有 32 位运行时的机器），
   把 5 个 kernel 推到 S6 仿真验证。
2. 真模型联调：同 kernel 跑 5 个 seed，统计首次合法率、平均调用数、II 分布、`pins_applied` 比例。
3. 用 strict 失败的案例（gemm STORE、hpcg/array_cond route）改 prompt：要求模型给出
   「端口可用 + 相位有余量」的 pin，而不是只满足依赖下界。
4. CDD 细化：用 `SIMULATOR=1` 的 `kII/PC/x/y/OP` 定位到具体 PE，把责任分到节点或边。
5. 把 fft 的 mapper 崩溃做成已知失败样本，交给工具链 owner；或换一个可跑的 67 节点级 kernel。
