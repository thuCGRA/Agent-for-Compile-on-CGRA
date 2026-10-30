# CGRA Agent Compiler

面向 CGRA 编译器的 agent 工作流研究工程。后端用 [Morpher](https://github.com/ecolab-nus/morpher) 的映射器与周期精确仿真器，前端用本地 LLVM 10 从 C 提取 DFG。核心问题：**LLM 应该放在编译器的哪个位置才有用**。

> 状态（2026-10-30）：工具链、10 kernel 评测台、双工作树与两臂实现已就绪；实验结论见 [FINDINGS](docs/FINDINGS.md)。

## 两条实验臂

| 臂 | 布局 | 布线 |
|---|---|---|
| **A** | LLM | 寻路程序（PathFinder） |
| **B** | 模拟退火 | LLM |
| C | 模拟退火 | 寻路程序 |
| D | LLM | LLM |

主链相同：`LLVM → facts → Placer → Router → 仿真 → 诊断`，两个槽位可插拔。详见 [计划](docs/PLAN.md)。

## 目前站得住的结论

- II 由 DFG 结构决定，放置改不动它；能动的只有可布性与 lat。
- 让 agent 同时给 `(x,y,t)` 是错的接口：mapper 自己会调度。改成只给 `(x,y)`（`t=-1` 表示相位自由）后，同一批 LLM 输出的可映射数从 **2/10 变成 7/10**。
- 可布不等于布得好：7 个里只有 `array_add` 的 lat 优于基线（10 vs 11），其余更差。
- `-m 1`（SA）与 `-m 0`（PathFinder）的 II/lat 逐项相同，只差耗时；`-m 2`（LISA）缺训练产物，10/10 断言。

详见 [FINDINGS](docs/FINDINGS.md)（23 节，含每次自我否决）。

## 文档

- [工作流](docs/WORKFLOW.md) —— 主链、状态机、失败路由、工具与数据契约
- [计划](docs/PLAN.md) —— 两条实验臂、里程碑、评测协议、风险
- [环境手册](docs/ENVIRONMENT.md) —— 版本矩阵、七个坑（GCC 7.5 / NDEBUG / ABI）、复现步骤

## 快速开始

```bash
conda env create -f toolchain/environment.yml   # cgra-tc：LLVM 10 + GCC 7.5 + cmake
conda env create -f environment.yml             # cgra：Python 3.12 + LangGraph

MORPHER_SRC=~/morpher-src bash scripts/vendor_morpher.sh   # 从 Morpher 工作区离线导出源码快照并打补丁
conda run -n cgra-tc bash toolchain/build.sh    # 编译 pass / mapper / simulator
bash scripts/smoke.sh                           # DFG → 映射 → 仿真 冒烟
```

冒烟期望：`Map Success with II = 4`、`Matches::387, Mismatches::0`。

`toolchain/vendor/` 是 git-ignore 的源码快照（122 MB），由 `scripts/vendor_morpher.sh` 从上游 [Morpher](https://github.com/ecolab-nus/morpher) 工作区导出；克隆本仓库后必须先准备该工作区并把 `MORPHER_SRC` 指向它，否则只能读代码、不能编译。
