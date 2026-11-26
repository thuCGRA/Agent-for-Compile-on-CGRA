# CGRA-ME 实测记录：为什么拿不到对照数字

日期：2026-11-26 / 2026-11-27
环境：`~/cgra-me-release-v2.0.1`（非 git 快照，v2.0.1），conda env `cme`

## 一句话

CGRA-ME 的 ILP mapper **强依赖 Gurobi 商业授权**；没有授权时发布版根本不注册 ILP mapper，
唯一能构建的 `ClusteredMapper` 在本机所有配置下都对它**自带的** microbenchmark 崩溃。
所以"CGRA-ME 效果很好"这句话我们**无法在本环境验证**，也不能拿来当对照。

## 构建过程（已成功，但有五个坑）

1. `cmake_minimum_required(VERSION 3.1)` 被 CMake 3.29 拒绝
   → `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`
2. 上游 `CMakeLists.txt` 从不 `find_package(ortools)`，但 `CGRAME_USE_GOOGLE_OR_TOOLS`
   分支引用 `ortools::ortools`；且 `add_dependencies(... ortools::ortools)` 把 imported
   target 当可构建目标 → 必须在任何 `add_subdirectory` 之前 `find_package(ortools)`。
   （若在子目录作用域里调用，`PkgConfig::Cbc` 等 imported target 出作用域即消失，
   链接期报 "The link interface of target ortools::ortools contains PkgConfig::Cbc"）
3. conda 的 `libortools` 不提供 `bin/fzn-ortools`，但 `ortoolsTargets.cmake` 声明了该
   imported 可执行文件 → 造占位脚本 `$CONDA_PREFIX/bin/fzn-ortools`
4. conda-forge 的 absl 为 C++17 编译，CGRA-ME 钉 C++14
   → `absl/utility/utility.h:164: 'std::in_place_t' has not been declared`
   → 改 `CMakeLists.txt:9` 为 `set (CMAKE_CXX_STANDARD 17)`
5. 运行时需要 `CGRA_ME_ROOTDIR`（否则 `src/core/Scheduler.cpp:10` 的
   `std::string dir = std::getenv("CGRA_ME_ROOTDIR");` 对 nullptr 构造 → `basic_string::_M_construct null not valid`），
   并且 `Scheduler` 打开 `$CGRA_ME_ROOTDIR/src/sched/<arch_name>_supported_ops.dot`，
   而 `arch_name` 来自 `-c` 的数字 id（`"2"`），树里只有 `hycube_supported_ops.dot`
   → 需要 `ln -s hycube_supported_ops.dot 2_supported_ops.dot`

`build/bin/cgrame`（13.4 MB）可以跑起来，`--arch-list` 有 1=Adres、2=HyCUBE、3=Elastic RIKEN。

## 核心结论：没有 Gurobi 就没有 ILP mapper

`src/core/CMakeLists.txt` 把整个 ILP 源文件列表 gate 在 `if(GUROBI_HOME)` 里：

```
if(GUROBI_HOME)
  target_sources(core PRIVATE ILPMapper.cpp HeuristicMappers.cpp ConstraintSet.cpp)
else()
  # 什么都没有
endif()
```

`--mapper-list` 于是只有 `BothSmallTimeThenHeurFullTime` 和 `ClusteredMapper`，
而前者内部 `createMapper("ILPMapper")` 直接抛
`No mapper registered with ID 'ILPMapper'`。

我试过把 `HeuristicMappers.cpp` + `ConstraintSet.cpp` 加回 OR-Tools 构建：
`#include <gurobi_c++.h>` 能用 `#ifdef` 包住，但 `HeuristicMappers.cpp` 里有
**54 处 `GRB*` 引用不在任何 `#ifdef` 内**（`GRBEnv env = GRBEnv(true);`、
`env.set(GRB_IntParam_OutputFlag, 0)`、`GRB_DoubleParam_TimeLimit`、`GRBModel model = GRBModel(env)` …），
`ConstraintSet.cpp` 的 `addConstraintsImpl(...)` 签名里就是 `GRBCallbackPublic*` / `GRBModel*`。
→ **OR-Tools 是 Gurobi 之外额外加的后端，不是替代品。** 已 revert。

`gurobipy` 的 wheel 只带 `.libs/libgurobi110.so`，**不带 `gurobi_c++.h`**，也没有 licence。

## 剩下的 ClusteredMapper 也跑不起来

对它**自带的** `benchmarks/microbench/*/pre-gen-graph_loop.dot`：

| 配置 | 结果 |
| --- | --- |
| `simple`（最小图）`-c 2 -i 4` | `std::bad_alloc` → abort |
| `simple` `--no-reduce-mrrg` | 段错误（`Mapping... SEED: 10` 之后） |
| `sum` `-c 2 -i 4` | 段错误 |
| `sum` `-c 2 -i 1` | 段错误 |
| `-c 3`（Elastic RIKEN） | `[ERROR] MRRG verify check failed!` |
| 换系统 libstdc++（排除 conda libstdcxx-ng 16.2.0 与 g++ 9 的 ABI 冲突） | 同样崩 |

不是输入格式问题（是它自己的 benchmark），也不是我们转换器的问题。

## 对"为啥到咱这就不行了"的回答

用户的直觉是对的：CGRA-ME 的 ILP **确实**比我们的完整——它把放置
（`F[op][function_node]`）、布线（`R[val][routing_node]`）、时序（`T[node]` 整数拓扑序）、
扇出（`S[val][node][fanout_k]`）放在同一个模型里，13 组约束中 5 组是纯路由约束，
`routing_node` 是从 arch 描述**自动展开的端口级 MRRG**，所以 `FEASIBLE` 本身就构造出真实布线。
我们缺的正是这一层（见 `docs/09`、`docs/08`）。

但有三条必须打折：

1. **它的 benchmark 很小**：`sum` 7、`mac` 10、`matrixmultiply` 10、`conv2` 12、
   `accumulate` 15、`mults1` 15、`conv3` 17、`cap` 18、`mults2` 19、`mac2` 20 个 op。
   我们跑的是 array_add 20 / mac 22 / gemm 49 / adpcm_coder 105。
2. **默认不是纯 ILP**：`cgrame.cpp:116` 默认 `BothSmallTimeThenHeurFullTime`，
   是 ILP + 启发式并行 portfolio + 超时仲裁。
3. **它没证明最优**：`mip_gap` 默认 0.2、`solution_limit` 默认 1，即"找到 20% 界内第一个解就停"。

## 下一步

不再纠缠 CGRA-ME。真正要做的是 **Plan B**：把 `ilp_router.py` 从手工 0/1 弧
改成从 `arch.json` 的 `PE.CONNECTIONS` **自动展开的端口级 MRRG**，
让"布线可行"与 mapper 的端口账本使用同一张图——这正是 CGRA-ME 的做法，
也是我们唯一能自己掌控的修法。

## 复现命令

```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate cme
CME=$HOME/cgra-me-release-v2.0.1
export LD_LIBRARY_PATH=$CME/build/lib:$CONDA_PREFIX/lib
export CGRA_ME_ROOTDIR=$CME
ln -sf hycube_supported_ops.dot $CME/src/sched/2_supported_ops.dot
$CME/build/bin/cgrame -c 2 -i 4 -g $CME/benchmarks/microbench/sum/pre-gen-graph_loop.dot \
  -m ClusteredMapper -t 60      # -> segfault
$CME/build/bin/cgrame --mapper-list   # -> 没有 ILPMapper
```
