# M1：约束接口（agent 说"怎么放"，后端负责"放得下"）

M0 的结论是：**"给 SA 更多预算"这条最容易想到的路走不通**——预算 200→1000→4000→跑满，
array_add 的 cost 是 36.0 / **41.0** / 36.0 / 35.0，耗时从 6.5 s 涨到 245 s。
原因不是搜索不够，是**搜索的目标函数不是我们的目标**：mapper 的 `getCost()` 只算
`unmapped*100 + congestion*10 + conflict*10 + port_usage`，它关心"能不能映射"，
不关心"映射得好不好"（绕行、跳数、SELECT 同拍对）。

所以 M1 不是"给后端更多算力"，而是**给后端更多信息**：让 agent 用策略级的动作
改后端的搜索空间，而不是自己吐坐标。

## 交付物

### C++ 侧：`--constraints <json>`（`toolchain/patches/0003-mapper-constraints.patch`）

新增 `src/mapper/include/morpher/mapper/Constraints.h`，两个语义：

| 字段 | 含义 |
|---|---|
| `pins` | `{"11": [2, 1]}` 节点 11 必须落在 PE(2,1)，相位自由 |
| `forbidden` | `{"0": [[1,0],[1,1]]}` 节点 0 不许落在这些格 |

- 挂在 `PathFinderMapper` 上（`public Constraints constraints;`），SA 继承它，两个后端共用语义。
- 过滤点选在 `estimateRouting()` 里**候选集生成之后、`getLatCandDests()` 之前**——
  放在之后的话，后面按 `minLat` 做的裁剪会作用在未过滤的集合上，pin 会被"裁剪掉"。
- SA 的退火邻域（`getRandomDPCandidate()`）也必须过滤。只在 `initMap()` 里过滤是不够的：
  热启动一旦可行就早退返回，约束"看起来生效"；热启动不可行时走退火，那条路完全没约束。
- 只支持 `pin_mode: "strict"`。没有 `prefer`：候选集后面进的是 `priority_queue`，
  顺序注入不生效；`dpPenaltyMap` 在 `PathFinderMapper.cpp` 里只写不读（死代码）。
  **宁可少一个模式，不留假功能。**

### Python 侧：`Hints.to_constraints_json()`

`backends/base.py` 的 `Hints(pins, forbidden, region, budget, note, seed)` 新增
`to_constraints_json()`，输出 `{"pin_mode":"strict","pins":{...},"forbidden":{...}}`，
空约束返回 `None`（不传 `--constraints`）。

**pins 里的相位 t 被主动丢掉**。理由：相位是 mapper 从数据到达时间推出来的**结果**，
钉住它等于砍掉一半搜索空间。旧工程实测：全 pin（含 t）2/10 可映射，只给 (x,y) 7/10。

### 随机性必须钉住：`--sa_seed`（`toolchain/patches/0004-sa-seed.patch`）

上游 SA 的每个随机点都现场 new 一个 `std::random_device`（真随机源）：

- `selectDFGNodeToUnmap()` 每次调用重新播种；
- `getRandomDPCandidate()` 同样；
- 候选表洗牌用 `system_clock::now()` 当种子；
- 接受准则用全局 `rand()`（从不 `srand`，实际恒定）。

后果不是"结果略有不同"，是**同一输入两次跑出完全不同的映射，甚至一次能映射一次不能**。
实测 array_add/sa 的 M1 验收连跑三次：PASS / PASS / FAIL，第三次 mapper 直接没产出放置。

这直接判死"agent vs SA"的对比：不钉种子，比较的是两个不同的搜索过程。

改法：`SAMapper` 加成员 `std::mt19937 rng{20136u}` 与 `seedRng(unsigned)`，
四个随机点全部走 `rng`；CLI 加 `--sa_seed`（默认 20136）。验证：

- 同种子两次 → 放置逐节点相同；
- 换种子 → 结果可以不同（贪心初始化一次成功时不变，因为那条路本来不用随机）。

