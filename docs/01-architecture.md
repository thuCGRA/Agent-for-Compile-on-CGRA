# 01 · 硬件描述（ADL）长什么样

`arch/*.json` 就是这个编译器的"硬件描述"。mapper 读它来建 CGRA 模型，仿真器不直接读它
（仿真器只吃 bitstream，硬件形状在 bitstream 的头部）。

## 1. 一个 ADL 文件的骨架

```json
{ "ARCH": {
    "DP":      {...},          // 数据通路单元（算术前后的寄存器堆/MUX）
    "FU":      {...},          // ALU：支持哪些 op、每个 op 几拍
    "FU_MEM":  {...},          // 访存单元：LOAD/STORE 那些 op
    "PE":      {...},          // 一个处理单元 = 4 个方向的交叉开关 + FU + 5 个寄存器
    "PE_MEM":  {...},          // 靠存储器那一列的 PE（里面装 FU_MEM）
    "CGRA":    {...},          // 阵列：几行几列、哪个坐标放 PE / PE_MEM、PE 之间怎么连
    "SPM_B0_WRAPPER": {...},   // 第 0 个 bank（这个键是后注入的，见 §5）
    "SPM_B1_WRAPPER": {...}
} }
```

注意外层有 `ARCH` 包装。`arch/hycube_4x4_updatemem.json` 里还有 `MDP` 和 `SPM` 两个
模板模块，是 `update_mem_alloc.py` 展开 `SPM_B*_WRAPPER` 时用的原型。

## 2. 每个模块的通用字段

| 字段 | 含义 |
|---|---|
| `INPUTS` | 输入端口名列表，例如 `["NORTH_I","WEST_I","EAST_I","SOUTH_I"]` |
| `OUTPUTS` | 输出端口名列表，例如 `["NORTH_O","WEST_O","EAST_O","SOUTH_O"]` |
| `REGS` | 模块内部寄存器名（`PE` 有 `NR/ER/WR/SR/TREG`）|
| `INTERNALS` | 内部子对象（`PE` 里是 4 个 `XBARI` 交叉开关）|
| `SUBMODS` | 子模块实例，`{"FU":[{"name":"FU0"}]}` 表示装了 1 个 FU |
| `CONNECTIONS` | **端口到端口的连线**，`{"THIS.NORTH_I": ["THIS.NR","THIS.NORTH_XBARI"]}` 表示北输入同时接到寄存器和交叉开关 |
| `OPS` | 只有 FU/FU_MEM 有：`op → latency` 表 |

`PE.CONNECTIONS` 里那条"输入同时接 `NR` 寄存器和 `NORTH_XBARI` 交叉开关"很关键：
它说明 HyCUBE 的 PE 既有**寄存一拍**的路径，也有**同拍直通**的路径。这就是为什么
在 HyCUBE 上"两个算子放同一拍、距离为 0"是合法的——很多 CGRA 不允许，HyCUBE 允许。

## 3. `CGRA` 是阵列，且连线是"模板"

```json
"CGRA": { "SUBMODS": [ {
  "PATTERN": "GRID",
  "DIMS":  {"X": 4, "Y": 4},
  "MODS":  [ {"X":0,"Y":0,"MOD":"PE_MEM","name":"PE_MEM_X0_Y0"},
             {"X":1,"Y":0,"MOD":"PE"},
             ... 共 16 项 ... ],
  "CONNECTIONS": [
    {"FROM_X":"X","FROM_Y":"Y","FROM_PORT":"NORTH_O","TO_X":"X",  "TO_Y":"Y-1","TO_PORT":"SOUTH_I"},
    {"FROM_X":"X","FROM_Y":"Y","FROM_PORT":"EAST_O", "TO_X":"X+1","TO_Y":"Y",  "TO_PORT":"WEST_I"},
    {"FROM_X":"X","FROM_Y":"Y","FROM_PORT":"WEST_O", "TO_X":"X-1","TO_Y":"Y",  "TO_PORT":"EAST_I"},
    {"FROM_X":"X","FROM_Y":"Y","FROM_PORT":"SOUTH_O","TO_X":"X",  "TO_Y":"Y+1","TO_PORT":"NORTH_I"}
  ] } ] }
```

两个必须知道的点：

