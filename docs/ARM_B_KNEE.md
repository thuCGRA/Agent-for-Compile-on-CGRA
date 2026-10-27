# 拥塞膝点：先看 A* 在哪里"失败"

结论先放这儿：**在这个设计空间里，A* 的失败率不是可用的拥塞旋钮**。用架构尺寸当旋钮时，
A* 在 4x4 和 3x3 上把能造出来的实例全部映射成功（10/10），2x2 上也只有 80%，而且那两例
失败是**超时**（7 分钟没收敛），不是"证明不可映射"。拥塞真正表现为**II 膨胀和耗时爆炸**，
不是失败。这直接改变了消融该测什么。

## 怎么造的实例（可复现）

1. **真实 kernel**：`toolchain/vendor/dfggen` 的 dfggen pass 可以从 C 源码直接生成
   `*_PartPredDFG.xml`（`opt -load toolchain/out/libdfggenPass.so -fn <kernel> -nobanks 2
   -banksize 2048 -skeleton` 这一步就会把 XML + mem_alloc 写出来，不需要链接、不需要跑二进制）。
   成功生成：`array_add` 20 节点、`array_cond` 25、`trmm` 11。
   - 失败：`aes`（clang 阶段失败）、`gemm`（pass 内部 abort）、`fft`（源文件是 `fix_fft.c`，
     `-fn` 名字对不上）、`hpcg`（XML 生成出来了，但 mapper 加载后立刻崩，BB 分配不完整）。
     这四个不进表，脚本 `cac_scripts/armB/gen_all.sh`。
2. **架构尺寸**：4x4 用现成的 `work/armB/spike/array_add_mem.json`；3x3 / 3x2 / 2x2 用
   `cac_scripts/armB/make_arch.py` 从它派生（GRID 模式 + MODS 子集，**必须删掉显式的
   CGRA CONNECTIONS**，否则 `ParseCGRA` 的 `dest_mod` 断言）。派生出来的 3x3 上
   array_add 仍能映射（II=4），说明派生的架构是可用的，不是坏 json。
3. **密度**：真实 kernel 太小（11–25 节点），任何尺寸都装得下。所以复制 k 份做密度阶梯
   （`sweep3.py`，文本级复制：idx 偏移、BB 名加 `_cN` 后缀、每份带自己的 MutexBB）。

口径：`-m 0`、II 自由（mapper 从 `max(ResMin,RecMin)` 起逐级抬 II）、超时 420s。

## 结果：密度 x 尺寸（k=0，纯 A*）

| 实例 | 节点 | 4x4 | 3x3 | 2x2 |
|---|---|---|---|---|
| array_add x1 | 20 | II=4 (1.2s) | II=4 (0.3s) | II=5 (0.1s) |
| array_add x2 | 40 | II=4 (1.5s) | II=5 (0.6s) | II=10 (0.8s) |
| array_add x3 | 60 | II=6 (89.2s) | II=7 (1.6s) | II=17 (76.1s) |
| array_add x4 | 80 | II=7 (224.1s) | II=9 (75.5s) | **timeout (II>23)** |
| array_cond x1 | 25 | II=4 (1.8s) | II=4 (0.4s) | II=7 (3.0s) |
| array_cond x2 | 50 | II=4 (4.8s) | II=6 (19.8s) | II=13 (2.4s) |
| array_cond x3 | 75 | II=6 (141.7s) | II=10 (144.8s) | **timeout (II>21)** |
| trmm x2 | 22 | II=4 (1.1s) | II=4 (0.3s) | II=7 (0.2s) |
| trmm x3 | 33 | II=4 (2.6s) | II=4 (0.8s) | II=9 (0.3s) |
| trmm x5 | 55 | II=4 (63.4s) | II=7 (3.2s) | II=14 (18.4s) |

成功率：4x4 = 10/10，3x3 = 10/10，2x2 = 8/10（全是超时）。

## 三条结论

1. **尺寸不是 20%–80% 那个旋钮**。筛不出来的原因不是实例不够，而是 A* 有 II 搜索：
   装不下就抬 II（4x4 从 4 抬到 7，2x2 从 5 抬到 17），于是"可映射"几乎恒真。
   要让 A* 真的失败，得**钉住 II**（这也是消融里布局固定 + II 固定的做法）。
2. **拥塞的真实签名是 II 和耗时**：节点数 20→80 时 4x4 的耗时 1.2s→224s（约 190 倍），
   II 4→7；3x3 在 75 节点时 145s。这个区间才是"布线算法值不值钱"的战场。
3. **2x2 的 80% 不算膝点**：那两例是 420s 超时，换个超时会变，属于"不可判定"，
   写进论文要单列，不能算 A* 的失败率。

## 对消融的直接影响（提议，等确认）

既然 A* 不是"失败"而是"变慢、抬 II"，覆盖率消融的因变量应该换成：

- **主指标**：mapper 耗时（同布局同 II，k=0 vs 接管 k%）——这是有 190 倍动态范围的那个量。
- **次指标**：verify 轮数、`suggest_edges` 条数、首轮冲突数（我的 flex 通道已经全都记了）。
- II/lat 在这一层基本不变（布局固定），继续如实报但不再是主角。
- 前提是选一档"慢但能映射"的实例：**4x4 + 60~75 节点**（89–142s）或 **3x3 + 75 节点**
  （145s）。这些档位上 k% 接管能否把耗时压下来，才是可测的问题。

**一个硬约束**：仿真验证需要 per-kernel 的 trace，而复制出来的实例没有 trace。
所以"能仿真的膝点档"只能是**真实 kernel**（A 的 9 个 kernel + trace）。目前我这边的
`work/traces/` 只有 `array_add` 一个，其余等 A 落盘。在那之前，膝点档的仿真结论只能
用 array_add 一条；复制实例只能做"映射层"的耗时/II 结论（verify 仍然有效，仿真不行）。

## 附：本次新增的可复用件

- `cac_scripts/armB/gen_all.sh`：C → PartPred DFG XML（只跑 pass，不链接）。
- `cac_scripts/armB/make_arch.py`：从 4x4 arch json 派生 NxM（关键：删 CGRA CONNECTIONS）。
- `cac_scripts/armB/sweep2.py` / `sweep3.py`：kernel×size、密度×尺寸 两套扫描，输出 csv+md。
- 产物：`work/armB/dfggen/<kernel>/`、`work/armB/archs/hy_<X>x<Y>.json`、
  `work/armB/sweep2/`、`work/armB/sweep3/`。
