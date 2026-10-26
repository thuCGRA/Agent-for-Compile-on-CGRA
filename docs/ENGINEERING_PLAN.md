# CGRA Agent Compiler 工程与环境规划

版本 v0.3 · 2026-10-26 · 替代 v0.2（Docker 版）

> 本版根据三条新决策重写：**工程跑在 WSL 原生环境**、**Python 用 conda + LangGraph/deepagents**、**LLVM 自己装、源码自己 vendor**。Morpher 从"运行依赖"降级为"对照物 + 源码来源"。

---

## 0. 本版变更一览

| 维度 | v0.2（Docker 版） | v0.3（本版） | 原因 |
|---|---|---|---|
| 运行位置 | 工具链进镜像，`docker run` 调用 | 全部在 WSL 原生 | 单用户、调试频繁，容器边际收益低 |
| Python 环境 | `uv` + venv | **conda 双环境**（`cgra` / `cgra-tc`） | 用户习惯；LLVM 10 也能用 conda 装 |
| LLVM-10 | 从 `morpher_dev` 镜像继承 | **conda-forge 10.0.1 自装** | 与 Morpher 工程解耦 |
| 编排 | 自研 500 行状态机 | **LangGraph `StateGraph`** + deepagents | 避免重复造轮，保留可控性 |
| 源码 | 快照 + 容器内重编 | **离线 `git archive` vendor + patch** | GitHub 不可达，且要能独立重建 |
| Morpher 仓库 | 工具链基底 | **仅对照物/源码来源**，运行时零依赖 | 用户要求 |

概念架构（五层、七个角色、三个闭环）不变，变的是 §11 工具层与 §12 技术选型的落地方式。

---

## 1. 三条决策的判断

### 1.1 放回 WSL：赞成

理由不是"省事"，而是这个项目的调试密度：

- 一次实验里 mapper 要被调几十次，每次失败都要看 stderr、看 `result.txt`、看 jobdir。容器把这些都推到 `docker exec` / 挂载目录后面，排查成本乘以调用次数。
- 5 GB 内存、8 核的机器上，容器自身的开销和 `--memory` 限制会直接压缩 mapper 的可用内存（PathFinder 的队列很吃内存）。
- 我们要 profile 的是**编译器**，不是容器；容器只会给性能数字引入噪声。

**但 v0.2 里有一条原则必须保留**：工具链要"不可变"。在 conda 方案里，由 **环境锁定（explicit spec + 包 sha256）+ conda-pack 归档 + 二进制 sha256 记录**来承担这个角色。这不是把容器换了个名字——它没有命名空间隔离，所以更要靠"只读约定 + 不原地改 vendor"来保证。

### 1.2 LLVM 自己装：可行，但只有一个现实选项

实测结论（2026-10-26）：

| 检查 | 结果 |
|---|---|
| conda-forge `clang` / `llvmdev` / `llvm-tools` | **有 10.0.1**（9.0.x、11.x 也有） |
| `github.com` | **不可达**（`000`），release 资产同样拿不到 |
| `pypi.org`、`conda.anaconda.org` | 可达（200） |
| 宿主 | 8 核 / 5 GB 内存 / 874 GB 可用 |

所以：

- **不能**从源码编 LLVM 10：没有 GitHub，5 GB 内存也扛不住（链接阶段峰值通常 >8 GB）。
- **不能**用 apt：Ubuntu 24.04 只有 clang/llvm 14–19，没有 10。
- **只能**用 conda-forge 的 10.0.1 预编译包。这反而是最好的结果——和你用 conda 的偏好一致，且 `llvmdev` 自带 `LLVMConfig.cmake`，`find_package(LLVM REQUIRED CONFIG)` 可以直接工作。

**风险点是 ABI**：他们的 `.so` 是对着 Ubuntu 18.04 的 llvm-10（10.0.0，GCC 7 构建）编的；conda-forge 的 10.0.1 用另一套 `libstdc++` 构建。LLVM 的 C++ ABI 不稳，混用会在 `opt -load` 时报未定义符号。对策是**两边一起换**：pass 用 conda 的 `clang++` 重编，`opt` 也用 conda 的，绝不加载旧 `.so`。M0 的第一项验收就是这件事。

万一 conda 版真的不行，退路按优先级：① 用 `clang++` 编 pass、`g++` 编 mapper（两套 ABI 互不相干，但 pass 必须与 `opt` 同源）；② 从 `morpher_dev` 镜像里 `docker cp /usr/lib/llvm-10` 出来一次性落到 `toolchain/llvm10/`（离线可用，但等于承认依赖他们的构建）；③ 放弃 LLVM 10，把 pass 移植到 18（工作量大，只在 ①② 都失败时考虑）。