### SA 的空候选表崩溃（`toolchain/patches/0005-sa-empty-candidate-guard.patch`）

加了约束之后，array_add/sa 在某些 pin 下直接 **SIGSEGV**（rc=139、stdout 全空）。
gdb 栈：

```
Program received signal SIGSEGV
CGRAXMLCompile::SAMapper::inner_map (this=...) at SimulatedAnnealingMapper.cpp:417
417    auto dp_candidate = getRandomDPCandidate(selected_dfg_node).front();
#1  CGRAXMLCompile::SAMapper::SAMap (...) at SimulatedAnnealingMapper.cpp:145
#2  main (...) at CGRA_xml_compiler.cpp:179
```

根因：上游在 `inner_map()` 里对候选表直接 `.front()`，**没判空**。
约束过滤之后"这个节点此刻没有合法位置"是正常情况，空表 `.front()` 是未定义行为。
修法：候选表为空时回滚快照、当作一次失败的移动、`continue`。

**这个 bug 之前一直被误读成"SA 不支持约束"**：`run_mapper` 从空 stdout 里拿不到
`Map Success`，于是报"没有可读的放置产物"，看起来像"SA 映射失败"，实际是段错误。
教训和 M0 那次一样：**没有把退出码和信号记录下来，失败就会被归错因**。

## 三个"想错了"的 bug（都不是写错，是判据错）

### 1. 把"两个节点在同一个 PE"当错误

第一版 `Constraints` 在加载时就检查"两个节点 pin 到同一 PE"并报错退出。结果 T1 全挂：

```
constraints: nodes 0 and 12 are pinned to the same PE (1,2)
FATAL: failed to load constraints
```

但 (1,2) 这个 pin 是**从基线解本身读出来的**——array_add 的基线里节点 0 与 12 就都在 PE(1,2)。
**CGRA 是时分复用的**，同一 PE 的不同相位承载不同节点是常态，不是冲突。
同一 PE 上放不放得下，是 mapper 的 `checkDPFree` 在搜索时该判的事，约束层不越权。

这和旧工程"把 OSTORE 判成算术算子"是同一类错误：**把我以为的约束当成硬约束**。
两次都是"我的检查器把 mapper 自己产出的合法解判成非法"。

改法：删掉 `pinOwner`，`allowed()` 对未被 pin 的节点一律返回 true。

### 2. `ok` 判据让失败也看起来成功

T5（pin 到阵列外 (99,99)）本应失败，实测 `mapper ok=True`。查下来：

- mapper 在**失败**时也会留下半截 `routeInfo.log`（只含已成功布线的少数节点，实测 2 个）；
- 进程**退出码恒为 0**；
- 原来的判据 `ok = bool(placement) and ("Map Success" in out or rc == 0)` 里 `or rc == 0` 恒真。

于是只要解析出哪怕 2 个节点就算成功。**这正是旧工程"拿 mapper ok 当成功率会报 88% 假阳性"的同一类错误。**

新判据三条同时成立：

1. stdout 里有 `Map Success`；
2. 解析出的放置非空；
3. 覆盖**全部**真实节点（`real_nodes` 显式传入）。

第 3 条踩过一个坑：一开始传的是"节点数"并假设 idx 是 `0..n-1` 连续，
但 DFG 的 idx 不连续（array_add 20 个节点里还混着 20018 这类伪节点），
导致基线自己被判 17/20 失败。必须传真实 idx 集合。

### 3. `II++; if (II == max_II) break;` 是等号

`CGRA_xml_compiler.cpp:202` 用**等号**判上界。`max_ii` 等于初始 II 时，
自增后永远不等于它 → 实测扫到 **II=70、单次 11 分钟**。
`run_mapper` 里统一传 `max_ii + 1` 绕开，负面测试才秒级返回。

## 验收（`scripts/m1_constraints.py`）

