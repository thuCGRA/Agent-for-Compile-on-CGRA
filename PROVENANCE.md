# 来源与改动记录

上游：`Morpher`，commit **9a9dce7**（GitHub: `ecolab-nus/morpher`，MIT License，
Copyright (c) 2021/2023 ecolab-nus）。服务器上的干净副本在 `~/morpher`。

本工程 = 上游三个组件 + 我们自己的 9 个 bash 脚本 + 3 个 kernel + 2 个 ADL 文件。
**对上游 C++ 源码的实质改动只有一个 2 行的 patch**，其余差异都是"删除"和"新增文件"。

## 一、`src/` 与上游的逐文件差异（已实测）

```
$ diff -rq src/dfggen ~/morpher/Morpher_DFG_Generator -x benchmarks -x '*.md'
（无输出）

$ diff -rq src/simulator ~/morpher/hycube_simulator/src
（无输出）

$ diff -rq src/mapper ~/morpher/Morpher_CGRA_Mapper -x applications -x json_arch -x visualize -x '*.md'
文件 src/mapper/src/CMakeLists.txt 和 .../src/CMakeLists.txt 不同
```

也就是说：**dfggen 与 simulator 与上游逐字节相同；mapper 只差一个 CMakeLists 里的两行。**

## 二、唯一的上游源码改动

`toolchain/patches/0001-mapper-no-hardcoded-compiler.patch`：

```diff
-set(CMAKE_C_COMPILER "gcc-7")
-set(CMAKE_CXX_COMPILER "/usr/bin/g++-7")
+# set(CMAKE_C_COMPILER "gcc-7")
+# set(CMAKE_CXX_COMPILER "/usr/bin/g++-7")
```

上游把编译器写死成系统 `gcc-7` / `/usr/bin/g++-7`，在没装系统 gcc-7 的机器上会
`/bin/sh: /usr/bin/g++-7: No such file or directory`。注释掉之后由 `build.sh` 用
`-DCMAKE_C(XX)_COMPILER` 传入 conda 环境里的 GCC 7.5。

`build.sh` 的 `apply_patch()` 会打补丁并在 `src/mapper/.0001-*.applied` 留下标记文件，
重复执行不会二次打补丁。

## 三、删掉了什么（只删不加，避免混淆上游内容）

| 上游路径 | 体积 | 为什么删 |
|---|---|---|
| `Morpher_CGRA_Mapper/applications/` | 38 MB | 一堆预生成的 DFG/映射结果，与编译器本体无关 |
| `Morpher_CGRA_Mapper/json_arch/` | 576 KB | 20+ 个架构描述变体；本工程只用 HyCUBE 4x4，放 `arch/` |
| `hycube_simulator/yash-aes/` | 61 MB | AES 演示工程 |
| `hycube_simulator/pedometer*` | — | 演示工程 |
| `Morpher_DFG_Generator/benchmarks/` | 3.2 MB | 上游 20+ 个 benchmark；本工程只留 3 个，放 `kernels/` |
| `Morpher_DFG_Generator/run_pass.sh` | — | 调用 `libSkeletonPass.so` 的旧脚本，上游自己也已不用 |

**重点**：`src/` 里**没有** `LLMMapper`、`QuickMapper`、`DFGPartPredLight`。
这三个是本地 `morpher-src` 上后来加的（LLM 映射方案与 light 编译模式），不属于上游 9a9dce7。
校验方式：

```bash
find src -name '*LLM*' -o -name '*Quick*' -o -name '*Light*'   # 空
grep -rn "mapping_method == 3\|--morpher_light" src/mapper/src/  # 空
```

顺带说明一个上游的坑：上游 `run_morpher.py` 里**引用了** `--morpher_light` 和
`dfg_type: PartPredLight`（`default_light_config.yaml` 也这么写），但上游源码里这两个符号
根本不存在——`grep -rln "Light" Morpher_DFG_Generator/` 为空，mapper 只有
`HeuristicMapper` / `PathFinderMapper` / `SimulatedAnnealingMapper` 三个实现。
所以"light 模式上游跑不通"是上游自带的残破，不是我们砍掉的。本工程因此**不使用**
`run_morpher.py`，每一步都用 `scripts/*.sh` 显式驱动。

## 四、新增的东西（我们的）

