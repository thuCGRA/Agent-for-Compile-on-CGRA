# 07 · 基准覆盖与跨算法横评（48 个 test case 的实际可跑范围）

> 一句话：`kernels/` 里 48 个 case **不是 48 个都能进 mapper**。补上上游预生成 DFG 之后，可跑
> 集合从 23 个涨到 **41 个**，另有 10 个 CGRA-ME microbench 因上游 DFG 缺字段不可用；但
> **4×4 阵列只有 64 个 PE·拍**，超过 ~50 节点的图在合理时间内基本无解 —— 这是容量问题，不是
> 算法问题。

## 1. 覆盖账（48 个 case 的去向）

| 来源 | 数量 | 能否跑 | 说明 |
| --- | --- | --- | --- |
| `kernels/*/*.c` 原生 | 22 | ✅ 全部 | DFG pass 现场编译；**只有 5 个有 trace**（array_add/conv2/fir/mac/trmm），其余只能比 II 不能验仿真 |
| `kernels/pb_*/*.c` polybench | 26 | ❌ 全部 | 上游 stub 残缺（`volatile int **A;`、`i`/`j` 从未赋值），见 §2 |
| 上游预生成 polybench DFG | 10 | ✅ 8 / ⏱ 2 | `scripts/import_prebuilt.sh` 装入，见 §3 |
| 上游预生成 CGRA-ME microbench | 10 | ❌ 全部 | DFG 缺 `BasePointerName`，mapper 断言退出，见 §4 |
| 上游预生成真实应用 DFG | 14 | ✅ 5 / ⏱ 9 | 38–150 节点，大图在 4×4 上超时 |

合计可进 mapper：**22 + 10 + 14 = 46** 个 case（其中 23 个和原生重名的不重复计），实际跑出结果的 41 个。

## 2. 为什么 23 个 polybench 原生 case 编不出 DFG

三条独立原因，都实测过：

**(a) 上游 polybench stub 本身是残缺的。** `benchmarks/woset_benchmarks/polybench/*/*.c` 只剩
一个 `for (k=1;k<n-1;++k)` 循环，`i`/`j` 从未赋值：

```c
volatile int **A;           /* 二维指针 */
sum += alpha * A[i][k] * B[k][j];
```

上游自己 Release 编也会 nullptr 解引用（assert 被 NDEBUG 掉）。这是子模块 commit
`b88b372 "updating benchmark kernel"` 引入的，不是我们改的。

**(b) 多级指针过不了 DFG pass。** 报错（本工程在 `src/dfggen/src/common/dfg.cpp:11397/11427`
加的可读版，上游是裸 assert）：

```
LLVM ERROR: morpher DFG pass: unsupported memory access through a multi-level pointer
(e.g. 'int **A; A[i][k]'); the base pointer is not a GEP on a named array.
Flatten the array to 1-D or use 'int *A'.
```

**(c) 单纯改写成 1-D 也不够。** 三个反例都实测过：

| 写法 | 结果 | 原因 |
| --- | --- | --- |
| `int A[512]; A[i+j]` | ✅ 22 节点 | 单层仿射 GEP，pass 认 |
| `int A[512]; A[i*n+j]` | ❌ | `-O2` 把 `i*n` 提到循环外 → 两级 GEP |
| `int A[32][32]; A[i][j]` | ❌ | 常量折叠成常量表达式 GEP，不是指令，`arr_ptrs` 表里没有 |

所以要救这 26 个必须**真重写循环体**（把索引改成单层仿射形式并挡住 `-O2` 的提升），
不是文本替换能解决的。

## 3. 预生成 DFG 通路（`scripts/import_prebuilt.sh`）

上游 `Morpher_CGRA_Mapper/applications/` 里有 44 个**已经生成好的** DFG，没有对应可编译的
`.c`。它们只差一份 `mem_alloc.txt`。

```bash
bash scripts/import_prebuilt.sh              # 装全部（scripts/prebuilt.tsv 44 条）
bash scripts/import_prebuilt.sh pb_pre_2mm   # 只装一个
bash scripts/import_prebuilt.sh --list       # 列清单
FORCE=1 bash scripts/import_prebuilt.sh      # 覆盖已存在
```

它做三件事：拷 xml → `tools/prebuilt/synth_mem_alloc.py` 反推 `mem_alloc.txt` → 写
`manifest.json`（含 `bank_size`，`inject_mem.sh` 会读它）。