| 测试 | 内容 | 判据 |
|---|---|---|
| T1 | 取基线解里 5 个高度数节点，pin 回原位 | 5/5 命中且 mapper ok |
| T1b | 每个节点**单独** pin 到同类节点用过的另一个位置 | 每个都命中且确实离开了原位 |
| T3 | 把节点 0 的全部 x≥1 位置禁掉 | 无违规且仍能映射 |
| T4 | 同一节点既 pin 又 forbidden 于同一格 | mapper 必须失败 |
| T5 | pin 到阵列外 (99,99) | mapper 必须失败 |

T1b 前两版都是错的，值得记下来：

1. **整体轮换** 5 个 pin → 把 LOAD 的位置换给 ALU 算子、又把 ALU 的位置换给 LOAD，
   测的是"pin 到非法列会不会失败"。
2. **同类内轮换** 仍然 FAIL：5 个 pin 同时生效等于把基线解锁死，任何一步不同就整体不可行，
   测的是"多重 pin 下可行性还剩多少"。
3. 最终版：**单点位移**，目标位置只从"基线里同类节点已经用过的位置"里挑。
   为什么不挑 `(0,0)` 这种角落：把 ALU 算子钉到内存列角落，即使 op 支持（FU_MEM ⊇ FU）
   也会让布线立刻不可行——那不是测 pin 有没有生效。

## 验收结果（II=4，`scripts/m1_constraints.py`）

| kernel | backend | T1 | T1b | T3 | T4 | T5 | 结论 |
|---|---|---|---|---|---|---|---|
| array_add | pathfinder | PASS | PASS | PASS | PASS | PASS | **PASS** |
| array_add | sa | PASS | PASS | PASS | PASS | PASS | **PASS** |
| mac | pathfinder | PASS | PASS | PASS | PASS | PASS | **PASS** |
| mac | sa | PASS | PASS | PASS | PASS | PASS | **PASS** |
| trmm | pathfinder | PASS | PASS | PASS | PASS | PASS | **PASS** |
| trmm | sa | PASS | PASS | PASS | PASS | PASS | **PASS** |

六格全 PASS。但两个后端的"代价"不一样，这一条对后面有用：

- **PathFinder 在 II=4 就能满足的 pin，SA 有时要抬到 II=5**。直接实测：array_add 把节点 1
  （ALU 算子）钉到 (0,2)，PathFinder 在 II=4 给出 `node1=(0,2)`；SA 在 II=4 的死约束下
  贪心初始化失败，`while(!mappingSuccess)` 把 II 抬到 5 才成功。
  原因在 `initMap()`：`enableBackTracking=false`（`backTrackLimit` 写了但没用），
  贪心一步走错就整轮失败，而 pin 让它更容易走错。
- SA 的单格耗时因此远高于 PathFinder：整个六格 sweep 跑了约 **2 小时**，绝大部分花在
  SA 的 pin 用例上（每次都要从 II=4 往上扫）。

**这两条不是缺陷证据，是"M1 的约束接口对两个后端都真生效、但代价不同"的量化。**
如果没有 0005 那个空候选表保护，SA 的失败会被 SIGSEGV 伪装成"映射失败"，
这条差异根本看不出来。

## 下一步（M1 剩余）

约束只能改"节点能在哪"，改不了"怎么搜"。真正的加速要动 SA 的五个钩子：

1. `selectDFGNodeToUnmap()` —— 现在纯随机挑节点（`sortedNodeList[uniform(0,n-1)]`）；
2. `getRandomDPCandidate(node).front()` —— 现在只取候选表第一个；
3. `whetherAcceptNewMapping()` —— 接受准则里没有我们的目标；
4. `initMap()` —— 热启动只用一次贪心；
5. `getCost()` —— 权重写死 100/10/10，没有延迟项。

真退火实测：array_add 跑满 14900 次移动（149 个温度台阶），`new best cost` 只出现 2 次
（167 → 165）。**邻域算子质量差**，这才是 agent 有活干的地方——不是"多跑"，是"挑对节点、挑对位置"。
