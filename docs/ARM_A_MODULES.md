# 臂 A 模块边界与合并说明

`interfaces.py` 是冻结契约（`Placer` / `Router` 两个 Protocol）。本文件说明哪些代码属于
A 臂、哪些是共享工具、B 臂要接 `placers/sa.py` 与 `routers/llm.py` 时改哪里。

## 1. 槽位

pipeline 只通过工厂拿实现，不直接 import 具体类：

| 槽位 | 工厂 | 工厂文件 | 当前实现 |
|---|---|---|---|
| Placer | `make_placer(kind, client=..., pins_file=..., seed=...)` | `src/cgra_agent/placers/__init__.py` | `llm`（A 臂）、`greedy`（参考）、`fixed`（回放） |
| Router | `make_router(kind, repo_root=..., xdim=..., ydim=..., timeout=...)` | `src/cgra_agent/routers/__init__.py` | `pathfinder`（vendor pinned mapper） |

CLI 对应 `--placer {llm,greedy,fixed}` 与 `--router pathfinder`。

另有一个与臂无关的导出命令（B 臂跨尺寸对比用）：

```
python -m cgra_agent.cli export-placement --dfg <DFG.xml> --arch <arch.json>
        --placer greedy --ii 4 --export-placement out/pins.json
```

尺寸（xdim/ydim/PE 列表）全部来自 arch JSON；失败时文件里写
`{"status": "placement_not_found", "error": ...}` 且退出码 2，不产出空 pins。
`placement_infeasible` 只用于穷举证明；启发式失败一律 `placement_not_found`（ARM.md §2.6）。

ad-hoc shell 脚本放在 `/home/steve/cac_scripts/armA/`（`cac_scripts/` 根目录两臂共用，勿混放）。

## 2. 文件归属

**A 臂专属（合并时随臂走）**
- `src/cgra_agent/placers/llm.py` —— LLM 布局器（A 臂主体）
- `src/cgra_agent/llm/prompts.py` —— A 臂 prompt（含 R3 延迟语义）
- `src/cgra_agent/placers/pin_select.py` —— pin 密度消融的固定选点规则（A 臂实验设计）
- `scripts/mock_replay.py`、`scripts/eval_map.py`、`scripts/eval_density.py` —— A 臂评测辅助

**共享工具（与臂无关，合并时保留一份）**
- `src/cgra_agent/tools/{dfg,facts,validate,mapper,sim,trace,jobdir}.py`
- `src/cgra_agent/pipeline.py`（S0–S9 状态机、预算分桶、II 阶梯、pin 子集开关）
- `src/cgra_agent/cli.py`、`src/cgra_agent/llm/client.py`（OpenAI 兼容客户端，任何臂可复用）
- `configs/*.yaml`（4x4 共享架构参数）

**参考实现（不属于任何臂，只用于评测/对照）**
- `src/cgra_agent/placers/greedy.py`、`src/cgra_agent/placers/fixed.py`
- `src/cgra_agent/routers/pathfinder.py`（所有臂共用的路由器）

## 3. B 臂接入步骤

1. 把 `sa.py` 放到 `src/cgra_agent/placers/sa.py`，实现
   `place(facts, arch, ii, budget, *, feedback=None, previous=None) -> Placement`。
2. 在 `placers/__init__.py` 的 `_PLACERS` 加 `"sa": ...`，并在 `make_placer` 里加构造分支
   （构造参数按需，`--placer sa` 即可选中）。
3. 把 `routers/llm.py` 放到 `src/cgra_agent/routers/llm.py`，实现 canonical 签名
   `route(placement: Placement, facts: dict, arch: dict, ii: int, budget: dict) -> MapReport`；
   文件位置从 `facts["paths"]` 取（见 §4），在 `routers/__init__.py` 的 `_ROUTERS` 注册，
   `make_router` 加分支，`--router llm` 选中。
4. `interfaces.py` 的 router 签名已定稿（2026-10-27，父代理批准改一次），pipeline 不加新参数。
5. 注意：`pipeline` 的 `pin_ratio/pin_rule` 是 A 臂实验开关（默认 1.0 = 全 pin）；
   B 臂默认不设即可。`validate` 的硬规则（依赖延迟下界）是共享的，R3/R6 只告警。

## 4. 契约细节（照抄 `interfaces.py`）

- `Placer.place(facts: dict, arch: dict, ii: int, budget: dict) -> Placement`
- `Router.route(placement: Placement, facts: dict, arch: dict, ii: int, budget: dict) -> MapReport`
  —— 5 个位置参数，不用关键字别名。pipeline 把 job/dfg/arch/ii/pins/pins_mode 收进
  `placement` + `facts` + `budget` 三件套：
  - `placement.pins`：要钉的节点（空 = 交给路由器自由布局，走 `-m 0`）；
  - `facts["paths"]`：`{"dfg": ..., "arch_json": ..., "mem_alloc": ..., "workdir": ...}`，
    router 直接按路径读文件，不再猜位置；
  - `budget`：`{"pins_mode": 1|2|3|0, "timeout_s": float}`；
  - 返回的 `MapReport` 带 `binary` 与 `binary_sha256`（router 负责写 bitstream，
    pipeline 只按字段取文件），`pins_applied` 是 mapper 内部多轮尝试的累计值
    （`pin_attempts` 别名，非去重节点数）。

