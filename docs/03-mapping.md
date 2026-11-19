# 03 · 映射（布局布线）

`out/cgra_xml_mapper` 是上游 `Morpher_CGRA_Mapper` 编出来的。它做的事：
读 DFG + 硬件描述，把每个算子分配到 (PE, 相位)，把每条边布成路由，输出配置包。

## 1. 命令行

```bash
out/cgra_xml_mapper -d dfg.xml -j arch.json -x 4 -y 4 -i 0 -t HyCUBE_4REG -m 0
```

| 参数 | 含义 |
|---|---|
| `-d` | DFG XML 路径 |
| `-j` | ADL（硬件描述）路径 |
| `-x` / `-y` | 阵列列数 / 行数。**必须与 ADL 的 `DIMS` 一致，写错不报错** |
| `-i` | 用户指定的初始 II；0 表示由 mapper 自己算 |
| `-t` | PE 类型，本项目固定 `HyCUBE_4REG` |
| `-m` | 映射算法，见下表 |
| `--maxiter` | PathFinder 的最大迭代轮数 |

`-m` 的取值（`src/mapper/src/CGRA_xml_compiler.cpp:87`）：

| `-m` | 算法 | 状态 |
|---|---|---|
| `0` | `PathFinderMapper` | 默认，Morpher 论文用的算法 |
| `1` | `SAMapper`（模拟退火）| 可用，但见 §4 |
| `2` | `LISAMapper` | **不可用**：需要 `--arch_name` 和预训练的 GNN 模型，仓库里没有训练产物 |
| 其它 | `assert(false && "did not set a valid mapping method")` | 直接断言退出 |

注意：`-m 3`（LLM 放置）**在本工程里不存在**——那是另一条工作线（`~/cac`）在
`morpher-src` 上加的补丁，不属于上游。

## 2. II 是怎么定的

```
ResMinII = 资源下界（PE 数、op 数决定）
RecMinII = 递推下界（环路上的总延迟 / 环路边数）
II = max(RecMinII, ResMinII, 用户给的 -i)
```

然后 mapper 从 `II` 开始试，失败就 `II++`，直到成功（或撞上 `--maxiter`）。
所以日志里的 `Map Success with II = 4` 是**最终**的 II，不一定等于你传的 `-i`。

我们的三个 kernel 都在 `II = 4` 成功了，其中 `RecMinII = 4` 是主导项
（循环里有 4 拍的递推链）。想看到 II 被抬高的例子，传 `-i 1` 即可。

## 3. 产物（`out/<k>/m0/`）

文件名前缀是 `<dfg文件名>` 直接拼 `<arch文件名>`，看着很长，但含义简单：

| 文件 | 内容 | 怎么用 |
|---|---|---|
| `*.bin` | 配置包（bitstream）| 仿真器的 `-c` 参数 |
| `*.mapping.csv` | 逐节点决策日志：每个节点尝试时还剩几个未映射节点、几次迭代 | 排查"为什么这个节点没放下去" |
| `*.routeInfo.log` | **完整路由**：每个节点落在哪个 PE 的哪一拍，每条边的每一跳经过哪个 PE 的哪个端口 | 唯一能看清布线的地方 |
| `*.congestion.info` | 每轮迭代的拥塞/冲突统计 | 判断失败是拥塞还是结构不可行 |
| `result.txt` | 追加一行的汇总：`<dfg> method:<名> <II> <lat>` | 批次实验的汇总来源 |
| `map.log` | stdout（含 `Map Success with II = ...`、耗时）| `map.sh` 的判定依据 |

`*.mappingwithlatency.txt` 有时是空的（只有部分路径会写），不要拿它统计节点数——
数 `*.mapping.csv` 里 `current node =` 的行数更可靠。

`routeInfo.log` 长这样（截自 array_add）：

```
Map Success!.
--------------------------------------------------------
node=11,mapped=PE_MEM_X0_Y2-T0
node=12,mapped=PE_X1|_Y2|-T2
	parent:11
		CGRA_Ins.PE_MEM_X0_Y2-T2.FU0.DP0_T
		CGRA_Ins.PE_MEM_X0_Y2-T2.EAST_O
		CGRA_Ins.PE_X1|_Y2|-T2.WEST_I
		CGRA_Ins.PE_X1|_Y2|-T2.WEST_XBARI
		CGRA_Ins.PE_X1|_Y2|-T2.FU0.DP0_P
```

