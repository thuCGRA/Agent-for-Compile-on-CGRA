# M1.5 / M1.5b：SA 的邻域算子与代价函数，都不值得改

**结论先说：两个手写邻域算子没用，换代价函数也没用。** 三次实验把原因逼到了同一处——
**退火本身在空转**：单节点重定位这个邻域算子几乎总把代价推高 3 倍，300 次移动里
只有 4 次比热启动便宜，最好的一次只便宜 1 点。所以 agent 如果在"调 SA 的旋钮"
上做决策，上限就是热启动（= PathFinder），没有增益。

**能改变结果的只有改问题结构的动作**：区域分解（A2 `set_region`）与硬约束
（A3 `constrain`，M1 已可用）。这决定了 M2/M3 的决策粒度。

## 1. 改了什么

上游 SA 的邻域算子是纯随机的：

```cpp
// selectDFGNodeToUnmap()
std::uniform_int_distribution<int> uniform_dist(0, sortedNodeList.size()-1);
return sortedNodeList[std::abs(std::round(uniform_dist(e1)))];

// getRandomDPCandidate() 尾部：洗牌后调用方取 .front()
std::shuffle(candidateDests.begin(), candidateDests.end(), rng);
```

`toolchain/patches/0006-sa-neighbour-strategies.patch`（201 行）加了两个开关：

| 开关 | 取值 | 行为 |
|---|---|---|
| `--sa_select` | 0 | 随机挑节点（上游） |
| | 1 | 按 `nodeBlame` 降序取前 4 名，再在其中随机 |
| `--sa_pick` | 0 | 候选表洗牌后取第一个（上游） |
| | 1 | 按 `candidateScore` 升序稳定排序，**同分段内再洗牌** |

- `nodeBlame(node)`：①同 PE 上还挤了 `same` 个节点 → `blame += 3*same`；②已布线边 `hops > 1` → `blame += hops-1`。
- `candidateScore(dp, node)`：`2 * (到已放置 parents/children 的曼哈顿距离之和) + 该 PE 上的占用数`。
- 同分段内必须再洗牌，否则 `candidateScore` 相同的一批 PE 里永远取同一个方向，退化成"只往北走"。

## 2. 四变体 × 三种子（II=4，budget=1000，G1 仿真判据）

`scripts/m15_seednoise.py --kernel array_add --kernel mac --kernel trmm --seeds 20136,7,99`

### array_add（20 节点）
| variant | seed 20136 | seed 7 | seed 99 | cost min/med/max |
|---|---|---|---|---|
| base | 36 / lat 11 | 36 / lat 11 | 36 / lat 11 | **36 / 36 / 36** |
| sel | 34 / lat 12 | 32 / lat 11 | 37 / lat 12 | 32 / 34 / 37 |
| pick | 35 / lat 13 | 37 / lat 11 | 36 / lat 11 | 35 / 36 / 37 |
| both | 37 / lat 11 | 36 / lat 11 | 43 / lat 12 | 36 / 37 / 43 |

### mac（22 节点）
| variant | seed 20136 | seed 7 | seed 99 | 通过数 |
|---|---|---|---|---|
| base | fail lat14 cost43 | fail lat11 cost45 | fail lat11 cost46 | **0/3** |
| sel | PASS **II=5** lat12 cost50 | PASS **II=5** lat11 cost50 | fail II=4 | 2/3 |
| pick | fail lat11 cost44 | fail lat11 cost48 | fail lat13 cost38 | **0/3** |
| both | PASS **II=5** lat11 cost55 | PASS **II=5** lat12 cost54 | PASS **II=5** lat12 cost53 | 3/3 |

### trmm（11 节点）
| variant | seed 20136 | seed 7 | seed 99 | cost min/med/max |
|---|---|---|---|---|
| base | 18 / lat 6 | 18 / lat 6 | 18 / lat 6 | **18 / 18 / 18** |
| sel | 20 / lat 7 | 18 / lat 6 | 25 / lat 6 | 18 / 20 / 25 |
| pick | 24 / lat 6 | 24 / lat 6 | 24 / lat 6 | **24 / 24 / 24** |
| both | 24 / lat 6 | 24 / lat 6 | 24 / lat 6 | **24 / 24 / 24** |