### 失败分类（`failure_class`）

`MapReport.failure_class` / `CDD.failure_class` 取值 `static | runtime | semantic | toolchain`：

- `toolchain`：mapper 自身崩溃或「说 ok 但没有 bitstream」。`tools/mapper.py::check_artifacts`
  统一把 `ok=True` 且无 `*_binary.bin` 的情况改成 `ok=False, failure_class="toolchain"`，
  `rc<0`（超时除外）同样归此类。
- pipeline 遇到 `toolchain` 直接 abort：不占 validate/placement/route 桶，不再重问，CDD 记
  `layer="failure"`。这样工具链的崩溃不会计入任何一臂的失败率。
- `placement` 阶段失败 = `semantic`，`route` 阶段失败 = `runtime`；其余（validator 拒绝）由
  pipeline 自己归因。

回归用例：`tests/test_mapper.py`（ok+无 bitstream、ok+有 bitstream、信号、阶段分类）。

## 5. 字段口径（`pins[].t`、`placement[].lat`、`routeInfo -T`）

这三个字段都叫「时间」，但语义不同，B 臂已因此栽过一次（把 `routeInfo` 的 `-T` 当 latency
传给 pin，8 条 timing 冲突、布线全被拒，现象看起来像「策略全错」）。写死如下：

| 字段 | 出现位置 | 单位 | 语义 |
|---|---|---|---|
| `pins[].t` | 我们写、mapper 读（`--pins --pins_mode`） | phase | **相位**：一个 II 内的第几拍，`0 <= t < II`。mapper 按相位给节点找时间槽。 |
| `placement[].lat` | mapper `--report` / dump `--dump-placement` / 我们的 `report.json` | cycle | **绝对延迟**：从内核起点算的第几拍。`lat = t + k*II`。 |
| `routeInfo` 的 `-T` | `*_routeInfo.log`（mapper 中间产物） | phase | **相位**，等于 `lat % II`。**不是延迟**，不能直接当 pin 的 `t` 之外的东西用，更不能当 `lat`。 |

判断口诀：**pins 用相位，report 用延迟，routeInfo 的 `-T` 是相位。** 我们的
`Pin(node,x,y,t)` 只接受相位；从 mapper 产物反推 pin 时必须 `t = lat % II`，
反过来给 B 的 placement 要显式写 `lat` 时是绝对延迟，别再和 `t` 混。

`pins_applied` 也要注意：它是 mapper 内部多轮放置尝试的**累计成功次数**（`LLMMapper.cpp:206`
无 reset，method 3 不扫 II），不是去重节点数，也不是跨 II/进程累计；`pin_attempts` 是它的别名。

## 6. 数据位置（给 B 的稳定路径）

| 路径 | 内容 |
|---|---|
| `work/kernels/<kernel>/dfg.xml` | DFG（`*_PartPredDFG.xml` 的固定副本） |
| `work/kernels/<kernel>/mem.json` | `update_mem_alloc.py` 生成、带 bank 标注的 arch |
| `work/kernels/<kernel>/arch.json` | 原始 `hycube_original.json`（未改 bank） |
| `work/kernels/<kernel>/mem_alloc.txt` | 变量基址表 |
| `work/kernels/<kernel>/<kernel>_trace_*.txt` | 生成好的 trace（最多 4 条） |
| `work/kernels/<kernel>/manifest.json` | 节点数、sha256、trace 列表 |
| `work/traces/<kernel>/` | trace 原始输出（`scripts/gen_trace.sh` 的产物） |
| `work/simv/<kernel>-<tag>.log` | 仿真扫描结果 |
| `work/armA/<kernel>/<ts>/` | 单次 pipeline 的完整 bundle（时间戳目录，不保证唯一副本） |

`work/kernels/` 由 `scripts/export_kernels.py` 重建（跑一遍 DFG pass，约 1–2 分钟/10 kernel）。
时间戳 bundle 只用于调试，**B 读输入一律用 `work/kernels/`**；evaluation 产物以这里为准。

## 7. vendor 补丁（不在 git 里）

`toolchain/vendor/` 整体被 gitignore。本轮给 mapper 加了 `--dump-placement`（诊断用），
源码改动不随仓库走：

- `scripts/patch_mapper_dump.py`：幂等打补丁（`util.h` 注册选项、`CGRA_xml_compiler.cpp`
  在 `printHyCUBEBinary` 后写 `{node,op,x,y,t,lat}` JSON）；
- `scripts/rebuild_mapper.sh`：`cgra-tc`（GCC 7.5）下重建并覆盖 `toolchain/out/cgra_xml_mapper`。

重建后 `toolchain/out/cgra_xml_mapper --dump-placement /tmp/p.json ...` 会在成功映射后打印
`[dump] placement -> ...`。当前二进制已带该功能；如果不重建，dump 参数不存在，`scripts/diag_placement.py`
会退化为只做 validate。