1. **`CONNECTIONS` 只有 4 条模板，不是 48 条边。** `FROM_X`/`TO_Y` 里写的是符号表达式
   （`"X"`, `"Y-1"`, `"X+1"`），要按 `DIMS` 把 16 个坐标逐个代入、丢掉越界和自环，
   才得到真正的有向链路表（4x4 网格 → 48 条）。自己做工具时必须展开，
   别把它当成现成的边表读。
2. **x = 0 整列是 `PE_MEM`，x ≥ 1 是普通 `PE`。** 所以"访存算子只能放最左列"、
   "算术算子最好别放最左列"这两条约束来自这里，不是来自算法。

## 4. 算子与延迟

- `FU.OPS`：28 项，`NOP ADD SUB MUL SEXT DIV LS RS ARS AND OR XOR SELECT CMERGE CMP CLT BR
  CGT MOVCL OLOADCL OLOAD OLOADH OLOADB OSTORE OSTOREH OSTOREB JUMPL MOVC`，**全部 latency = 1**。
- `FU_MEM.OPS`：35 项，**全部 latency = 2**（额外一拍是访存本身的开销）。

`SELECT` 和 `CMERGE` 是两输入的选择类算子，布线时要求两个父节点**不同拍**到达
（同一拍拿不到两个操作数），这是 mapper 里最容易触发断言的地方。

## 5. bank 是后注入的，不是原始 ADL 的一部分

上游 `json_arch/hycube_original.json` 里**没有** `SPM_B0_WRAPPER`。它由
`src/mapper/update_mem_alloc.py` 按**具体 kernel 的变量地址表**生成：

```
python3 src/mapper/update_mem_alloc.py \
        arch/hycube_4x4_updatemem.json  out/<k>/mem_alloc.txt 2048 2  out/<k>/arch.json
        #     模板 ADL                    变量基址表             bank 容量  bank 数  输出
```

生成后每个 bank 长这样：

```json
"SPM_B0_WRAPPER": {
  "TSOCKETS": ["MEMPORT_P0","MEMPORT_P1"],          // 2 个访存端口
  "SUBMODS": {"SPM": [{"name":"SPM0","DATA_LAYOUT":{"A":128,"B":0,"loopend":2047}}]},
  "INIT_FILE": "...",
  "CONNECTIONS": {"SPM0.PORT0":"THIS.MEMPORT_P0","SPM0.PORT1":"THIS.MEMPORT_P1"}
}
```

`DATA_LAYOUT` 是"变量 → 起始地址"（单位：字）。它就是 `mem_alloc.txt` 加 bank 划分后的结果。

**已知缺口**：`update_mem_alloc.py` 只写 `DATA_LAYOUT`，**不写 PE → bank 的连线**，
所以"哪个 memory PE 能访问哪个 bank"在 ADL 层面没有权威描述（`arch/*.json` 的
`CGRA.CONNECTIONS` 里没有 `PE_MEM_* → SPM_B*` 的条目）。现在的实际约定是隐式的：
`SPM_B<i>` 由第 i 行（第 i 个 y）的 `PE_MEM` 独占。做资源检查工具时必须知道这一点，
否则会以为"所有 PE 都能访问所有 bank"。

## 6. 这个 ADL 表达不了什么

上游 `json_arch/thu_pea8x8.json` 顶部注释自己列了四条（我们只用 4x4，但限制同样适用）：

1. 阵列级共享寄存器堆（array-wide shared GR）；
2. 位置相关的多跳路由码（position-dependent multi-hop route codes）；
3. 配置包（config package）模型；
4. 4 级流水 / 多周期 EX。

另外我们发现的两条：

5. **`OPS` 名字集合是硬约束**：`Route`/`Sel`/`Clz`/`Equal` 这些名字不在 HyCUBE 的 op 集里，
   从别的 CGRA 抄 DFG 过来会直接映射失败；
6. **`printHyCUBEBinary` 硬编码 FU0/DP0 和 HyCUBE 端口名**，换架构要改 C++ 而不是改 JSON。

## 7. 想换架构怎么办

1. 复制一份 `arch/hycube_4x4.json`，改 `DIMS`/`MODS`/`CONNECTIONS`；
2. `map.sh` 的 `-x/-y` 必须和 `DIMS` 一致（这两个参数是分开传的，写错了不会报错，只会算错）；
3. 大于 4x4 或非 HyCUBE 形状，先确认 `src/mapper/src/PathFinderMapper.cpp` 里
   `printHyCUBEBinary` 能处理——它目前只认 HyCUBE 的端口命名和每 PE 一个 FU0/DP0。