读法：节点 11（LOADB）放在 `PE_MEM(0,2)` 的第 0 拍，节点 12（CMERGE）放在 `PE(1,2)` 的第 2 拍；
从 11 到 12 的路由依次经过 `PE_MEM(0,2)` 的输出寄存器 → 东向端口 → `PE(1,2)` 的西向输入
→ 西向交叉开关 → FU 的第二操作数。中间那些 `T2` 是相位，跳数的累加就体现在这里。

## 4. SA 为什么和 PathFinder 结果一模一样

`SAMapper` 继承自 `PathFinderMapper`，`SAMap()` 的第一件事是：

```cpp
initMap_with_PathFinder();
if (已经拿到合法解) return;   // ← 直接返回，根本进不了退火循环
```

所以只要 PathFinder 一次就能成（我们的三个 kernel 都是），**SA 从未真正退火**，
两者输出的 II 和 lat 逐项相同，只是 SA 因为多做了一次拷贝而略慢。
这不是我们改的，是上游实现如此（`include/morpher/mapper/SimulatedAnnealingMapper.h:111`、
`src/mapper/src/SimulatedAnnealingMapper.cpp` 的 `SAMap`）。

真要让 SA 起作用，得让它跳过那个提前返回，或者在它外面做多起点。

## 5. 如果要做"agent 加速映射算法"，切口在哪

SA 的内部结构（同一份源码）：

```cpp
while (curr_temp > minimim_temp) {          // 默认 200 → 10
  for (i = 0; i < movement_in_each_temp; i++) {   // 默认 100 次移动
    selectDFGNodeToUnmap();      // ① 选哪个节点撤下来（现在是无脑/随机）
    clearNodeMapping();
    getRandomDPCandidate(node);  // ② 放到哪（现在取 candidates.front()）
    SARoute();                   // ③ 能不能布通
    whetherAcceptNewMapping(new_cost, old_cost, curr_temp, accepted, tried);  // ④ 接不接受
    // 失败则 restoreMapping()
  }
  curr_temp = updateTemperature(curr_temp, accept_rate);
}
getCost() = unmapped_node_number*200 + congestion_number*5 + conflict_number*5 + port_usage;
```

五个可插入点，按预期收益排序：

| 位置 | 现在怎么做 | 可以怎么做 |
|---|---|---|
| ① `selectDFGNodeToUnmap` | 随机/朴素选择 | 按关键路径长度、拥塞贡献、失败历史打分再选 |
| ② `getRandomDPCandidate` | 取候选队列第一个 | 按曼哈顿距离、bank 亲和、当前占用排序 |
| ③ `whetherAcceptNewMapping` | 只看温度与代价差 | 加禁忌表 / 模拟退火+大邻域（LNS）|
| ④ 初始化 | 一次 PathFinder 热启动 | 多起点、不同 II 各热启动一次 |
| ⑤ `getCost` | 四项固定权重 200/5/5/1 | 先测四项实际分布再定权重（现在 200:1 的比例可能是拍的）|

三种接入形态：

- **(a) 加虚函数钩子 + 外部提示文件**（推荐）：在 `SAMapper` 的四个位置加 `virtual`，
  默认实现保持原样；加一个 `--sa-hint hints.json` 读外部打分/建议。改动约 30 行，
  SA 主体逻辑零改动，也便于做 A/B 对照。
- **(b) 完全外置**：不碰 C++，只在外层跑多个不同随机种子的 SA，取最好。
  这只提高稳定性，不提高单次效率。
- **(c) 重写 mapper**：把 SA 重构成库函数、由外部循环驱动。收益最大、成本也最大，
  并且会失去与上游的可比性。

## 6. 已知限制

1. **只能 HyCUBE 形状**：`printHyCUBEBinary` 硬编码了每 PE 一个 `FU0`/`DP0` 和 HyCUBE 的
   端口命名（`NORTH_O`…）。换架构要改 C++。
2. **不扫 II 的算法**：`-m 2`（LISA）拿到解就停，不做 II 递减搜索；`-m 3` 在上游也不存在。
   只有 `-m 0/-m 1` 走完整的 "II++ 重试" 循环。
3. **同一份 DFG 只有一份最左列**：x=0 全是 `PE_MEM`，所以所有访存算子都在争抢 4 个位置，
   访存密集的 kernel 会先在资源上卡住，而不是在布线。