### 1.3 LangGraph + deepagents：赞成，但要划清"谁能做决定"

- **LangGraph 负责控制流**：节点、条件边、并行、检查点、中断。这些恰好是我们最需要被审计的部分，而且它把控制流显式画在图上——比藏在 `while` 里更好审。
- **deepagents 负责"策略性"的子任务**：A5 的逐 II 规划（自带 todo 列表）、A6 的路由诊断（可派生一个只读的 route-analyst 子 agent）。它的 `task` 子 agent 机制正好对应"给诊断员一个只读的、上下文干净的助手"。
- **硬门永远不是 agent**：`validate_pins`、`run_map`、`run_sim` 是普通 Python 函数，由图的节点直接调用。deepagents 拿不到"跳过校验"的工具，也就没有幻觉空间。

一句话：**LangGraph 是骨架，deepagents 是肌肉，校验器是关节**。三者不能互换。

### 1.4 Morpher 的定位

| 资产 | 用途 | 运行时依赖 |
|---|---|---|
| `~/morpher-src`（改过的三件套） | **源码来源**：vendor 进 `toolchain/vendor/`，patch 化 | 否 |
| `morpher_dev` 镜像 | 可选 oracle：跑他们的二进制做对照 | 否（仅在对照实验时） |
| `morpher_tutorial` / 上游 git 对象 | 基线方法（method 0/1/2）与"原版行为"参照 | 否 |

我们没有找到名为 `morpher_tutorial` 的目录，只有 `~/morpher-src`、`~/morpher-THU`。**待确认**它是不是一个镜像 tag。若只是镜像，用 `docker save` 导出一次即可归档；若不存在，上游原版可以从本地 submodule 的 git 对象里 `git archive` 出来（见 §6），不需要联网。

---

## 2. 两个 conda 环境

### 2.1 为什么是两个

| 环境 | 内容 | 变更频率 | 角色 |
|---|---|---|---|
| `cgra-tc` | clang/llvm 10.0.1、cmake、make | **几乎不变**（LLVM 10 已 EOL） | 不可变工具链 |
| `cgra` | Python 3.12、LangGraph、deepagents、pydantic | 每天变 | 应用 |

合成一个是省事，但每次 `conda install` 都可能把 `libLLVM` 拖着升/降级，某天 pass 就加载失败了，而且很难查。分开之后，`cgra-tc` 只在 M0 建一次并归档，`cgra` 随便折腾。

### 2.2 `toolchain/environment.yml`

```yaml
name: cgra-tc
channels:
  - conda-forge
dependencies:
  - clang=10.0.1
  - llvmdev=10.0.1        # 头文件 + LLVMConfig.cmake + llvm-config
  - llvm-tools=10.0.1     # opt / llvm-link / llc / llvm-dis
  - cmake>=3.20
  - make
  - libstdcxx-ng>=13      # 与宿主一致，避免两套 libstdc++
```

锁定与归档：

```bash
conda env create -f toolchain/environment.yml
conda list -n cgra-tc --explicit > toolchain/conda-explicit.txt   # 带 URL + sha256
conda pack -n cgra-tc -o toolchain/cgra-tc.tar.gz                 # 等价于 docker save
```

`--explicit` 文件是重建的唯一依据，`environment.yml` 只是人读的意图声明。

### 2.3 `environment.yml`（应用）

```yaml
name: cgra
channels:
  - conda-forge
dependencies:
  - python=3.12
  - pip
  - pip:
      - -r requirements.txt
```

`requirements.txt` 只写直接依赖并锁死版本（`langgraph==x.y.z`、`deepagents==a.b.c`、`langchain-core==…`），`pip freeze > requirements.lock` 记录全量。deepagents 迭代很快，**版本必须精确到 patch**，升级走单独 PR + 回归。

---

## 3. 目录结构

