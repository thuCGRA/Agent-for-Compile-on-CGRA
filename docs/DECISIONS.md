# 关键决策记录（ADR）

本文件记录架构中“看起来可以那样、但我们选择这样”的地方。每条给出背景、备选、决定、理由、代价。后续若推翻，追加新 ADR，不修改历史。

---

## ADR-001：把 agent 放进编译循环，而不是包在工具链外面

**背景**：MACO/MALTA、Beacon 都在设计点层工作：agent 选参数，工具链整体作为评估器。这套做法成熟、易实现，但把编译器当成黑盒。

**备选**：

1. 设计点级：agent 只改 unroll / tile / banks / dataflow，映射交给工具。
2. 编译级：agent 进入 DFG → 调度 → 放置 → 路由，直接操作编译决策。

**决定**：选 2，但限定在**放置**这一层；调度、路由仍由 Morpher 完成。

**理由**：

- Morpher 已经暴露了 `mapping_method=3` 与 `pins`，这是现成的、低成本的入口；不需要重写编译器。
- 放置是“全局结构先验”最能发挥作用的层：recurrence 环、长链、bank 约束都是图结构问题，适合 LLM；而路由是局部组合优化，PathFinder 更强。
- 设计点级的工作已经很多，继续做只能拼 prompt 与模型；编译级动作空间是新的。

**代价**：

- 需要处理 II 扫描（method 3 不扫 II），预算更紧。
- 需要更细的失败诊断（placement vs route），否则 pin 级修复无从下手。
- 评估更复杂：要证明“不是 portfolio 的运气”。

**缓解**：以 B1（最强启发式 portfolio）为主基线；LLM 只在启发式失败或 II 有 gap 时启用；所有增益做配对比较。

---

## ADR-002：七个 agent，而不是三个或十二个

**背景**：MACO 四个阶段，Beacon 三个 agent。角色太少会让一个 agent 承担多种失败模式；太多会让协调成本超过收益。

**决定**：七个 LLM 角色（契约、应用、架构、策略、放置、诊断、判优）+ 一个离线 Curator + 一个确定性 Conductor。

**拆分标准**：存在独立的失败签名，且修复需要不同的工具与不同的输出 schema。按此：

- 契约：目标不可测，与其他都不同；
- 应用：构建期失败（op、指针、规模）；
- 架构：ADL/容量/op 覆盖；
- 策略：选 mapper/II，不改图也不改 pin；
- 放置：pin 级几何约束；
- 诊断：从证据到根因，不直接改；
- 判优：比较与合规，不改任何东西。

**被合并的**：

- “路由修复”并入放置：工具相同（map + validate），输出都是 pins；单独拆会重复上下文。
- “性能优化”并入判优 + 策略：没有独立的工具集。
- “记忆检索”并入 Curator：它是离线索引维护，不在关键路径。

**代价**：七个角色意味着七套 prompt 与七套 schema，维护成本高。

**缓解**：共用 `AgentBase`（工具白名单、JSON 校验、重试、证据检查）；v1 可以把应用/架构合并成一个“设计 agent”，等失败码分布稳定后再拆。

---

## ADR-003：黑板式协作，而不是对话式协作

**背景**：多 agent 系统常见的做法是让 agent 互相发消息、讨论、投票。这对开放域任务是必要的，对编译任务未必。

**决定**：agent 不直接对话；共享 `CampaignState`，只读写类型化 artifact；Conductor 注入最小切片。

**理由**：

- 编译器已经提供了结构化状态；把它压成自然语言再解析，是信息损失。
- 对话历史随轮次增长，token 成本不可控，且难以复现“为什么这么决定”。
- artifact 有 hash，可缓存、可去重、可审计；对话没有这些性质。

**代价**：灵活性下降。某些跨阶段的问题（例如“这个失败到底是应用还是架构造成的”）需要 Conductor 显式路由，不能靠 agent 自由讨论。

**缓解**：CDD 是跨阶段的共享语言；A6 可以在 CDD 里给出多个候选根因与置信度，由 Judge/Conductor 决定路由。

---

## ADR-004：Conductor 是确定性代码，不是 LLM

**背景**：让 LLM 做“项目经理”很自然，也很容易做出 demo；但预算、并发、重试、复现都需要精确控制。

**决定**：Conductor 是普通 Python；LLM 只输出 `DesignMove` / `Verdict` / `CDD`，不能直接改变状态机。

**理由**：

- 预算是硬约束，不能由模型“觉得够了”决定。
- 状态转移条件必须可测试；LLM 的判断无法写单元测试。
- 论文需要复现：同一事件日志应重放出同一决策序列。

