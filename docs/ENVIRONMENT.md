# 环境手册（M0 实测版）

> 状态：**已在 WSL 上从零复现并跑通端到端冒烟**（DFG → bank 架构 → 映射 → 仿真，387 向量零失配）。
> 记录时间：2026-10-26，机器：WSL2 Ubuntu 24.04，Python 3.12.3，conda 26.1.1。

本文只写"怎么做"和"为什么这么做"。规划层面的取舍见 [ENGINEERING_PLAN.md](ENGINEERING_PLAN.md)，环境相关的决策见 [DECISIONS.md](DECISIONS.md)。

## 1. 一次性安装

```bash
# 1) 工具链环境：LLVM 10 + 编译器 + 构建工具（含 GCC 7.5）
conda env create -f toolchain/environment.yml

# 2) 应用环境：Python 3.12 + LangGraph + deepagents
conda env create -f environment.yml

# 3) 从上游仓库取三件套源码快照，打上本地补丁，并记录来源 commit
bash scripts/vendor_morpher.sh

# 4) 编译（必须在 cgra-tc 里，脚本会自己清掉 conda 注入的 CC/CXX/CPPFLAGS）
conda run -n cgra-tc bash toolchain/build.sh

# 5) 端到端冒烟
bash scripts/smoke.sh
```

第 5 步的期望输出：

```
[A] ABI：opt 能加载 DFG pass          PASS
[B] DFG 生成（array_add）              PASS  nodes=20
[C] 生成带 bank 布局的 arch            PASS  arch=array_add_mem.json
[D] 映射（method 0 PathFinder，4x4）   Map Success with II = 4  (lat = 11)
[E] 仿真（比对测试向量）               Matches::387, Mismatches::0
SMOKE OK
```

整条链在一台 8 核机器上约 5 分钟（含编译三个 C++ 目标），此后每次增量重编在 30 s 内。

## 2. 版本矩阵

| 组件 | 版本 | 来源 | 为什么是这个版本 |
|---|---|---|---|
| clang / opt / llc | 10.0.1 | conda-forge | DFG pass 必须与 opt 同一 LLVM；10 是 Morpher 上游锁定的版本 |
| LLVM CMake 包 | 10.0.1（`LLVM_ENABLE_RTTI=YES`） | conda-forge | 决定 pass 的编译选项，见坑 5 |
| cmake | 3.29 | conda-forge | 上游 CMakeLists 需要 ≥3.13 |
| 宿主 C++ 编译器（mapper / simulator） | **GCC 7.5.0**（crosstool-NG 1.24） | conda-forge `gxx_linux-64=7.5` | 与上游 Dockerfile（ubuntu:18.04）一致，见坑 1 |
| binutils / ld | 2.42（系统） | Ubuntu 24.04 | conda GCC 7 自带的 ld 太老，见坑 2 |
| Python | 3.12.3（系统 `python3`） | Ubuntu 24.04 | 仓库脚本用 `pathlib`/f-string，3.8+ 即可 |

## 3. 七个坑（按踩到的顺序）

### 坑 1：用 GCC 13 编 mapper，解析 arch JSON 时 SIGSEGV / SIGILL

症状：`cgra_xml_mapper` 一启动就崩，gdb 栈顶是 `CGRA::PreprocessPattern`（`CGRA.cpp:817`），`-O3` 与 `-O0` 都复现；而作者预编译的二进制（`readelf -p .comment` 显示 GCC 7.5.0）跑同一个 JSON 正常。

结论：上游代码里存在依赖编译器行为的 UB，GCC 7 恰好容忍、GCC 13 不容忍。我们**不修上游代码**，改用 GCC 7.5 编译，与上游二进制保持同一 ABI 行为。

> 这条同时解释了为什么"直接用系统 g++ 编一遍"会失败——不是配置问题，是源码问题。

### 坑 2：conda 的 GCC 7 自带 ld 认不了 `.relr.dyn`

症状：`ld: unknown type [0x13] section '.relr.dyn'`，`collect2: error: ld returned 1 exit status`，连 CMake 的编译器自检都过不去。

原因：conda 的 `gcc_linux-64=7.5` 附带 binutils 2.28 级别的 `ld`，而 Ubuntu 24.04 的 `ld-linux-x86-64.so.2` 用了 DT_RELR 重定位（binutils ≥2.36 才支持）。

处置：编译时加 `-B/usr/bin`，让 GCC 用系统的 binutils 2.42 做汇编/链接（`toolchain/build.sh` 里的 `HOSTFLAGS`）。编译器仍是 GCC 7.5，只是换了 `as`/`ld`。

