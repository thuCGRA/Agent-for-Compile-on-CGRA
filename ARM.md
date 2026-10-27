# 臂 A：LLM 布局 + PathFinder 布线（M1）

状态：**可运行**。`array_add` 端到端跑通，II=4、Mismatches::0；坏 pin 会被本地校验拦下并
带着错误重问；同 seed 两次运行的 pins/report/bitstream 哈希完全一致。

## 1. 怎么跑

环境已就绪（cgra / cgra-tc 两个 conda 环境、预编译 mapper 与 simulator）。仓库是 src 布局，
未安装成包，所以用包装脚本或显式 `PYTHONPATH`：

```bash
cd /home/steve/cac-armA

# 造一份坏 pin：把 node 0 挪出 4x4 网格，用来演示「报错 -> 重问 -> 成功」
python - <<'PY'
import json
d = json.load(open('/home/steve/llm-lg-demo/pins_m0.json'))
for p in d['pins']:
    if p['node'] == 0:
        p['x'] = 9
json.dump(d, open('/tmp/bad_pins.json', 'w'))
PY

# 起离线 mock（OpenAI 兼容，回放固定 pins；可给多个文件，最后一个重复）
python scripts/mock_replay.py --port 18431 \
    --pins /tmp/bad_pins.json --pins /home/steve/llm-lg-demo/pins_m0.json &

# 端到端
PYTHONPATH=src python -m cgra_agent.cli run --arm A --kernel array_add
# 或者
scripts/run.sh run --arm A --kernel array_add
```

接真模型时只换三个环境变量，代码不动：

```bash
export CGRA_LLM_BASE_URL=https://api.example.com/v1
export CGRA_LLM_API_KEY=sk-...
export CGRA_LLM_MODEL=deepseek-chat
PYTHONPATH=src python -m cgra_agent.cli check        # 探活
PYTHONPATH=src python -m cgra_agent.cli run --arm A --kernel array_add --seed 7
```

其它入口：

```bash
# 不做布局，直接回放一份 pins（调试/对照）
PYTHONPATH=src python -m cgra_agent.cli run --arm A --kernel array_add \
    --pins-file /home/steve/llm-lg-demo/pins_m0.json
# 只跑 LLVM 段，打印 facts
PYTHONPATH=src python -m cgra_agent.cli facts --kernel array_add --work work/facts
# 单元测试（不需要工具链）
python -m pytest -q
```

退出码：0 = 映射且仿真通过；2 = 映射成功但没有 trace（未验证）；1 = 失败。

每次运行落在 `work/armA/array_add/<时间戳>/`（work/ 已 gitignore）：

```
config.yaml     本次生效的配置（含 seed、预算）
facts.json      解析后的节点/边/容量/bank 信息（LLM 看到的同一份数据）
pins.json       最终提交给 mapper 的 pin 集（含 reasoning、pins_mode）
report.json     mapper 的 MapReport
sim.txt         仿真输出（首行是汇总）
bitstream.bin   mapper 生成的 bitstream
llm_calls.jsonl 每次 LLM 请求/响应的完整记录
attempts        在 run.json 里：每次尝试的 validate/map/sim 证据
run.json        全量记录（状态、II、耗时、哈希、CDD）
run.log         人类可读的阶段日志
```

## 2. 结果

`array_add`，4x4，II=4：

| 路径 | II | lat | Matches | Mismatches | cycles | LLM 调用 |
|---|---|---|---|---|---|---|
| smoke 基线（method 0，无 pin） | 4 | 11 | 243 | 0 | 134 | - |
| A 臂回放已知 pins | 4 | 11 | 243 | 0 | 133 | 0 |
| A 臂 + mock（先坏 pin 后好 pin） | 4 | 11 | 243 | 0 | 133 | 2 |

* 坏 pin 恢复：第 1 次回答把 node 0 放到 `x=9`，`validate` 报
  `node 0 at (9,2): outside the 4x4 grid`，错误原文回给模型；第 2 次回答通过，
  映射+仿真成功。`run.json` 的 `attempts[0].validate.errors` 保留了这条证据。
* 可复现：同一份 pins 连跑两次，`binary_sha256=ab64645e…`、`pins.json` 与
  `report.json` 的 sha256 均相同（见 `work/armA/array_add/20261027-152850/` 与 `…152852/`）。
