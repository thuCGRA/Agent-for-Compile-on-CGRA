# 04 · 仿真器怎么用

`out/hycube_simulator` 是一个周期级（cycle-accurate）的 HyCUBE 模拟器。它吃配置包，
按拍执行，最后把 DMEM 里"应该关心的那些位置"和参考数据比对，打印一行结论。

## 1. 命令行

```bash
out/hycube_simulator -c <bitstream.bin> -d <trace_0.txt> -a <mem_alloc.txt> \
                     -m 4096 -x 4 -y 4 -t 1
```

| 参数 | 含义 | 本项目取值 |
|---|---|---|
| `-c` | 配置包（mapper 产出的 `*.bin`）| `out/<k>/m0/*.bin` |
| `-d` | 数据 trace（程序前后内存快照，见 `docs/02-dfg.md` §3）| `out/<k>/trace_0.txt` |
| `-a` | 变量基址表 `mem_alloc.txt` | `out/<k>/mem_alloc.txt` |
| `-m` | DMEM 容量（**字**，不是字节）| `4096` |
| `-x` `-y` | 阵列列数、行数，必须与 mapper 用的相同 | `4 4` |
| `-t` | bank 位置模式：1 = bank 在左侧，2 = 两侧 | `1` |

缺 `-c` 或 `-d` 会直接 `parseDMEM` 断言失败（`CGRA.cpp:190 Assertion 'dmemfile.is_open() && "Error opening dmem file"'`），
退出码 134。这是最常见的"仿真崩了"，先检查路径而不是先怀疑映射。

## 2. 它是怎么知道程序跑完了的

仿真器不认"执行了多少条指令"，而是**轮询哨兵**：

```cpp
while (cgraInstance.dmem[MEM_SIZE/2-1] == 0) { /* 一直跑 */ }
```

`MEM_SIZE/2-1` = 4096/2-1 = **2047**，正好是 `mem_alloc.txt` 里 `loopend` 变量的地址。
DFG 的 pass 会把循环结束写成对 `loopend` 的写，仿真器看到这个位置被写成非 0，
再跑一段固定的收尾拍数（20 拍），然后开始比对。

所以：**改 `-m` 会同时改变哨兵地址**，必须和生成 `mem_alloc.txt` 时用的 bank 大小匹配，
否则仿真器永远等不到哨兵、死循环。`mem_alloc.txt` 里 `loopend,2047` 与 `-m 4096` 是配套的。

## 3. 输出与判定

```
Simulation Result: Matches::243, Mismatches::0
```

- `Matches`：trace 里列出的每个 (变量, 偏移) 中，DMEM 最终值等于 `post-run-data` 的条数；
- `Mismatches`：不相等的条数。
- **PASS 的条件是 `Mismatches::0` 且 `Matches::0` 不算通过**（后者说明 trace 没被真正加载，
  是"什么都没检查"的假通过）。

`scripts/sim.sh` 的判定就是这两条：

```bash
grep -q "Mismatches::0" sim_result.txt && ! grep -q "Matches::0," sim_result.txt
```

实测结果：

| kernel | Matches | Mismatches | 结论 |
|---|---|---|---|
| array_add | 243 | 0 | PASS |
| mac | 167 | 0 | PASS |
| trmm | 5 | 0 | PASS |

`trmm` 只有 5 条，是因为它的 trace 只在被映射的那段循环里记了 5 个数据；数量少不等于覆盖好，
看 `Matches` 的绝对值时要结合 kernel 的数据规模。

## 4. 出问题时怎么调

打开逐周期指令流：

```bash
SIMULATOR=1 out/hycube_simulator -c ... -d ... -a ... -m 4096 -x 4 -y 4 -t 1
```

`debug.h:37` 里 `#define LOG(S) if (getenv(#S))` —— 环境变量名就是 `SIMULATOR`，
值不重要（只要非空）。打开后会打印每个 (T,Y,X) 拍上实际执行的 op、DMEM 读写、
以及"关心的地址"清单。

常见症状与原因：

| 症状 | 大概率原因 |
|---|---|
| `parseDMEM` 断言，退出码 134 | `-d` 路径不存在，或 trace 文件名不对 |
| 卡住不动、CPU 100% | `-m` 与 `mem_alloc.txt` 的 `loopend` 地址不匹配（哨兵永远等不到） |
| `CGRATile.cpp:175` 断言（`received both operands in the same cycle`）| `SELECT`/`CMERGE` 的两个父节点被放到了同一拍、同一 PE 的两条输入路径上。这是**映射**的问题，不是仿真器的 |
| `CGRATile.cpp:352/358/364` 断言（输入寄存器被覆盖）| 同一个 PE 在同一拍被要求接两个不同来源的数据 |
| `CGRA.cpp:127` 附近 XB 配置断言 | bitstream 里的交叉开关配置与 ADL 端口名不匹配（多半用了非 HyCUBE 的 ADL） |
| `Matches::0, Mismatches::0` | trace 没加载成功（变量名对不上 `mem_alloc.txt`） |

一条经验：**仿真器报的错几乎都在映射的产物里，而不在仿真器自身**。它只是第一个
把"这个配置包在硬件上根本跑不通"暴露出来的地方。

## 5. 它和 ADL 的关系

仿真器**不读 ADL**。硬件形状（几行几列、端口怎么连）已经在 mapper 生成 bitstream 时
被"编译进"配置包了，仿真器只按 HyCUBE 的固定结构解释这个包。所以：

- ADL 写错 → mapper 按错误的硬件布线 → 生成一个"看起来合法但硬件上不成立"的包 →
  仿真器要么断言、要么给出 mismatch；
- 想验证"ADL 与真实硬件是否一致"，唯一的手段是仿真器（它是本项目里唯一的 oracle）。
