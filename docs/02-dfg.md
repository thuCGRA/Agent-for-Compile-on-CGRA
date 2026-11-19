# 02 · 数据流图（DFG）与配套的两个小文件

`gen_dfg.sh` 一次产出三样东西，都在 `out/<kernel>/`：

| 文件 | 内容 |
|---|---|
| `dfg.xml` | 数据流图本体（mapper 的输入）|
| `mem_alloc.txt` | 变量 → 基址（`update_mem_alloc.py` 与仿真器的输入）|
| `*.dot` | 同一张图的 graphviz 版本（纯给人看，`dot` 没装也不影响）|

## 1. DFG XML 的结构

```xml
<MutexBB>
  <BB1 name="_0_0"><BB2 name="_0_1"/></BB1>
  <BB1 name="_0_1"><BB2 name="_0_0"/></BB1>
</MutexBB>
<DFG count="20">
<Node idx="11" ASAP="0" ALAP="0"BB=""CONST="4094">
  <OP>LOADB</OP>
  <BasePointerName size="1">loopstart</BasePointerName>
  <Inputs>
  </Inputs>
  <Outputs>
    <Output idx="12" nextiter="0" NPB="0" type="P"/>
    <Output idx="19" nextiter="0" NPB="0" type="P"/>
  </Outputs>
  <RecParents>
  </RecParents>
</Node>
...
</DFG>
```

### `<Node>` 属性

| 属性 | 含义 |
|---|---|
| `idx` | 节点编号，也就是 DFG 里的节点 ID |
| `ASAP` | 最早可放置的相位（as-soon-as-possible 时间槽）|
| `ALAP` | 最晚可放置的相位（as-late-as-possible）|
| `BB` | 所属基本块；空串表示跨块/循环外 |
| `CONST` | 常量操作数的值（只有常量节点或带常量输入的节点才有意义）|

`ASAP`/`ALAP` 是在**未定 II** 时算出来的相对时间槽，只能当参考；真正的相位是
mapper 在给定 II 下重新算的（相邻节点的约束是模 II 的等式，不是这个绝对值）。

### 子元素

| 元素 | 含义 |
|---|---|
| `<OP>` | 操作名，必须在 `FU.OPS` 或 `FU_MEM.OPS` 里（见 `docs/01-architecture.md` §4）|
| `<BasePointerName size="N">变量名</BasePointerName>` | 访存类节点的基址指针：变量名 + 字节数 |
| `<Inputs><Input idx="k"/>` | 数据依赖：本节点的输入来自节点 k |
| `<Outputs><Output idx nextiter NPB type/>` | 反向登记：本节点的结果给了谁。`type="P"` 普通边，`type="I1"` 迭代间边；`nextiter="1"` 表示跨迭代；`NPB` 与跨块标记相关 |
| `<RecParents>` | 递归依赖（本项目用到的三个 kernel 里都是空）|

### 两个必须小心的实现细节

1. **属性之间偶尔缺空格**：`ALAP="0"BB=""CONST="4094"` —— 这不是笔误，是上游 pass 的
   输出就这样。用标准 XML parser 解析会失败（属性会被当成一个畸形 token），
   所以任何读 DFG 的代码都应该用正则按属性名抓取，例如
   `idx="(\d+)"`、`ASAP="(-?\d+)"`、`CONST="(-?\d+)"`。
2. **`CONST=` 出现的节点不一定是常量节点**：`LOADB` 的 `CONST="4094"` 是它的地址偏移，
   不是"这个节点算常量"。判断常量节点要看 `<OP>` 是不是 `MOVC`/`MOVCL` 之类。

## 2. `mem_alloc.txt`

```
var_name,base_addr
B,2048
A,0
C,80
loopend,2047
loopstart,4094
```

- 普通变量：名字 + 基址（单位：**字**，不是字节）。上例 `A` 在 0、`C` 在 80，
  说明 `A` 占了 80 个字 ≈ 20 个 int，对得上 `#define SIZE 20`。
- `loopstart` / `loopend`：pass 自己加的哨兵变量，仿真器靠它们判断循环边界
  （`loopstart` 的地址 4094 就是 4096 字存储的倒数第二个字）。
- 这个文件同时被三处使用：`update_mem_alloc.py`（决定 bank 划分）、
  `sim.sh` 的 `-a` 参数（告诉仿真器每个变量的基址）、`gen_trace.sh`（trace 里的变量名靠它对齐）。

## 3. `trace_0.txt`（仿真器的激励）

```
var_name,offset,pre-run-data,post-run-data
A,0,5,5
A,1,0,0
A,2,0,0
A,4,7,7
```

| 列 | 含义 |
|---|---|
| `var_name` | 变量名（与 `mem_alloc.txt` 对应）|
| `offset` | 相对该变量基址的偏移（字）|
| `pre-run-data` | 程序跑**之前**这个位置的值 → 仿真器的 DMEM 初值 |
| `post-run-data` | 程序跑**之后**的值 → 仿真器的参考输出 |

它不是"记录程序执行过程"，而是"记录程序执行前后的内存快照差分"。所以仿真器的
判定非常直接：跑完整个程序后，凡是 trace 里列出的每个 (变量, 偏移)，DMEM 里对应位置
的值必须等于 `post-run-data`，全等就 `Mismatches::0`，否则那一条就是 mismatch。

## 4. trace 是怎么生成的（以及为什么不能照抄上游）

原理：DFG pass 除了生成 `dfg.xml`，还会把每个内存访问替换成对
`src/dfggen/src/instrumentation/instrumentation.cpp` 里桩函数的调用；把这个插桩过的
IR 编译成可执行文件、在 host 上**真跑一遍**程序，桩函数就会把每次访存前后的值写进
`memtraces/*.txt`。

上游的做法是"kernel IR + instrumentation.cpp 都编成 IR，再 `llvm-link` 起来"。这条路在本机
走不通，原因有两个：

1. DFG 用的是 `-target i386-unknown-linux-gnu`（为了 DFG 里指针是 32 位），而 conda 的
   clang sysroot 只带 64 位 glibc 头 —— 连 `gnu/stubs-32.h` 都没有，更不用说 32 位 C++ 标准库；
2. `llvm-link` 要求两个模块的 target triple 一致，而 instrumentation.cpp 只能用 host 编译器编。

我们改成（`scripts/gen_trace.sh`）：

```
用 native（不加 -target）重新编译 kernel.c → 同一个 pass 插桩 → llc 降成 .o
instrumentation.cpp 用 host GCC 7.5 编成 .o
两个 .o 在 object 层链接 → ./final → 跑 → memtraces/*.txt
```

trace 里只有 4 字节数据值，与 host 字宽无关，所以 native 跑出来的 trace 对仿真器等价。
插桩产物放在 `out/<k>/trace_build/`，**不会**覆盖 `out/<k>/dfg.xml`（DFG 必须来自 i386 那条路）。

`toolchain/include32/gnu/stubs-32.h` 是这个问题的另一半解药：一个空壳头
（内容只有 `#define __stub_dummy 0`），让 i386 那一步的 `#include <gnu/stubs-32.h>` 能过。
`gen_dfg.sh` 用 `-isystem toolchain/include32` 把它排在 sysroot 前面。