```
CGRA_Agent_Compiler/
├── README.md
├── Makefile                  # doctor / env / toolchain / fig / test / agent
├── environment.yml           # cgra 环境
├── requirements.txt
├── requirements.lock
├── configs/
│   ├── arch/hycube_4x4.yaml  # 4x4 / 4 banks：目标架构
│   ├── arch/hycube_8x8.yaml  # 8x8 / 8 banks：上限实验
│   └── space/                # A3 设计点空间（M3 启用）
├── toolchain/
│   ├── README.md             # 重建步骤与已知坑
│   ├── environment.yml       # cgra-tc
│   ├── conda-explicit.txt
│   ├── vendor/               # 三件套源码快照（只读）
│   │   ├── VENDOR.md         # 上游 URL、submodule SHA、快照日期
│   │   ├── dfggen/
│   │   ├── mapper/
│   │   └── simulator/
│   ├── patches/              # 0001-*.patch … 按 §7 编号
│   ├── build.sh              # cmake + make，产物进 out/
│   └── out/                  # 二进制（不进 git）
├── scripts/
│   ├── vendor_morpher.sh     # 从本地 git 生成快照 + patch（离线）
│   └── check_vendor.sh       # git apply --check 防漂移
├── src/cgra_agent/
│   ├── contract/             # A1：KernelSpec / ArchSpec / Budget
│   ├── plans/                # A2：MappingPlan
│   ├── facts/                # DFG/ADL 解析、事实渲染
│   ├── checks/               # 确定性校验（R1–R7、预算、一致性）
│   ├── tools/                # 五个可执行文件的薄封装（本地子进程）
│   ├── runtime/              # LangGraph 图、状态、checkpointer、预算守卫
│   ├── agents/               # A1/A4/A5/A6/A7 的 prompt 与 deepagents 装配
│   ├── memory/               # SQLite + FTS5 + 证据库
│   └── reports/              # Markdown / JSON / 图
├── benchmarks/               # 6 个内核 + 期望输出（金标准）
├── bench/oracle/             # 可选：调 morpher_dev 做对照
├── runs/                     # 实验产物（gitignored）
└── tests/                    # pytest；golden DFG / report / 图
```

依赖方向仍然是 `contract → facts → checks → tools → runtime → agents`，`agents` 只能向下调，不允许反向 import。

---

## 4. 接口契约（本地版）

v0.2 的"一个 jobdir + 一次 `docker run`"退化成"一个 jobdir + 一次 `subprocess`"，契约本身不变：

```
runs/<run_id>/kernels/<k>/ii<II>/
  in/   dfg.xml  arch.json  pins.json
  out/  result.txt  mapping.json  map_report.json  fail_report.json  *.bin
```

```python
subprocess.run(
    [TOOLCHAIN / "bin" / "cgra_xml_mapper", "-d", dfg, "-x", "4", "-y", "4",
     "-j", arch, "-i", str(ii), "-t", "HyCUBE_4REG", "-m", "3",
     "--pins", pins, "--pins_mode", "1", "--report", report],
    cwd=jobdir, env=BASE_ENV, timeout=1800, capture_output=True,
)
```

- `cwd=jobdir` 解决它"把产物写在 cwd"的耦合，不需要改 C++；
- `BASE_ENV` 显式设置 `OMP_NUM_THREADS=1`、`PATH` 只含 `cgra-tc/bin`；
- 退出码沿用 `0/10/11/12/13/20/21/30`（成功/契约/DFG/映射/仿真/超时/工具缺失/未知）；
- 超时按进程组杀：`start_new_session=True` + `os.killpg`。

**并发**：map 2、sim 2（5 GB 内存下的实测上限，M1 校准）。每个 jobdir 独立，无共享 cwd。

---

## 5. LangGraph 编排

### 5.1 状态

```python
class State(TypedDict):
    run_id: str
    kernel: KernelRef
    arch: ArchRef
    contract: Contract | None
    plan: MappingPlan | None          # 当前假设
    attempt: Attempt | None           # 当前这轮的执行结果
    best: Attempt | None
    history: Annotated[list[Event], add]
    budget: Budget
    error: str | None
```

### 5.2 节点与边

```
START → n0_intake → n1_contract → n2_preflight → n3_apply → n4_feasible
                                                        │
                        ┌───────────────────────────────┘
                        ▼
                     n5_design ──Send(K)──► n6_map ──ok──► n8_sim ──pass──► n10_judge
                        ▲                     │ fail(place)          │ fail
                        │                     ▼                      ▼
                        │                  n7_place_fix ──► n6     n9_diagnose
                        │                                            │
                        └────────────────────────────────────────────┘
                     n10_judge ──accept/stop──► n12_report
                          └──improve──► n5_design
```