## 3. 三条读得出的结论

### 3.1 base 在热启动可行时**完全确定**，一个种子换不出差别

array_add 三种子全是 36、trmm 三种子全是 18、lat 也一模一样。
不是"结果稳定"，是**退火一步都没走成**：`initMap()` 是确定性的贪心 PathFinder，
热启动一旦可行，`considerBest()` 就把这个解记成 best，之后任何一次移动都必须让
SA 自己的 `getCost()` 严格更小才会替换——而热启动的 `unmapped=0, conflict=0, overuse=0`，
唯一能降的只有 `port_usage`，基本降不动。所以 `--sa_optimize` 跑满 1000 次移动后
`restoreBestSnapshot()` 还原的**就是热启动那个解**。

这条把 M0 的观察钉死了：**"14900 次移动只换来 2 点 cost 改进"不是搜索能力弱，
是搜索目标里根本没有我们要的东西。**

### 3.2 手写启发式的收益全在噪声里，还稳定引入劣化

- `sel` 在 array_add 上 median 34（比 base 36 好 2），但区间 32–37 与 base 重叠。
- `pick` 在 trmm 上 **24/24/24**，三种子一致地比 base 的 18 差 6——这不是噪声，是系统性劣化：
  往"离已放置邻居近"的 PE 挤，会把节点堆成团，绕行反而变大。
- `both` 在 array_add 上 36/37/43，最差。
- 两个变体的耗时都接近翻倍（`nodeBlame` 是 O(n²) 遍历），array_add 17.8 s → 33.7 s。

**没有全局最优**：array_add 偏好 sel、trmm 偏好 base、mac 只有 sel/both 能过。
但这种"因 kernel 而异"不能当 agent 的卖点，因为差异幅度小于噪声。

### 3.3 mac 上 sel/both 的"通过"是假象

mac 的 base 三种子全 G1 fail，sel/both 却 PASS——但**II 被抬到了 5**。
原因不是它们搜得更好，而是它们**更慢**：在 II=4 用完预算仍没找到可行解，
`while(!mappingSuccess)` 把 II 抬到 5，而 II=5 恰好避开了那个让 mac 崩掉的
SELECT 同拍双操作数冲突（`CGRATile.cpp:168` 的 `assert(!(I1isvalid && I2isvalid))`）。

换句话说：**它们用"找不到解"换来了"找到一个合法的解"**，代价是 II 从 4 退到 5
（吞吐直接掉 20%）。这不是加速，是拿时序换合法性。

## 4. 所以下一步该做什么

不是继续手写第三个启发式。是**把目标换掉**：

| | 上游 `getCost()` | 我们真正想要的 |
|---|---|---|
| 组成 | `unmapped*100 + congestion*10 + conflict*10 + port_usage` | `detour + hops + select_pairs` |
| 关心 | 能不能映射 | 映射得好不好（走线长度、绕行、SELECT 同拍） |
| 后果 | 找到可行解就再没有下降空间 | 有明确的优化方向 |

具体做法：加 `--sa_cost_mode`，mode 1 时把 `getCost()` 的 `port_usage` 换成
**已布线边的总跳数**（`data_routing_path` 里每条路径的 `hops` 累加，C++ 侧可直接算）。

## 5. 把目标换掉之后：还是没有改善（patch 0007，`--sa_cost_mode`）

`toolchain/patches/0007-sa-cost-mode.patch`（92 行）加了 `--sa_cost_mode`：
mode 1 时 `getCost()` 返回 `unmapped*100 + congestion*10 + conflict*10 + getWireLength()`，
`getWireLength()` = `data_routing_path` 里每条路径 `path.size()-1` 的总和。

`scripts/m15b_costmode.py`（II=4，budget=1000，三种子）：