**代价**：遇到真正模糊的分支（例如两个根因都合理），需要额外的一次 policy 调用；不如“让经理拍板”灵活。

**缓解**：保留 `policy` 钩子，输入是候选动作与预算，输出是选择；它仍然受 schema 与预算约束。

---

## ADR-005：软锚点，而不是全量硬 pin

**背景**：`pins_mode=1` 要求所有 pin 被满足，否则失败。直觉上“LLM 全盘安排”最强大。

**决定**：默认 `pins_mode=2`（prefer）+ 少量锚点；`strict` 只用于小 DFG、高置信、需要精确复现的场景；`repair` 作为兜底。

**理由**：

- 全量 pin 把路由自由度压到零；PathFinder 的迭代消解、Dijkstra 路由都用不上，结果往往更差。
- Pin 越多，LLM 出错的概率越高；一个非法 pin 就会让整次映射失败。
- 锚点选在 recurrence SCC、最长链、高扇出、memory 节点，正好是“结构先验”最有价值的地方。

**代价**：`prefer` 模式下，mapper 可能忽略锚点，LLM 的意图不能 100% 实现；需要 report 里的 `pins_applied/rejected` 来判断。

**缓解**：把 `pins_applied/rejected` 作为反馈；若关键锚点被拒绝，下一轮改 `strict` 或换位置。

---

## ADR-006：CDD 是封闭枚举 + 证据引用，不是自由文本诊断

**背景**：让 LLM 写一段“失败分析”很自然，但无法统计、无法检索、无法做混淆矩阵。

**决定**：CDD 的 `root_cause` 是封闭集合；每条 evidence 必须指向 artifact 字段；推荐动作必须来自合法移动集。

**理由**：

- 封闭集合让失败可以聚合：哪种根因最常见、哪个 agent 最常误判。
- 证据引用让“幻觉诊断”可被自动拒绝。
- 与合法移动集对齐，保证诊断能落地成动作。

**代价**：遇到集合外的新失败，只能标 `unknown`，信息量下降。

**缓解**：`unknown` 触发人工审阅；新根因通过 ADR 加入集合，并补对应的证据规则。

---

## ADR-007：v1 自研状态机，不直接上 LangGraph（已被 ADR-019 取代）

**背景**：仓库里已有 `graph.py`，用 LangGraph 表达三节点放置循环；团队也有 LangGraph 环境。

**决定**：v1 用普通 Python 状态机 + Pydantic + JSONL 事件日志；节点边界按 LangGraph 的 node 设计，未来可平移。

**理由**：

- 需要预算拒绝、并行 map worker、检查点、事件重放、硬门；这些在框架外更容易做。
- 调试时希望“一步一个函数”，而不是在框架的运行时里追踪。
- LangGraph 的 checkpointer 很好，但它不理解“工具预算”和“artifact hash”。

**代价**：少了现成的可视化与 HITL 支持；需要自己写 `replay` 与状态序列化。

**缓解**：状态机节点保持纯函数（`state -> state`），事件日志记录所有转移；需要 HITL 时，用 `request_human` 状态 + CLI 确认即可。

---

## ADR-008：先 portfolio，后 LLM；LLM 调用有硬门槛

**背景**：最容易犯的错误是“每个 kernel 都让 LLM 放置”，结果又慢又不稳定。

**决定**：每个 kernel 先跑确定性 portfolio（quick → PathFinder → LISA → SA → II 扫描）；只有满足 §7.2 的条件才启用 A5。

**理由**：

- Portfolio 是强基线；很多 kernel 在 II=MII 时启发式就能成功，LLM 没有边际价值。
- LLM 放置的成本（token + 映射重试）远高于一次 PathFinder；必须用在“启发式确实做不到”的地方。
- 等预算比较才有意义；否则只是“花更多钱买更好结果”。

**代价**：可能错过一些 LLM 能做好、但启发式也“刚好成功”的案例；增益被低估。

**缓解**：在消融中强制“LLM 优先”跑一组，量化机会成本；报告中同时给“启用门槛”的敏感性分析。

---

## ADR-009：正确性门不可协商

**背景**：映射成功（`ok=true`）常被当成编译成功；但 Morpher 的成功只表示没有资源超订，功能正确性要靠仿真。

**决定**：`verified` 只能由 Conductor 在 `mismatches==0` 后写入；未 verified 的候选不得进入 Pareto，不得出现在“best”中。

**理由**：

