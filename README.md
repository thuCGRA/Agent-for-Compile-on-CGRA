# acc-thu：CGRA 编译器的可信最小骨架

把 [Morpher](https://github.com/ecolab-nus/morpher)（开源的 CGRA 编译 + 仿真框架）里**属于上游原版**的三件东西，
裁成一个自包含、可端到端跑通、约 4 MB 源码的工程：

| 阶段 | 组件 | 作用 |
|---|---|---|
| 前端 | **DFG 生成**（LLVM pass）| C 内核 → 数据流图 XML + 变量基址表 |
| 中端 | **映射**（mapper）| DFG + 硬件描述 → 布局布线 → 配置包（bitstream）|
| 后端 | **仿真**（simulator）| 配置包 + 数据 trace → 逐周期执行 → 与参考数据比对 |

**不包含**：LLM 映射、QuickMapper、`PartPredLight`、应用集（applications/）、AES/pedometer 等
演示工程。这些都不在上游 9a9dce7 里，或者不属于编译器主线，见 [PROVENANCE.md](PROVENANCE.md)。

## 快速开始

```bash
bash scripts/build.sh        # 编译三件套 → out/
bash scripts/run_all.sh      # 三个 kernel 端到端跑一遍（约 22 s）
```

`run_all.sh` 的输出（在 `sutong_103:~/acc-thu` 上实测）：

```
kernel     nodes  II   map        sim
array_add  20     4    ok         PASS
mac        22     4    ok         PASS
trmm       11     4    ok         PASS

全部通过
```

## 目录

```
acc-thu/
├── src/
│   ├── dfggen/          LLVM pass：C → DFG（上游 Morpher_DFG_Generator，去掉 benchmarks）
│   ├── mapper/          布局布线（上游 Morpher_CGRA_Mapper，去掉 applications/json_arch）
│   └── simulator/       周期级仿真器（上游 hycube_simulator/src）
├── arch/                硬件描述（ADL JSON）        → docs/01-architecture.md
├── kernels/             三个 C 内核 + kernels.tsv
├── scripts/             全流程 bash，每个脚本只做一件事
├── toolchain/
│   ├── patches/         对上游源码的全部改动（目前只有 1 个 2 行的 patch）
│   └── include32/       32 位 glibc 头的空壳（见 docs/02-dfg.md）
├── out/                 所有产物（每个 kernel 一个目录）
└── docs/                四篇说明
```

## 脚本

按流水线顺序，每个都能单独跑、单独调：

| 脚本 | 用法 | 输入 → 输出 |
|---|---|---|
| `build.sh` | `bash scripts/build.sh` | 源码 → `out/{libdfggenPass.so,cgra_xml_mapper,hycube_simulator}` |
| `gen_dfg.sh` | `bash scripts/gen_dfg.sh <k> [banks] [banksize]` | `kernels/<k>/<k>.c` → `out/<k>/{dfg.xml,mem_alloc.txt,*.dot}` |
| `inject_mem.sh` | `bash scripts/inject_mem.sh <k> [banks] [banksize]` | `mem_alloc.txt` + `arch/hycube_4x4_updatemem.json` → `out/<k>/arch.json` |
| `gen_trace.sh` | `bash scripts/gen_trace.sh <k>` | `kernel.c` → `out/<k>/trace_0.txt`（真跑一遍程序记录访存） |
| `map.sh` | `bash scripts/map.sh <k> [method] [II] [x] [y]` | `dfg.xml` + `arch.json` → `out/<k>/m<method>/`（含 `.bin`） |
| `sim.sh` | `bash scripts/sim.sh <k> [method] [mem]` | `.bin` + `trace_0.txt` → `Matches/Mismatches` |
| `run_all.sh` | `bash scripts/run_all.sh [k...]` | 以上串起来，打表 |

环境变量：`METHOD=1` 换模拟退火（`METHOD=1 bash scripts/run_all.sh mac`），`CONDA_ENV=xxx` 换 conda 环境。

## kernel 清单

`scripts/kernels.tsv` 三列：`kernel<EOTAB>函数名<EOTAB>来源`。

| kernel | 被映射的函数 | 说明 |
|---|---|---|
| `array_add` | `array_add` | 20 节点，C[i]=A[i]+B[i]，最小完整例子 |
| `mac` | `main` | 22 节点，循环写在 `main` 里，所以函数名是 `main` |
| `trmm` | `trmm` | 11 节点（DFG 跨外层循环切出来的一段）|

`kernels/<k>/<k>.c` 与上游 `Morpher_DFG_Generator/benchmarks/` 下的文件逐字节相同。

## 三件套版本

| 组件 | 编译者 | 版本 | 为什么是这个 |
|---|---|---|---|
| dfggen | conda `clang` | LLVM/Clang 10.0.1 | pass 与 `opt` 必须同一个 LLVM 版本 |
| mapper | conda GCC | 7.5.0 | GCC 13 编出来解析 ADL 时踩 latent UB，直接 SIGSEGV |
| simulator | conda GCC | 7.5.0 | 同上；且必须保留 `assert`（Release 的 `-DNDEBUG` 会删掉带副作用的断言 → 死循环）|

`build.sh` 顶部有一段注释解释每个选择的来历，改动前先读它。

## 已知限制

1. **只能 4x4 的 HyCUBE 形状**：`src/mapper` 的 bitstream 生成（`printHyCUBEBinary`）硬编码了
   FU0/DP0 与 HyCUBE 的端口名，换架构要先改那里。
2. **ADL 表达力不足**：现有 ADL 无法描述阵列级共享寄存器堆、位置相关的多跳路由码、
   配置包模型、多周期 EX。见 `docs/01-architecture.md` 末节。
3. **bank 连线缺失**：`update_mem_alloc.py` 只往 ADL 里写 `DATA_LAYOUT`，不写 PE→bank 的连线，
   所以"哪个 PE 能访问哪个 bank"在 ADL 里其实没有权威描述。
4. **trace 是 native 生成的**：i386 目标的 C++ 标准库头在 conda 里没有，所以插桩程序用 host
   g++ 编。trace 只记 4 字节数据值，与字宽无关，但对指针运算敏感的 kernel 需复核。

## 许可

`src/` 下三个组件来自 Morpher，MIT License，Copyright (c) 2021/2023 ecolab-nus。
本工程对上游的改动全部记录在 [PROVENANCE.md](PROVENANCE.md) 与 `toolchain/patches/`。