| kernel | mode 0（上游 port_usage） | mode 1（总跳数） |
|---|---|---|
| array_add | 36 / 36 / 36，lat 11 | **36 / 36 / 36，lat 11** |
| trmm | 20 / 18 / 18，lat 6–7 | **18 / 18 / 18，lat 6** |
| mac | 3/3 G1 fail | **3/3 G1 fail** |

换目标只把 trmm 的一个种子从 20 修到 18（就是回到热启动），**没有任何一个 kernel 变好**。
于是问题不在"目标错了"，在更下面一层。

## 6. 直接看退火轨迹：单点移动几乎总是把代价推高三倍

用 `COST=1` 打开 `getCost()` 的日志，跑 array_add（budget=300，seed 20136），统计每次移动的代价：

| | mode 0 | mode 1 |
|---|---|---|
| `getCost` 调用次数 | 357 | 367 |
| 热启动代价（第一次） | 167 | 165 |
| 全程最低代价 | 166 | **165（一次都没降）** |
| 全程最高代价 | **538** | **536** |
| 比热启动更低的移动次数 | **4 / 357** | **0 / 367** |

**这就是根因，且与目标函数无关**：一次"搬走一个节点 + 重新路由它所有边"的移动，
绝大多数时候把总代价推到热启动的 **3 倍**（538 vs 167）。300 多次移动里只有 4 次
比热启动便宜，最好的也只便宜 **1 点**。

上游 SA 在 3 个 kernel 上不是"搜索能力弱"，是**邻域算子结构性无效**：
PathFinder 的贪心热启动已经在一个单点移动的局部最优上，单节点随机重定位
既逃不出去（温度不够高、接受率 0.01）也找不到更好的点。

## 7. 结论：agent 的价值不在"调 SA 的旋钮"

三条链合起来说明一件事：

1. `budget`（M0）、`seed`（M1.5）、`select`/`pick`（M1.5）、`cost_mode`（M1.5b）
   —— 这五个旋钮在 3 个 kernel 上都**换不来质量**，因为退火本身空转；
2. 换目标不改结构：邻域算子决定了"一步能到哪"，而它一步只能到更差的地方；
3. 所以 agent 如果只在"选哪个后端 / 给多少预算 / 用什么目标"上做决策，
   **上限就是热启动（= PathFinder）**，也就是没有增益。

能改变结果的只有**改问题结构**的两类动作：

| 动作 | 为什么可能有用 |
|---|---|
| **A2 `set_region`（区域分解）** | 固定大部分节点，让 mapper 只重排一个小子图（≤8 节点）。搜索空间小到能被真解开，而不是在 22 个节点上随机游走。这正是笔记里"多步放置降低搜索空间"的原意。 |
| **A3 `constrain`（pins / forbidden）** | M1 已验证可用（6 格全 PASS）。把"该往哪走"变成硬信息喂给 mapper，替代随机试探。 |

这两条也是后续 M2/M3 的主线：agent 的决策粒度落在"拆哪一块、钉哪些点"，
而不是"给 SA 调参"。

## 8. 复现

```bash
export ACC_THU_ROOT=/home/sutong/acc-thu-agent
python scripts/m15_neighbours.py --budget 1000 --json work/m15_b1000.json
python scripts/m15_seednoise.py --kernel array_add --kernel mac --kernel trmm \
    --seeds 20136,7,99 --budget 1000 --json work/m15_seednoise.json
python scripts/m15b_costmode.py --seeds 20136,7,99 --budget 1000 --json work/m15b_costmode.json

# 退火轨迹（直接调 mapper，COST=1 才有 getCost 日志）
COST=1 out/cgra_xml_mapper -d dfg.xml -j arch.json -x 4 -y 4 -i 4 -t HyCUBE_4REG \
    -m 1 --sa_seed 20136 --sa_force_anneal --sa_optimize --sa_budget 300 --sa_cost_mode 1
```

产物 `out/cgra_xml_mapper` sha256（含 0007）`9b8e01f0d6ff4359f43a6f3df74c6546540dedc7d56a10b80fc796176ad67f09`。