- 错误映射的 cycles 没有意义；把它排进前沿会污染搜索。
- LLM 容易把“mapper 说 ok”解释成“成功”，硬门可以消除这种歧义。
- 论文的可信度依赖于“所有报告的性能数字都经过功能验证”。

**代价**：每个候选都要仿真，成本上升；大 trace 时更明显。

**缓解**：初筛用 1 条 trace + quick mapper；只有进入候选池的才跑 4 条 trace。未验证的候选可以保留为诊断素材，但不参与排序。

---

## ADR-010：v1 不碰 RTL 与功耗模型

**背景**：MACO/MALTA 用 OpenCGRA + Design Compiler 做 PPA；这使“功耗/面积”成为一等目标。

**决定**：v1 只做 cycles / II / 编译时间；area/energy 用代理指标，报告中明确标注；RTL/综合作为接口预留。

**理由**：

- Morpher 没有 RTL 与功耗模型；硬接会引入大量不可控变量。
- 先把“映射正确性与质量”的闭环做扎实，再谈 PPA。
- 代理指标足以支撑“编译器侧”的论文；PPA 是下一篇或合作方的接口。

**代价**：与 MACO 的 PPA 结果不可直接比较；审稿人会问“功耗呢”。

**缓解**：在报告中给出 `area_proxy`（PE/RF/bank 计数）与 `energy_proxy`（op 数 × latency × 权重），并明确说“不是综合结果”；未来接 OpenCGRA/DC 时，`qor` 接口不变。

---

## ADR-011：环境分两层——工具链进镜像，我们的代码进 venv（已被 ADR-016/017 取代）

**背景**：Morpher 的 `Dockerfile` 基于 ubuntu:18.04 + LLVM-10 + Python 3.8；我们的 agent 栈需要 pydantic v2 / Python 3.12。C++ 侧可以改，但没有理由把两边焊死在同一个 rootfs 里。

**备选**：

1. 单容器：在 18.04 镜像里装 Python 3.12（或退回 3.8），所有代码进一个镜像。
2. 两层：`cgra/morpher-toolchain` 只放工具链；Python 侧用 WSL 24.04 + uv + 3.12，通过 `docker run` 调用。
3. 宿主直接跑 Morpher，不用容器。

**决定**：选 2。通信面只有挂载目录、退出码、JSON。

**理由**：

- 18.04 的 glibc 2.27 装不了 3.12 的 manylinux wheel；退回 3.8 又会被上游脚本绑死，等于把我们的迭代速度交给一个 EOL 发行版。
- 工具链是"一年改两次"的东西，agent 是"一天改十次"的东西，生命周期完全不同，不该共用一个镜像。
- 选 3 会把 LLVM-10 / gcc-7 / 32 位 multilib 的依赖污染宿主，且不可复现。

**代价**：每次调用多 ~0.3 s 的容器启动；路径要做一次映射（宿主绝对路径 → `/work` 相对路径）。在秒级的映射/仿真面前可忽略。

**缓解**：stage 级 entrypoint（一次容器调用跑完一个阶段，而不是一条命令一次），把启动开销摊薄。

---

## ADR-012：只以 CLI + 文件格式依赖 Morpher，不复用它的 Python

**背景**：仓库里有 `run_morpher.py`、`update_mem_alloc.py`、`tools/morpher_llm/`（Python 3.6）。

**决定**：一行都不 import。只调用 `cgra_xml_mapper`、`hycube_simulator`、`libdfggenPass.so` 与 `clang/opt/llvm-link-10`。

**理由**：

- `run_morpher.py` 用 `os.chdir` + 固定相对路径，无法并发、无法单阶段重试；这正是 agent 循环最需要的两件事。
- 它的 Python 3.6 与我们的 3.12 无法共存；一旦 import，就等于把 3.6 拖进我们的依赖图。
- CLI + 文件是**稳定接口**：上游改内部类不影响我们；我们换实现也不影响上游。

**代价**：要自己实现 DFG 解析、事实构建、报告解析（约 600 行），并对畸形 XML 做容错。

**缓解**：这些代码本来就要重写——旧 `tools/morpher_llm` 的 `facts.py` / `validate.py` 只能作为语义参考，其正则与规则已在本工程重新实现并配单测。

---

## ADR-013：源码快照入仓 + 有序 patch，而不是只存 diff

**背景**：当前 method 3 的实现存在于 `~/morpher-src` 的**未提交工作区**里（4 个文件 +54 行、两个新文件、simulator +9 行）。只保存 `git diff` 会丢掉新文件；只依赖镜像会丢掉可重建性。

