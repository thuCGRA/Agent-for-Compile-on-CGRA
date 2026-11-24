# M1.5：两个邻域算子值不值得改

**结论先说：不值得——至少不是这样改。** 手写的两个启发式没有一个能稳定改善质量，
而实验暴露出一个更根本的问题：**上游 SA 的 `getCost()` 里没有延迟/绕行，它优化的目标
和我们要的不是一个东西。不修这个，agent 拧任何旋钮都只是在噪声里晃。**

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
这一个改动同时解决三件事：① SA 有了真正的优化目标 ② agent 的 `budget`/`seed`/`select`/`pick`
四个旋钮才第一次有意义 ③ 我们的 cost 与 SA 的 cost 对齐，才能谈"agent 让 SA 更快收敛"。

## 5. 复现

```bash
export ACC_THU_ROOT=/home/sutong/acc-thu-agent
python scripts/m15_neighbours.py --budget 1000 --json work/m15_b1000.json
python scripts/m15_seednoise.py --kernel array_add --kernel mac --kernel trmm \
    --seeds 20136,7,99 --budget 1000 --json work/m15_seednoise.json
```

产物 `out/cgra_xml_mapper` sha256 `58aead670c999b5f456fc4a38e7df4c1e6fcab279a5787ad8da7659b9fa43b73`。