**为什么需要 `synth_mem_alloc.py`**：mapper 的 `PathFinderMapper::checkOps`
（`src/mapper/src/mapper/PathFinderMapper.cpp:3305`）硬检查「DFG 里每个 LOAD/STORE 的 base
pointer 必须在 DATA_LAYOUT 里」，缺一个就 `exit(EXIT_FAILURE)`；而那条消息走 `LOG(ARCH)`
（`include/morpher/util/debug.h:37`），**不设 `ARCH` 环境变量就完全静默** —— 表现是 `rc=1`
且日志只到 `Initial target II = 4`。这就是第一轮"48 个里 23 个静默失败"的真因。

布局约定与 `run_morpher.py` 产出的真实 mem_alloc 对齐：`loopend → bank_size-1`（2047，
模拟器哨兵地址）、`loopstart → banks*bank_size-2`（4094），数组按 size 降序轮流装 bank，
标量接在后面。`--auto` 会在大数组装不下时自动放大 `bank_size`（doitgen 的 A 有 4000 字，
单 bank 2048 装不下 → 自动升到 4096；aes 的两个 4096 字数组同理）。

**上游 DFG 的两个 bug（我们修了第一个）**：

1. `cgra_me_bench/*/…noSemiOLOAD….xml` 的 `<Output type="P"/>` **没有 `NPB` 属性**，而
   `src/mapper/src/dfg/DFG.cpp:230` 是裸的 `int npb;`，`QueryIntAttribute` 失败不回写 →
   `assert(npb == 0 || npb == 1)` 读到栈上垃圾 → `SIGABRT`。已修（`int npb = 0;`），
   补丁脚本 `tools/patches/patch_npb.py`。
2. 同一批 DFG 的 LOAD/STORE 节点**没有 `<BasePointerName>`**，于是撞
   `PathFinderMapper::UpdateVariableBaseAddr()`（`:3353`）的
   `assert(!node.base_pointer_name.empty())`。这 10 个 case 目前**不可用** —— 补 pointer 名
   属于编造语义，不做。

## 4. 表 D：预生成集上的 PathFinder vs SA（II 阶梯，每档 45 s）

`sweep_prebuilt.sh`：每个 kernel 按 `II ∈ {4,5,6,7,8,10,12,16,20}` 逐档试，命中即停。
用一个 `-i 0` 递增会让失败的档位各花满一次搜索时间（2mm 在 II=4..7 上共 300 s 才到 8）。

| kernel | 节点 | PathFinder II | SA II |
| --- | --- | --- | --- |
| pb_pre_atax | 24 | **4** (1.7 s) | — |
| pb_pre_cholesky | 26 | **4** (2.0 s) | — |
| pb_pre_doitgen | 27 | **4** (3.9 s) | — |
| pb_pre_bicg | 31 | **4** (38.6 s) | — |
| pb_pre_2mm | 28 | **5** (49.1 s) | — |
| pb_pre_2mm_u4 | 61 | **5** (14.9 s) | — |
| pb_pre_atax_u4 | 54 | **8** (204.3 s) | — |
| pb_pre_bicg_u3 | 63 | **10** (263.3 s) | — |
| pb_pre_cholesky_u4 | 59 | ⏱ 450 s | — |
| pb_pre_doitgen_u4 | 63 | ⏱ 450 s | — |
| pre_finalSAD | 38 | **10** (25.9 s) | 12 (199.6 s) |
| pre_compare_neighb | 46 | **6** (78.2 s) | 8 (133.3 s) |
| pre_gemm_nt | 52 | **8** (194.0 s) | 8 (194.2 s) |
| pre_pedometer | 59 | ⏱ 405 s | **6** (98.4 s) |
| pre_nw | 60 | ⏱ 405 s | **16** (346.9 s) |
| pre_fix_fft | 58 | ⏱ | ⏱ |
| pre_adpcm_decoder | 85 | ⏱ | ⏱ |
| pre_dct | 88 | ⏱ | ⏱ |
| pre_adpcm_coder | 105 | ⏱ | — |
| cme_* × 10 | 7–20 | ✗ 缺 base pointer | ✗ |

**这里出现了和 4×4 小图完全相反的结论**：

- 小图上 SA 与 PathFinder 逐项相同（21/23），因为 `SAMap()` 的
  `if (!initMap_with_PathFinder()) {...}` 一旦拿到合法解就
  `return true`（打印 `find a valid initial mapping, exit....II =`），**根本没退火**。
- 但 **PathFinder 失败时 SA 才有意义**：`pre_pedometer`（59 节点）PathFinder 9 档全超时，
  SA 在 II=6 就过了；`pre_nw`（60 节点）PathFinder 全超时，SA 在 II=16 过。
- 反过来 PathFinder 能过的图上 SA 明显更差：`pre_compare_neighb` 6→8、`pre_finalSAD` 10→12。

也就是说 SA 的价值不在"更快"，而在"PathFinder 卡死时的兜底"，代价是 II 更差。