**决定**：`toolchain/vendor/morpher/` 放三个 submodule 的**源码快照**（剔除 `build/`、生成数据、`.git`），`toolchain/patches/` 放我们对它的增量修改，`toolchain/build.sh` 按序 `git apply` 后重新编译。

**理由**：

- 快照保证"即使上游删库、即使本机磁盘坏掉，也能重建"。
- patch 保证"我们改了什么"是可 review、可 rebase 的一等公民，而不是散落在工作区。
- 重新编译是验证手段：如果重建后的二进制与现网 hash 不一致，说明构建环境有隐藏依赖——这正是 M0 要暴露的。

**代价**：仓库变大（源码约几十 MB）；上游升级时要手工 rebase patch。

**缓解**：`VENDOR.md` 记录三个 submodule 的 SHA、patch 顺序、二进制 hash；CI 用 `git apply --check` 防止 patch 与快照漂移。

---

## ADR-014：v1 锁定 HyCUBE 4×4，模型只走 API

**背景**：`thu_pea8x8.json` 已存在，但 ADL 表达力有限（shared GR、位置相关 route code、config package、多周期 EX 都表达不了）；本地部署模型会引入 GPU/显存/并发的一整套运维。

**决定**：v1 只有 HyCUBE 4×4 一个设计点；LLM 一律通过 OpenAI 兼容 API 调用，接口层做 provider 抽象，测试用 Mock。

**理由**：

- 4×4 的映射在秒级，agent 循环能在分钟级收敛；8×8 会把"设计问题"和"规模问题"混在一起。
- API 让"换模型"变成改配置；本地权重会让复现包体积与门槛都上一个量级。
- 单一架构先跑通闭环，架构泛化留到 M3 之后，避免第一版就背两个变量。

**代价**：审稿人会问泛化性；API 有成本和速率限制。

**缓解**：所有 prompt/响应落盘，`replay` 不调模型即可复现；第二个 ADL 作为 M3 的显式任务，接口不变。

---

## ADR-015：v1 不做 agent 容器（随 ADR-016 一并失效：现在全程不用容器）

**背景**：容器里调容器需要挂 `docker.sock`，路径映射、UID、权限都会变复杂。

**决定**：v1 的 conductor 跑在 WSL 宿主 venv 里，用 `uv.lock` 锁依赖；`Dockerfile.agent` 推迟到 M4 交付复现包时再写。

**理由**：可复现性的 90% 来自"锁死依赖 + 锁死工具链镜像 digest"，而这两点已经满足；剩下 10% 的收益不值得在 M0 就引入嵌套容器。

**代价**：宿主 Python 升级可能破坏环境（由 `uv` 与 `.python-version` 兜底）。

**缓解**：M4 用同一个 `pyproject.toml` 打 `cgra/agent`，代码零改动；CI 里两套都跑。

---

## 附：被明确否决的方案

| 方案 | 否决理由 |
|---|---|
| 让 LLM 直接生成 ADL JSON 连接 | 幻觉成本最高，lint 也难覆盖语义错误 |
| 让 LLM 生成 bitstream | 无结构、无法验证、无复用价值 |
| 用一个 agent 端到端 ReAct | 失败模式不隔离，上下文膨胀，难定位 |
| 用 LLM 替代 PathFinder 做路由 | 路由是局部组合优化，模型没有优势 |
| 用标量奖励做 RL | 样本效率低，且与“可解释诊断”的目标冲突 |
| 一开始就做 8×8 + 多 kernel 联合优化 | 闭环未验证，规模会掩盖设计问题 |
| 复用 `run_morpher.py` 作为驱动 | `os.chdir` + 固定路径 + Python 3.6，无法并发/单阶段重试 |
| 把 Python 装进 18.04 工具链镜像 | glibc 2.27 装不了 3.12，退回 3.8 又被上游绑死 |
| 在 `~/morpher-src` 原地开发 | 与上游工作区混在一起，改动不可追溯、不可重建 |
| 只存 patch 不存源码快照 | 需要联网 + 上游仍在，且新文件易丢 |

---

## ADR-016：工程跑在 WSL 原生环境，不放进容器

**背景**：v0.2 把工具链封成镜像。实际使用中，单用户、每天几十次编译调试，容器的收益（环境隔离）低于成本（`docker exec`、挂载路径、UID、内存上限）。

**决定**：主链路完全不进容器。工具链由 conda 环境 `cgra-tc` + `toolchain/out/` 二进制提供；应用由 conda 环境 `cgra` 提供。容器只保留一个可选用途：`bench/oracle/` 里跑 `morpher_dev` 做对照。

**理由**：