* mapper 1.0–1.5s，DFG 0.1–0.5s，整条链约 5s。
* `python -m pytest -q`：15 passed。

## 3. 管线与设计决策

`pipeline.py` 实现 S0–S9 的最小闭环：

```
init -> dfg -> facts -> [place -> validate -> map -> sim] -> report
                            ^          |        |      |
                            +----------+--------+------+  带证据重问（有界）
```

* **LLVM 段不是搜索变量**：`tools/dfg.py` 里 clang/opt/DFG pass/update_mem_alloc 的命令行是
  字面量，只有 kernel 配置和 vendor 源码是输入。
* **LLM 只产布局**：契约是 `{"pins":[{"node","x","y","t"}],"reasoning","ii"}`；
  `tools/validate.py` 先做便宜的合法性检查（R1 一 PE 一相位、R2 op 支持、R4 边界、
  R5 bank、常量 27bit、相位容量），通过后才付 mapper 的钱。
* **R3/R6 只告警**：已知好 pins 本身就违反「hop 数 ≤ 相位差」（mapper 用 lat=11 > II=4
  的方式重定时），也违反「SELECT 两父相位不同」。把它们当硬规则会拒绝正确解，
  所以只作为风险提示传给模型。
* **pins_mode 选择**：模型给全了节点 → `strict(1)`，少给 → `repair(3)`（交给 mapper 补），
  这样部分布局也能落地。
* **失败分流**：schema 错 ≤2 次、validate 错 ≤2 次、mapper 错 ≤2 次、sim 失配 ≤1 次；
  同一 II 反复失败就 II+1（≤max_ii），总预算 `max_llm_calls=12`、`max_rounds=4`、
  `kernel_walltime_s=1200`，每次工具调用都按剩余时间截断。
* **II 起点**：`configs/array_add.yaml` 的 `init_ii` 改为 4（mapper 的 RecMin II 也正是 4），
  避免从 II=1 开始白白烧调用；facts 出来后还会用 `min_ii` 兜底。
* **bundle 自证**：pins/report/bitstream 都记 sha256，`run.json` 里能追到每次尝试的
  validate 错误、mapper stage、仿真证据。

## 4. 已知限制

1. **trace 生成还没接**。`tools/sim.py` 只找现成的 `*_trace_*.txt`（当前复用
   `toolchain/vendor/simulator/test/library/array_add_trace_0.txt`）。没有 trace 的 kernel
   会以 `status=unverified` 退出，不谎报通过。gemm 因此还没跑（也还没有 `configs/gemm.yaml`）。
2. **R5 只是建议**。`update_mem_alloc.py` 生成的 arch 没有 `CGRA.CONNECTIONS`，推不出
   PE→bank；mapper 本身也不按 bank 限制放置，最终裁判是仿真。
3. **仿真失败归因很粗**。只给「相位错」这一条 CDD，没有把失配地址映射回具体节点/边。
4. **mock 不是模型**。它证明的是管线与契约，不证明 prompt 的布局质量；真模型需要另做评测
   （II、lat、调用数、成功率）。
5. `max_rounds=4` 与重试预算会互相挤压：极端情况下重试没走完就被轮次上限截断。
6. 未安装成包时 `python -m cgra_agent.cli` 需要 `PYTHONPATH=src`（`pyproject.toml` 已备好，
   `pip install -e .` 后可用短命令）。

## 5. 下一步

1. 接 trace 生成（插桩 → 编译 → 运行 → 抽 memory trace），把 gemm 纳入；补 `configs/gemm.yaml`。
2. 真模型联调：同一 kernel 跑 5 个 seed，统计首次合法率、平均调用数、II 分布；据此改 prompt。
3. 允许模型只给关键节点（memory/长边），其余交给 `repair`，降低「全量 pin 一个错就重来」的代价。
4. CDD 细化：用 `SIMULATOR=1` 的 `kII/PC/x/y/OP` 定位到具体 PE，再把责任分到节点或边上。
5. II 搜索策略：从 `max(RecMinII, ResMII)` 起，失败时按 2 的步长升，而不是逐 1。