### 坑 3：`-DNDEBUG` 会让模拟器死循环

症状：simulator 用 `Release`（`-O3 -DNDEBUG`）编出来，跑我们自己的 `binary.bin` 时**卡死**、不输出任何结果；同一份输入用 `Debug`（`-O0`）编出来是正常的 387/0。`-O2` 加 NDEBUG 也卡，`-O2` 不加 NDEBUG 正常。

原因：上游模拟器里有**带副作用的 assert**（断言表达式里有自增/赋值之类），`NDEBUG` 把断言整条删掉后，循环条件再也不会被推进。

处置：mapper 与 simulator 都用 `CMAKE_BUILD_TYPE=Debug` + 手动 `-O2`（`HOSTFLAGS`），既保留 assert 又保留优化。

### 坑 4：`conda activate cgra-tc` 会污染 `CC` / `CXX` / `CPPFLAGS`

装了 `gcc_linux-64` 之后，激活环境会导出 `CC=x86_64-conda-linux-gnu-cc`、`CXX=...-c++`，甚至 `CPPFLAGS=-DNDEBUG -D_FORTIFY_SOURCE=2 ...`。

后果：DFG pass 会被悄悄换成 GCC 7 编译（与 LLVM 10 的 libstdc++ ABI 不匹配），而 `CPPFLAGS` 里的 `-DNDEBUG` 会再次触发坑 3。

处置：`toolchain/build.sh` 开头 `unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CMAKE_ARGS ...`，随后每个目标显式指定编译器（pass 用 clang，mapper/simulator 用 GCC 7.5）。

### 坑 5：pass 必须和 opt 同源

`libdfggenPass.so` 用 clang 10 编，`opt` 也是 10.0.1，且两边都开了 RTTI（`LLVM_ENABLE_RTTI=YES`）、都用 `_GLIBCXX_USE_CXX11_ABI=1`。错一个就会在 `opt -load` 时报 `undefined symbol` 或直接 abort。

`toolchain/bin/clang++` 是一个包装脚本，内部是 `clang --driver-mode=g++`——conda 的 LLVM 10 不带 `clang++` 可执行文件，必须这样绕。

### 坑 6：编译 i386 目标缺 32 位头文件

DFG 生成阶段用 `clang -target i386-unknown-linux-gnu`，系统没装 `gcc-multilib`，会报 `gnu/stubs-32.h: No such file`。

处置：`toolchain/include32/gnu/stubs-32.h` 放一个空壳头，编译时 `-isystem toolchain/include32 -isystem /usr/include/x86_64-linux-gnu`。只影响前端语义检查，不产生 32 位机器码（我们只取 LLVM IR）。

### 坑 7：`python` 不在 PATH

`cgra-tc` 里没有 `python`，只有系统的 `python3`。仓库脚本（`update_mem_alloc.py`）用 `python3` 调用；`smoke.sh` 里做了 `command -v python3` 的回退。

## 4. 产物

| 文件 | 大小 | 作用 | sha256（本次构建） |
|---|---|---|---|
| `toolchain/out/libdfggenPass.so` | 59 MB | LLVM pass，C → DFG XML + mem_alloc + 插桩 IR | `b1688918…bd992222` |
| `toolchain/out/cgra_xml_mapper` | 35 MB | 映射器（含 method 0/1/2/3） | `453dcc9a…24f4e579` |
| `toolchain/out/hycube_simulator` | 1.9 MB | cycle-accurate 模拟器 | `7438cead…6c1447` |

完整校验和见 `toolchain/out/SHA256SUMS`（每次构建重写；跨机器比对以它为准）。

参考性能（4×4 HyCUBE，array_add，20 节点）：

- 映射：II=4、lat=11，`method 0` 约 **0.9 s**
- 仿真：134 个 CGRA cycle，387 个测试向量全部匹配
- 编译：三件套冷编译约 5 min，增量 <30 s

## 5. 尚未验证 / 已知限制

- **method 3（LLM pins）没跑通**：需要 `{"pins":[...]}` 输入与 `--report` 输出，`tools/morpher_llm` 尚未移植进本工程；这是 P2 的工作。
- **trace 生成没有脚本化**：`*_trace_0.txt` 由插桩后的 32 位程序跑出来，目前是手工步骤；P1 的 runner 会接管。
- **8×8 / 多 bank 配置未验证**：目前只验证 4×4 + 2 banks × 2048。
- **模拟器依赖 assert**：如果以后要开 NDEBUG，必须先修上游那个带副作用的 assert，否则会静默死循环。