- 5 GB 内存下，容器额外占用与 `--memory` 限制会压缩 PathFinder 的可用堆。
- 调试密度高：失败时要看 stderr、`result.txt`、jobdir，容器把这些都推远一层。
- 我们要 profile 的是编译器，容器会给性能数字引入噪声。

**代价**：失去命名空间隔离；宿主升级可能破坏环境。

**缓解**：不可变性改由 `conda list --explicit`（URL + sha256）+ `conda pack` 归档 + 二进制 sha256 记录承担；`cgra doctor` 每次运行前校验。

---

## ADR-017：两个 conda 环境（`cgra-tc` / `cgra`），而不是一个

**背景**：LLVM 10 已 EOL，工具链几乎不变；应用依赖（LangGraph、deepagents）迭代很快。

**决定**：工具链一个环境，应用一个环境；`cgra-tc` 只在 M0 建一次并 `conda pack` 归档，`cgra` 可频繁升级。

**理由**：合成一个环境时，任何一次 `conda install` 都可能拖动 `libLLVM` 的版本，导致 pass 与 `opt` ABI 不匹配——这种故障表现为"昨天还能跑"，极难定位。

**代价**：两个环境、两套激活；跨环境调用要用绝对路径。

**缓解**：`tools/` 里所有子进程都用 `TC/bin/...` 绝对路径，不依赖当前激活的是哪个环境；`doctor` 打印两个环境的关键包版本。

---

## ADR-018：LLVM 10 用 conda-forge 自装，三件套全部本地重编

**背景**：Ubuntu 24.04 没有 llvm-10；GitHub 不可达；5 GB 内存编不动 LLVM 源码。

**决定**：`clang=10.0.1`、`llvmdev=10.0.1`、`llvm-tools=10.0.1` 从 conda-forge 安装；`libdfggenPass.so`、`cgra_xml_mapper`、`hycube_simulator` 全部从 vendor 源码本地重编，**绝不加载旧 `.so`**。

**理由**：LLVM 的 C++ ABI 不稳定，pass 与 `opt` 必须同一套构建；混用旧二进制会在 `opt -load` 时报未定义符号，或更糟——静默跑出错的结果。

**代价**：如果 conda 的 10.0.1 与 Ubuntu 的 10.0.0 行为有差异，DFG 可能对不上。

**缓解**：M0 的"DFG 对拍"（`array_add` 的节点数/边数/op 直方图与参考一致）是硬 gate；不一致时查 pass 的 LLVM API 用法，而不是改 DFG 语义（pass 源码零改动是约束）。

---

## ADR-019：LangGraph 做骨架，deepagents 做策略子任务

**背景**：ADR-007 曾决定自研状态机，理由是"框架会把控制流藏起来"。但自研意味着并行扇出、检查点、中断、回放都要自己实现并自己测——这些恰是最容易出错、最不该花时间的地方。

**决定**：控制流用 LangGraph `StateGraph`（节点 = 状态、条件边 = 失败出口、`Send` = 并行假设、`SqliteSaver` = 检查点）；A5 的逐 II 规划与 A6 的诊断用 deepagents 装配（todo 规划 + 只读子 agent）。硬门仍是普通 Python 函数。

**理由**：

- 控制流画在图上比藏在 `while` 里更可审、更可回放。
- deepagents 的 todo/子 agent 正好对应"策略性、可并行、上下文要隔离"的两类子任务。
- 硬门不进 agent，幻觉就没有绕过校验的通道。

**代价**：deepagents 是快速演进的包，API 可能漂移。

**缓解**：版本精确到 patch；`agents/` 做适配层，图只依赖 `run_design(...)` 这类函数签名；升级只改适配层。

---

## ADR-020：vendor 用离线 `git archive`，Morpher 仓库只做对照

**背景**：GitHub 不可达，拿不到上游；但 `~/morpher-src` 的三个 submodule 各自带完整 git 对象。

**决定**：从本地 git 对象 `git archive HEAD` 生成纯净快照，`git diff`（含 `add -N` 后的未跟踪文件）生成 patch；运行时不需要 Morpher 仓库、镜像或网络。对照实验单独放 `bench/oracle/`。

**理由**：源码快照 + patch 是最小的可重建单元；把仓库当作"一次性源码来源"而不是依赖，才谈得上"独立运行"。

**代价**：上游若有新版本，需要人工拉取（当前不可能）。

**缓解**：`VENDOR.md` 记录 SHA 与日期；`check_vendor.sh` 校验 patch 可干净应用；`bench/oracle/` 的结果单独归档，不参与主链路判定。