| 节点 | 职责 | LLM |
|---|---|---|
| `n0_intake` | 建 run、写 meta、解析 CLI | 否 |
| `n1_contract` | A1 契约（只跑一次） | 是 |
| `n2_preflight` | 预算/路径/工具版本 | 否 |
| `n3_apply` | A2 应用计划 | 是 |
| `n4_feasible` | 可行性门（MII / bank / 面积） | 否 |
| `n5_design` | A4 策略 + A5 放置，产出 K 个假设 | 是 |
| `n6_map` | `run_map`，读 `map_report.json` | 否 |
| `n7_place_fix` | 按逐 pin 拒绝原因修 pin | 是 |
| `n8_sim` | `run_sim` | 否 |
| `n9_diagnose` | A6 归因，写 `Diagnosis` | 是 |
| `n10_judge` | A7 判优（成对比较 + 预算） | 是 |
| `n11_decide` | 纯函数路由：accept / improve / stop | 否 |
| `n12_report` | 证据包 + 图 + Markdown | 否 |

三个闭环落到三条条件边：`n6→n7`（内环，pin 修复）、`n4→n5`（中环，II/契约冲突）、`n10→n5`（外环，设计点）。

### 5.3 并行：`Send` 扇出

同一 II 下采样 K 个放置假设，并行 map，`n10` 收齐后判优。这是相对 MACO"单候选串行"的直接改进，也是 LangGraph 带来的实际收益：

```python
def fan_out(state):
    return [Send("n6_map", {**state, "plan": h}) for h in state["plan"].hypotheses]
```

K 默认 3，内存允许时 4；并行度由 `max_concurrency=2` 控制。

### 5.4 持久化、回放、HITL

```python
checkpointer = SqliteSaver.from_conn_string("runs/checkpoints.sqlite")
app = graph.compile(checkpointer=checkpointer)
app.invoke(init, config={"configurable": {"thread_id": run_id}})
```

- 崩溃后 `invoke(None, config)` 从最后检查点续跑；
- 论文要的"某次失败是怎么一步步走到的"直接查 `history` + 检查点，不需要日志考古；
- HITL：M3 起在 `n3_apply` 前插 `interrupt()`，人工确认后再继续。

### 5.5 deepagents 的边界

```python
strategist = create_deep_agent(
    model=model,
    tools=[read_facts, validate_pins, dry_run_map],   # 只读/试算
    system_prompt=STRATEGIST_PROMPT,
    subagents=[route_analyst],                        # 只读诊断助手
    backend=FilesystemBackend(root_dir=jobdir, virtual_mode=True),
)
```

- 只给三个工具，不给 `run_map`（真映射由节点做）、不给 `run_sim`、不给写 jobdir 之外的能力；
- `write_todos` 让它自己拆步骤，正好对应 A5 的逐 II 规划；
- `route_analyst` 是 `task` 子 agent，只读 `fail_report.json` + facts，返回失败边的归因假设；
- 输出仍走 Pydantic schema 校验，**agent 的输出不是结果，只是下一轮的输入**。

### 5.6 预算守卫

`n11_decide` 是纯函数，先看预算再看判优结论：

```python
if state["budget"].exhausted(): return "n12_report"
if state["best"].lat <= target: return "n12_report"
return "n5_design"
```

LLM 调用次数、token、墙钟时间三个维度都在 `Budget` 里，任一触顶即收尾并写明原因——与 A7 的判优分离，避免"模型觉得自己还能再试"。

---

## 6. vendor 与离线重建

GitHub 不可达，但**三件套的 git 对象都在本地**，所以快照和 patch 完全可以离线生成：

```bash
# 纯净快照（上游 commit，不含本地改动）
git -C ~/morpher-src/Morpher_DFG_Generator archive HEAD | tar -x -C toolchain/vendor/dfggen

# 本地改动 → patch（含未跟踪文件，先 add -N 让 diff 看见）
git -C ~/morpher-src/Morpher_CGRA_Mapper add -N .
git -C ~/morpher-src/Morpher_CGRA_Mapper diff HEAD > toolchain/patches/0001-mapper-method3.patch
```

`VENDOR.md` 记录：上游 URL、submodule 的 `HEAD` SHA、快照日期、每个 patch 的一句话说明。CI 里 `git apply --check` 保证 patch 与快照一致（防手改 vendor）。

这样"我们的工程" = vendor 快照 + 4 个 patch + 构建脚本，**不含 Morpher 仓库本身**，也不需要网络。

> 注意：`Morpher_DFG_Generator` 的 pass 源码是**零改动**的，patch 只涉及 mapper（54 行改 + 2 个新文件）与 simulator（9 行）。这让"基线可复现"这件事变得很便宜：`-m 0` 就是原版。

---

## 7. 对 Morpher 的 C++ 增强（P1–P4，不变）

