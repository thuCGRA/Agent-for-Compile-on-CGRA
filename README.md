# CGRA Agent Compiler

面向 CGRA 编译器的 agent 工作流研究工程。后端用 [Morpher](https://github.com/ecolab-nus/morpher) 的映射器与周期精确仿真器，前端用本地 LLVM 10 从 C 提取 DFG。核心问题：**LLM 应该放在编译器的哪个位置才有用**。

> 状态：M0 环境已建成（2026-10-26，端到端冒烟通过）。正在进入 M1。

## 两条实验臂

| 臂 | 布局 | 布线 |
|---|---|---|
| **A** | LLM | 寻路程序（PathFinder） |
| **B** | 模拟退火 | LLM |
| C | 模拟退火 | 寻路程序 |
| D | LLM | LLM |

主链相同：`LLVM → facts → Placer → Router → 仿真 → 诊断`，两个槽位可插拔。详见 [计划](docs/PLAN.md)。

## 文档

- [工作流](docs/WORKFLOW.md) —— 主链、状态机、失败路由、工具与数据契约
- [计划](docs/PLAN.md) —— 两条实验臂、里程碑、评测协议、风险
- [环境手册](docs/ENVIRONMENT.md) —— 版本矩阵、七个坑（GCC 7.5 / NDEBUG / ABI）、复现步骤

## 快速开始

```bash
conda env create -f toolchain/environment.yml   # cgra-tc：LLVM 10 + GCC 7.5 + cmake
conda env create -f environment.yml             # cgra：Python 3.12 + LangGraph

bash scripts/vendor_morpher.sh                  # 离线导出三件套源码快照并打补丁
conda run -n cgra-tc bash toolchain/build.sh    # 编译 pass / mapper / simulator
bash scripts/smoke.sh                           # DFG → 映射 → 仿真 冒烟
```

冒烟期望：`Map Success with II = 4`、`Matches::387, Mismatches::0`。
