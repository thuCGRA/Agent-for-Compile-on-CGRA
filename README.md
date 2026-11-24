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
bash scripts/run_all.sh                    # kernels.tsv 里全部 48 个
bash scripts/run_all.sh array_add mac trmm  # 只跑指定的几个（约 22 s）
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
├── kernels/             48 个上游 testcase + benchmarks.tsv / kernels.tsv
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

上游 Morpher 的全部 testcase 都已装进来：**48 个**（按内容 sha256 去重后的数量；
`polybench/trmm/` 下 12 个是 `polybench/` 根目录同名文件的逐字节副本，已略去）。

* `scripts/benchmarks.tsv` —— 权威清单，三列 `kernel<TAB>函数名<TAB>上游相对路径`，
  并标了每个 case 属于哪一组（cgrame / morpher / opencgra / pace / woset）。
* `scripts/kernels.tsv` —— 由它生成的、`run_all.sh` 实际迭代的两列表。
* `bash scripts/install_benchmarks.sh` —— 按清单从上游重新拷一遍（幂等）。

| 组 | kernel | 说明 |
|---|---|---|
| morpher_benchmarks | `array_add` `array_cond` `hpcg` `trmm` | 上游 `run_all.sh` 的四个主力 |
| cgrame_microbench | `mac` `mac2` `sum` `conv2` `conv3` `mults1` `mults2` | 循环都写在 `main` 里，所以 `-fn main` |
| opencgra_benchmarks | `fir` | 源文件叫 `kernel.c`，函数名 `kernel` |
| pace_benchmarks | `gemm` `conv2d_2x2` `conv2d_3x3` | `conv2d` 的两个滤波尺寸是两份不同源文件 |
| woset / gemm | `gemm_w1` `gemm_w2` `gemm_w3` | 三档 SIZE，源文件都叫 `gemm.c` |
| woset / microspeech | `microspeech_1..4` | 要 `param_conv.txt` / `param_fc.txt` 和 `kernels/microspeech_test_data/` |
| woset / polybench | `pb_2mm` … `pb_trmm_u`（26 个） | **上游 DFG pass 不支持**，见下 |

`kernels/<k>/<k>.c` 与上游 `Morpher_DFG_Generator/benchmarks/` 下的文件逐字节相同
（`install_benchmarks.sh` 只做拷贝，不改内容）。

### 实测：22 个能跑通 DFG，16 个端到端零失配

`bash scripts/run_all.sh` 在 48 个 case 上的实测（II 由 mapper 从 RecMinII 起搜，
method 0 = 上游 PathFinder）：

| 结果 | 数量 | kernel |
|---|---|---|
| 端到端通过（`Mismatches::0`） | **16** | array_add array_cond trmm mac mac2 sum conv2 conv3 mults2 fir gemm conv2d_2x2 conv2d_3x3 gemm_w1 gemm_w2 gemm_w3 |
| DFG/映射通过、仿真有失配 | 5 | hpcg(1) mults1(4) microspeech_1(无结果行) microspeech_2(4) microspeech_4(502) |
| 映射失败 | 1 | microspeech_3 |
| **DFG pass 不支持** | **26** | 全部 `pb_*`（polybench） |

polybench 那 26 个的源码用 `volatile int **A; A[i][k]` 这种二级指针，而上游 DFG pass
假定每次访存的基址都是"命名数组上的 GEP"，遇到 `int**` 会在
`DFG::UpdateSPMAllocation` 里对空指针解引用。本工程把那里改成 `report_fatal_error`
并给出可读提示（而不是段错误），同时保留 `DFG::getTransferVariables` /
`DFG::SetBasePointers` 里的空指针防御。**这不是把 polybench 跑通**：静态 SPM 地址模型
本来就没法表达动态二级指针。要用 polybench 得先把数组展平成一维（`int *A`）。

## 三件套版本

| 组件 | 编译者 | 版本 | 为什么是这个 |
|---|---|---|---|
| dfggen | conda `clang` | LLVM/Clang 10.0.1 | pass 与 `opt` 必须同一个 LLVM 版本 |
| mapper | conda GCC | 7.5.0 | GCC 13 编出来解析 ADL 时踩 latent UB，直接 SIGSEGV |
| simulator | conda GCC | 7.5.0 | 同上；且必须保留 `assert`（Release 的 `-DNDEBUG` 会删掉带副作用的断言 → 死循环）|

`build.sh` 顶部有一段注释解释每个选择的来历，改动前先读它。
整套环境可以用 `conda env create -f toolchain/environment.yml` 复现（name 为 `cgra-tc`）。
生成 trace 还需要系统装 32 位 glibc 头：`sudo apt-get install -y gcc-multilib libc6-dev-i386`。

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