| 路径 | 说明 |
|---|---|
| `scripts/{lib,build,gen_dfg,inject_mem,gen_trace,map,sim,run_all}.sh` | 整条流水线。上游只有一个大而耦合的 `run_morpher.py`（`os.chdir` + 固定 benchmarks 路径 + 全局 `MORPHER_HOME`），无法并行、无法单步调试 |
| `scripts/kernels.tsv` | kernel → 函数名映射（`mac` 的函数名是 `main`）|
| `kernels/{array_add,mac,trmm}/*.c` | 与上游 benchmarks 逐字节相同，只是搬了位置 |
| `arch/hycube_4x4.json` | = 上游 `json_arch/hycube_original.json` |
| `arch/hycube_4x4_updatemem.json` | = 上游 `json_arch/hycube_original_updatemem.json`（`update_mem_alloc.py` 的输入）|
| `toolchain/include32/gnu/stubs-32.h` | 空壳头，让 `-target i386` 能找到 `gnu/stubs-32.h`；见 `docs/02-dfg.md` |
| `toolchain/patches/` | 全部上游改动，可审可复现 |
| `docs/` | 四篇说明 |

## 四之二、构建产物指纹

在 `/home/sutong/acc-thu` 里从零编译两次，结果完全一致（conda `cgra-tc`：LLVM 10.0.1 / GCC 7.5.0）：

| 产物 | sha256 |
|---|---|
| `out/libdfggenPass.so` | `57e4462f4c06be93e43b12655e8f0dcf4399e84ac86f262a38204df67f7c663c` |
| `out/cgra_xml_mapper` | `2144c0f6eb29142f7d89b894c0e6f9ee7b37cd9a38ef4edbac5ff4feccd1ac77` |
| `out/hycube_simulator` | `449ac2484a5769b8a41645d8e41604600032dbf78af9c9b0e8e2c02fb735a951` |

**但换一个目录编译，哈希就会变**（已实测：把同一棵树复制到 `/tmp/acc-clean` 编译得到
`libdfggenPass.so` = `6daabe55…`、`cgra_xml_mapper` = `f3b3f42c…`、`hycube_simulator` = `58483fee…`）。
原因是 mapper 与 simulator 用 Debug 构建，DWARF 里记录了源码绝对路径。
所以这些哈希只能用于**同一目录下的前后对比**（比如确认"我没改代码但重编了一次"），
不能当跨机器的内容指纹；跨机器要用 `git rev-parse HEAD` + `scripts/run_all.sh` 是否全 PASS 来判断。

`build.sh` 会把当前三个哈希写进 `out/SHA256SUMS`。

## 五、没用上游的什么（重要）

上游的 `run_morpher.py`（约 400 行）负责把五个阶段串起来，本工程**完全没有复用它**，原因是：

1. 它用 `os.chdir` 切到 `Morpher_DFG_Generator/benchmarks/<app>/`，并在全局变量 `MORPHER_HOME`
   里记 cwd —— 多 kernel 并行会互相踩；
2. 它把 `dfg_type` 强制成 `PartPredLight`（85-87 行）而该类型上游未实现；
3. 它假定 `dot` 存在，把每个 DFG 转 PDF（本机没装 graphviz 时整段失败）；
4. 它把 trace 生成、bank 注入、映射、仿真用相对路径串起来，任一步失败都难定位。

替代方案就是 `scripts/` 里 7 个 40 行左右的脚本，每个只做一件事，`out/<kernel>/` 里留全部中间产物。

## 六、与 `~/cac`（另一条工作线）的关系

`~/cac` 是早先的 LLM 放置研究工程，它 vendor 的上游代码来自用户改过的 `morpher-src`，
因此里面有 `LLMMapper`、`QuickMapper`、`PartPredLight`，且 `src` 与上游有实质差异。
本工程刻意**不**继承那些改动，所以两边二进制不可互换：

| | `~/cac` | 本工程 |
|---|---|---|
| mapper 的 `-m 3` | LLM 放置（需要 pins.json）| **不存在**（`-m 3` 未实现，会走默认分支）|
| `--morpher_light` | QuickMapper | 不存在 |
| benchmark | 10 个（含 conv2d/gemm/hpcg…）| 3 个（array_add/mac/trmm）|
| 驱动 | Python（`src/cgra_agent/`，LangGraph + 实验脚本）| 纯 bash |