## 5. 表 E：CP-SAT 放置 + 全局布线 + A* 回灌（预生成集）

`sweep_ilp_pre.sh`：以表 D 的 II 为固定 II，跑 `ilp_mapper.py`（45 s 预算）→
`ilp_router.py`（45 s）→ 把 pins 以 `--pins_mode 3` 灌给 mapper 的 A*。

| kernel | 节点 | II | CP-SAT 放置 | 耗时 | hops | regs | 全局布线 | A\* 吃否 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pb_pre_atax | 24 | 4 | FEASIBLE | 45.8 s | 14 | 9 | OPTIMAL | ✅ |
| pb_pre_bicg | 31 | 4 | FEASIBLE | 45.9 s | 27 | 17 | OPTIMAL | ✅ |
| pb_pre_cholesky | 26 | 4 | FEASIBLE | 46.5 s | 17 | 13 | OPTIMAL | ✅ |
| pb_pre_doitgen | 27 | 4 | FEASIBLE | 45.8 s | 13 | 14 | OPTIMAL | ✅ |
| pb_pre_2mm | 28 | 5 | FEASIBLE | 46.0 s | 17 | 16 | OPTIMAL | ❌ |
| pre_finalSAD | 38 | 10 | FEASIBLE | 46.5 s | 24 | 79 | OPTIMAL | ✅ |
| pre_compare_neighb | 46 | 6 | FEASIBLE | 50.3 s | 35 | 36 | OPTIMAL | ❌ |
| pre_gemm_nt | 52 | 8 | FEASIBLE | 46.7 s | 57 | 210 | OPTIMAL | ❌ |
| pre_pedometer | 59 | 6 | FEASIBLE | 46.5 s | 88 | 104 | OPTIMAL | ❌ |
| pre_nw | 60 | 16 | UNKNOWN | 48.4 s | — | — | — | — |

三条读数：

1. **放置只要解出，全局布线 9/9 OPTIMAL** —— 说明这些放置确实存在合法布线，不是"模型自嗨"。
2. **A\* 只吃下 9 个里的 4 个**，和图 D 的小图结论一致：mapper 的逐节点
   `estimateRouting` 是整条链路最弱的一环。
3. **`regs` 随图变大失控**（gemm_nt 210、pedometer 104）—— CP-SAT 的放置目标只有
   `10*hops + regs`，没有拥塞/端口意识，大图上会解出"hops 小但被寄存器串成糖葫芦"的放置。
   这是放置层必须"布通感知"的直接证据。

## 6. 修好的两个模型缺陷（本轮）

**(a) `--route-cycles` 默认值把长边卡死。** `ilp_mapper.py` 原来把 `d`（每条边路由吃几拍）
限制成全局常量 `{0,1}`，但长边（跨 3–8 跳）真实需要更多拍。头注释本来就写着上界是
`manhattan(u,v)+1`，代码没实现。改成按边计算 `delay_options(hh)`、默认 `-1`（auto）后：

```
gemm (49 节点, II=9):
  改前  INFEASIBLE
  改后  FEASIBLE  hops=128 route_cycles=205  wall=45.85s
```

补丁：`tools/patches/patch_routecycles.py`。

**(b) `NPB` 未初始化。** 见 §3。

## 7. 复现命令

```bash
# 0) 环境
bash scripts/build.sh                     # 三件套（含 npb 补丁后的重编）

# 1) 装预生成 DFG
bash scripts/import_prebuilt.sh

# 2) 表 D：PathFinder vs SA（II 阶梯）
INTS=... bash scripts/sweep_prebuilt.sh 45 0 1

# 3) 表 E：CP-SAT 放置 + 全局布线 + A* 回灌
bash scripts/sweep_ilp_pre.sh

# 4) 单个 case 手工走一遍
bash scripts/inject_mem.sh pb_pre_2mm
bash scripts/map.sh pb_pre_2mm 0
```

## 8. 已知限制

1. **预生成 DFG 没有 trace**，所以表 D/E 只能比 II 和布线可行性，**不能验证功能正确性**。
   要验仿真必须重新跑插桩程序，而预生成集没有对应可编译的 `.c`。
2. `cme_*` 10 个 case 不可用（DFG 缺 base pointer），补 pointer 名属于编造语义。
3. 4×4 阵列容量上限：>60 节点的图基本无解，这不是算法问题。要覆盖
   `microspeech_2/3`、`conv2d_3x3`、`gemm_w3` 这批 62–80 节点的图，必须上 8×8
   （`arch/hycube_8x8*` 上游有）。
4. 表 D 的 "⏱" 是 9 档 × 45 s = 405 s 预算用尽，不代表"无解"，只代表"这档预算内没搜到"。
