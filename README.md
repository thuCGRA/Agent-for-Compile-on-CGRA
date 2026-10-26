# CGRA Agent Compiler

面向 CGRA 编译器的 agent 工作流研究工程。后端以 [Morpher](https://github.com/ecolab-nus/morpher)（开源 CGRA 编译与仿真框架）为映射/仿真引擎，目标是把 LLM agent 从"参数调优的外包层"下沉到编译循环内部，做可验证、可诊断、可回滚的搜索与修复。

> 状态：**M0 环境已建成**（2026-10-26）：三件套在 WSL 上从零编出，端到端冒烟通过（DFG 20 节点 → 映射 II=4 → 仿真 387 向量零失配）。下一步是 M1 的 `cgra` CLI 与契约层。

## 文档

- [工程与环境规划](docs/ENGINEERING_PLAN.md) —— **从这里开始**：环境、目录、接口、编排、patch 计划、边界、里程碑
- [环境手册](docs/ENVIRONMENT.md) —— 版本矩阵、七个坑（GCC 7.5 / NDEBUG / ABI）与可复现步骤
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
conda env create -f toolchain/environment.yml   # cgra-tc：LLVM 10 + GCC 7.5 + cmake
conda env create -f environment.yml             # cgra：Python 3.12 + LangGraph + deepagents

bash scripts/vendor_morpher.sh                  # 从本地 morpher 仓库离线导出源码快照 + patch
conda run -n cgra-tc bash toolchain/build.sh    # 编出 toolchain/out/ 三件套
bash scripts/smoke.sh                           # 端到端冒烟：DFG → 映射 → 仿真，要求零失配
```

编译时锁 **GCC 7.5**（与上游 ubuntu:18.04 镜像一致）而不是系统 GCC 13，并且**必须保留 assert**（`-DNDEBUG` 会让模拟器死循环）——原因都写在 [ENVIRONMENT.md](docs/ENVIRONMENT.md) 的"七个坑"里。

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
