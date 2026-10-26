# CGRA Agent Compiler

面向 CGRA 编译器的 agent 工作流研究工程。后端以 [Morpher](https://github.com/ecolab-nus/morpher)（开源 CGRA 编译与仿真框架）为映射/仿真引擎，目标是把 LLM agent 从"参数调优的外包层"下沉到编译循环内部，做可验证、可诊断、可回滚的搜索与修复。

> 状态：**v0.3 规划完成**（WSL 原生 + conda 双环境 + LangGraph/deepagents），准备进入 M0（环境冻结与复现）。代码尚未开始。

## 文档

- [工程与环境规划](docs/ENGINEERING_PLAN.md) —— **从这里开始**：环境、目录、接口、编排、patch 计划、边界、里程碑
- [工作流架构](docs/AGENT_WORKFLOW_ARCHITECTURE.md) —— 方法论、agent 角色、三层闭环、失败路由、评估协议
- [接口与数据模型](docs/INTERFACES.md) —— CompileContract / DesignPoint / CDD / MapResult / SimResult
- [关键决策记录](docs/DECISIONS.md) —— ADR，含"为什么回到 WSL 原生""为什么用 LangGraph"

## 一句话主张

不是让 LLM "生成一个编译器"，而是让 LLM 在真实编译器内部做**有工具背书、可被仿真验证的假设**：LLM 负责全局结构先验、诊断与策略，确定性 mapper 负责路由，cycle-accurate 仿真负责正确性裁决。

## 环境（两个 conda 环境）

| 环境 | 内容 | 用途 |
|---|---|---|
| `cgra-tc` | clang/llvm 10.0.1、cmake、make | 工具链：编译 DFG pass、mapper、simulator |
| `cgra` | Python 3.12、LangGraph、deepagents | 应用：agent 图、工具封装、报告 |

```bash
conda env create -f toolchain/environment.yml   # cgra-tc
conda env create -f environment.yml             # cgra

conda run -n cgra-tc bash toolchain/build.sh    # 编出 toolchain/out/ 三件套
conda run -n cgra cgra doctor                   # 自检：版本 / sha256 / DFG 冒烟
```

工具链与 Morpher 仓库**解耦**：源码以快照 + patch 形式 vendored 在 `toolchain/vendor/`，本地重编，运行时不依赖上游仓库、镜像或网络。

## 目录

```
docs/            架构、接口、决策
toolchain/       vendor 源码 + patches + build.sh + out/ 二进制
src/cgra_agent/  contract / facts / checks / tools / runtime / agents / memory / reports
benchmarks/      6 个内核与金标准
bench/oracle/    可选：调 morpher_dev 做对照
tests/           unit / contract / e2e
runs/            实验产物（gitignored）
```

## 参考工作

- Morpher: Wijerathne et al., *An Open-Source Integrated Compilation and Simulation Framework for CGRA*
- MACO (= MALTA v4/v7，同一 arXiv 编号 2509.13557): Jiang et al., *A Multi-Agent LLM Framework for Automated CGRA Hardware/Software Co-Design*
- Beacon: Li et al., *LLM Multi-Agent Driven Hardware Design Space Exploration for Heterogeneous Multi-Chiplet DL Accelerators*