| 编号 | 改动 | 为什么必须先做 |
|---|---|---|
| P1 | `PathFinderMapper::Map()` 三处 `return false` 前记录失败节点/边 | 没有它，路由失败只能得到 `stage=route` |
| P2 | `report` 结构化：`fail{class,node,op,ii,candidate_count,congestion[]}`；修 `pins_mode=repair` 输出；`stage` 细分 | CDD 的证据来源 |
| P3 | 逐 pin 拒绝明细 `{node, requested, reason, offered_range}` | 内环修复的依据 |
| P4 | `--ii-list` 显式多 II，失败不 `break`，报告 `ii_tried[]` | 中环一次调用顶多次 |

构建方式从"镜像内重编"改为：

```bash
cmake -S toolchain/vendor/dfggen -B build/dfggen \
      -DLLVM_DIR=$CONDA_PREFIX/lib/cmake/llvm -DCMAKE_BUILD_TYPE=Release
```

（`$CONDA_PREFIX` 指向 `cgra-tc`。）

---

## 8. 边界

**在边界内**：三件套源码、patch、构建脚本、四个 Python 包、6 个内核、对照实验脚本。

**在边界外（v1 明确不做）**：

- 不改 Morpher 的 DFG 语义（pass 零改动是硬约束，否则基线不可比）；
- 不追求 mappers 的通用化（method 3 只对 HyCUBE 形状有效，8×8 复用同一实现）；
- 不做 ADL 自动扩展（A3 只在模板 + 合法 patch 内搜）；
- 不做多机并发、不做 Web UI；
- 不依赖任何在线服务（除 LLM API）。

**与 Morpher 仓库的关系**：单向。我们从它取源码（一次），之后它怎么变都与我们无关；对照实验用它的镜像，属于可选路径。

---

## 9. 里程碑

### M0（2–3 天，硬 gate）

1. `conda env create -f toolchain/environment.yml`，确认 `clang --version` = 10.0.1、`llvm-config --version` = 10.0.1；
2. `toolchain/build.sh` 编出 `libdfggenPass.so`、`cgra_xml_mapper`、`hycube_simulator`；`opt -load ... -help` 能列出我们 pass 的参数（ABI 通过）；
3. **DFG 对拍**：`array_add` 用我们的 pass 生成的 XML 与现网参考 XML 一致（节点数、边数、op 直方图）；不一致就查 LLVM 版本差异；
4. 端到端：dfg → map(method 0) → sim，II 与 cycles 与参考一致，`mismatches=0`；
5. 连跑两次结果一致；记录三个二进制的 sha256；
6. `conda pack` 归档 `cgra-tc`，`cgra doctor` 一条命令覆盖以上检查。

**M0 不过，后面所有 agent 效果都无法归因。**

### M1（1 周）

LangGraph 骨架跑通（n0–n12），A5 只做"单 II + 3 假设"，P1/P2 patch 合入，`run_map` 能返回失败节点；`cgra-agent compile` 在 `array_add` 上产出第一个 CDD。

### M2（2 周）

内环 + 中环闭环；6 个内核 × {A-greedy, PathFinder, SA, LISA, LLM} 基线表；deepagents 的 A5/A6 上线；缓存与预算守卫。

### M3（2–3 周）

外环（A3 设计点）+ HITL；8×8 扩展；消融（去 A6 反馈、去 A7 判优、去并行）。

### M4（1 周）

复现包：`conda-pack` + `runs/` 精选 + 一键脚本 + 论文图。

---

## 10. 风险

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| conda LLVM 10 ABI 不兼容 | 中 | M0 阻塞 | 用 conda 的 clang++ 编 pass；退路见 §1.2 |
| GitHub 不可达，拿不到上游 | 已发生 | vendor 只能靠本地对象 | `git archive` 离线生成；`VENDOR.md` 记录 SHA |
| deepagents API 漂移 | 高 | 装配层返工 | 版本钉死；`agents/` 做适配层，图不直接 import |
| 5 GB 内存不够并行 | 中 | 并发降到 1 | M1 实测；`max_concurrency` 可配 |
| 旧 `.so` 误加载 | 低 | 静默错误结果 | `doctor` 校验 sha256 + `opt -load` 冒烟 |
| `morpher_tutorial` 不存在 | 中 | 基线对照少一个锚点 | 用本地 git 对象的原始 commit 代替 |

---

## 11. 待确认

1. `morpher_tutorial` 是目录还是镜像 tag？如果是镜像，是否现在 `docker save` 归档？
2. 首批 6 个内核是否就定 `array_add / mac / fir / gemm / hconv / trmm`？
3. LLM 用哪家 API、哪个模型？决定 deepagents 的 `model` 与预算上限。
4. 是否需要我把 M0 的 `toolchain/` 骨架（yml、build.sh、vendor 脚本）现在就搭出来？
