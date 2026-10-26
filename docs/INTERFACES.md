# 接口与数据模型

**版本**：v0.1  
**原则**：所有跨进程/跨 agent 的数据都必须有 schema；所有失败都必须归一化为错误码；所有数字都必须带单位与来源。

---

## 1. 约定

### 1.1 标识

- `contract_id`：`c-<yyyymmdd>-<kernel>-<nnn>`
- `dp_id`：`sha256(canonical_json(app, arch, map))[:12]`
- `candidate_id`：`cand-<dp_id>-<attempt_index>`
- `cdd_id`：`cdd-<sha256(canonical_json(cdd_without_id))[:8]>`
- artifact：`sha256:<hex>`，指向 CAS 中的内容

### 1.2 单位

| 字段 | 单位 |
|---|---|
| `wall_s`、`seconds`、`duration_s` | 秒（浮点） |
| `ii`、`cycles`、`lat` | 周期（整数） |
| `tokens` | 整数 |
| `bytes` | 字节 |
| `*_proxy` | 无量纲，必须在报告中注明“代理指标” |

### 1.3 统一信封

所有工具返回：

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "meta": {
    "tool": "map",
    "cmd": "...",
    "exit": 0,
    "duration_s": 42.1,
    "stdout_tail": "...",
    "artifact": "sha256:..."
  }
}
```

失败：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "ROUTE_FAIL",
    "stage": "route",
    "message": "Map Fail at II=3",
    "detail": {"fail_edges": [...], "pins_rejected": 0},
    "retryable": true
  },
  "meta": {"tool": "map", "exit": 1, "duration_s": 12.4}
}
```

`retryable` 是给 Conductor 的提示，不是给 agent 的自由裁量：`false` 时禁止原样重试。

---

## 2. CompileContract

```json
{
  "contract_id": "c-20261026-gemm-001",
  "mode": "compile",
  "app": {
    "source": "benchmarks/gemm.c",
    "kernel": "gemm",
    "entry": "kernel_gemm",
    "language": "c"
  },
  "arch": {
    "kind": "fixed",
    "adl": "json_arch/hycube_original.json",
    "xdim": 4,
    "ydim": 4,
    "banks": 2,
    "banksize": 2048,
    "pe_type": "HyCUBE_4REG"
  },
  "objective": {
    "primary": "cycles",
    "direction": "min",
    "constraints": {
      "ii_max": 8,
      "verified": true,
      "area_proxy_max": null
    }
  },
  "correctness": {
    "require_sim_pass": true,
    "traces": 4,
    "tolerance": 0
  },
  "budget": {
    "wall_s": 1800,
    "map_calls": 40,
    "sim_calls": 20,
    "llm_calls": 60,
    "tokens": 200000
  },
  "search": {
    "allowed_transforms": ["unroll", "if_convert", "restrict_ptrs"],
    "allowed_mappers": [0, 1, 2, 3],
    "allowed_pin_modes": [2, 1, 3],
    "ii_max": 8,
    "max_nodes_for_llm": 150
  },
  "report": {
    "formats": ["json", "md"],
    "include_pareto": true,
    "include_prompts": true
  }
}
```

### 校验规则

| 字段 | 规则 | 违反时 |
|---|---|---|
| `mode` | `compile` / `codesign` / `study` | 拒绝 |
| `arch.kind` | `fixed` / `space` | 拒绝 |
| `objective.primary` | 必须是可测字段：`cycles` / `ii` / `wall_s` | 要求澄清 |
| `objective.direction` | 与 primary 匹配（`min`/`max`） | 拒绝 |
| `budget.*` | 非负整数；至少 `wall_s > 0` | 拒绝 |
| `search.ii_max` | `>= 1` 且 `<= 64` | 拒绝 |
| `correctness.require_sim_pass` | 本项目恒 `true` | 强制为 true |
| `arch.adl` | 文件存在且 `arch_lint` 通过 | 报 `ARCH_ILLEGAL` |

---

## 3. DesignPoint

```json
{
  "dp_id": "7f3a1c2b9d0e",
  "app": {
    "dfg_type": "PartPred",
    "transforms": {"unroll": 2, "if_convert": true, "restrict_ptrs": true},
    "kernel": "bicg"
  },
  "arch": {
    "template": "hycube_4x4",
    "adl": "runs/.../arch.json",
    "patch": {"banks": 4}
  },
  "map": {
    "portfolio": [
      {"method": 0, "ii": 3, "timeout_s": 120, "pins": null},
      {"method": 3, "ii": 3, "timeout_s": 300, "pins": {"mode": 2, "scope": "critical"}}
    ]
  },
  "fingerprint": "sha256:..."
}
```

`portfolio` 由 A4 生成；Conductor 按顺序执行，命中 `verified` 即停。每个尝试会展开成一条 `MapAttempt`。

---

## 4. Facts（来自 `morpher_llm/facts.py` 的扩展）

```json
{
  "nodes": [
    {
      "idx": 12,
      "op": "LOAD",
      "asap": 2,
      "alap": 2,
      "bb": "_0_0",
      "const": null,
      "parents": [11],
      "children": [0],
      "bank": 0,
      "nextiter": false
    }
  ],
  "arch": {
    "xdim": 4,
    "ydim": 4,
    "pe_all": [[0,0], [0,1]],
    "pe_mem": [[0,0]],
    "pe_alu": [[0,1]],
    "ops_alu": ["ADD", "MAC", "SELECT"],
    "ops_mem": ["LOAD", "STORE"],
    "lat_alu": {"ADD": 1, "MAC": 1},
    "lat_mem": {"LOAD": 8},
    "var_bank": {"A": 0, "B": 1},
    "pe_bank": {"0": [0,0]}
  },
  "n_nodes": 26,
  "n_inputs": 9,
  "res_min_ii": 3,
  "op_hist": {"MAC": 8, "LOAD": 9, "SELECT": 2},
  "node_bank": {"12": 0},
  "bank_vars": {"0": ["A"]},
  "bank_pes": {"0": [[0,0]]},
  "scc": [[0, 3, 7], [12, 13]],
  "critical_path": [11, 12, 0, 1, 4],
  "fanout": {"11": 2, "12": 1}
}
```

新增字段 `scc`、`critical_path`、`fanout` 由确定性代码计算，供 A5 选锚点；`facts` 本身不交给 LLM 全量渲染，而由 `render_facts()` 压缩。

---

## 5. MapAttempt / MapResult

```json
{
  "attempt_id": "att-7f3a-02",
  "dp_id": "7f3a1c2b9d0e",
  "method": 3,
  "ii": 3,
  "pins_mode": 2,
  "pins_scope": "critical",
  "timeout_s": 300,
  "ok": true,
  "stage": "done",
  "lat": 3,
  "seconds": 42.1,
  "pins_total": 8,
  "pins_applied": 8,
  "pins_rejected": 0,
  "fail_edges": [],
  "congestion": [],
  "route_cost": {"total": 91, "max_edge": 7},
  "error": null,
  "report_path": "runs/.../map_report.json",
  "bitstream": "sha256:..."
}
```

`stage` 的取值：`placement`（pin 不合法/无法满足）、`route`（路由失败）、`done`（成功）、`timeout`、`crash`。

**重要**：`ok=true` 只表示 mapper 没有资源超订，不表示功能正确；`verified` 在 `Evidence` 中由仿真决定。

---

## 6. SimResult

```json
{
  "ran": true,
  "ok": true,
  "matches": 4096,
  "mismatches": 0,
  "cycles": 1180,
  "traces": 4,
  "first_mismatch": null,
  "diagnosis": null,
  "per_pe_busy": {"0,0": 0.82, "0,1": 0.64},
  "bank_access": {"0": 1024, "1": 980},
  "stall_reasons": {"route": 0, "memory": 12},
  "seconds": 3.4,
  "log": "sha256:..."
}
```

失败时：

```json
{
  "ran": true,
  "ok": false,
  "matches": 4095,
  "mismatches": 1,
  "cycles": 1190,
  "first_mismatch": {"addr": 4096, "expected": 17, "actual": 23, "node": 31},
  "diagnosis": {
    "kind": "same_cycle_operands",
    "pe": [2, 1],
    "cycle": 47,
    "op": "MAC",
    "message": "instruction MAC received both operands in the same cycle"
  }
}
```

`per_pe_busy`、`bank_access`、`stall_reasons` 需要 simulator 增强（见架构文档 §11.7）；缺失时字段为 `null`，不得伪造。

---

## 7. CDD

```json
{
  "cdd_id": "cdd-9c1a2b3d",
  "scope": {
    "level": "region",
    "nodes": [12, 13, 14],
    "pe": [[2, 1]],
    "cycles": [40, 80]
  },
  "symptom": {
    "stage": "route",
    "code": "ROUTE_FAIL",
    "message": "edge 12->13 unroutable at II=3"
  },
  "primary_impact": "latency",
  "root_cause": "phase_violation",
  "evidence": [
    {"kind": "map_report", "ref": "sha256:...", "field": "fail_edges[0]"},
    {"kind": "facts", "ref": "sha256:...", "field": "nodes[13].parents"}
  ],
  "recommended_actions": [
    {
      "op": "repin_region",
      "args": {"nodes": [12, 13], "mode": "prefer"},
      "expected": "phase_gap >= 3 for edge 12->13",
      "cost_estimate": {"map_calls": 1}
    }
  ],
  "confidence": 0.72,
  "analysis_base": "best"
}
```

### 枚举

`scope.level`：`dfg` / `region` / `node` / `pe`  
`primary_impact`：`latency` / `throughput` / `energy` / `compile_time` / `correctness`  
`root_cause`：`recurrence` / `routing_congestion` / `bank_conflict` / `phase_violation` / `pe_imbalance` / `dfg_too_large` / `unsupported_op` / `memory_layout` / `ii_bound` / `schedule_conflict` / `unknown`  
`analysis_base`：`current` / `best` / `previous`

### 一致性规则

- `root_cause=phase_violation` 时，`evidence` 必须包含至少一条 `fail_edges` 或 `diagnosis`。
- `root_cause=routing_congestion` 时，必须包含 `congestion` 或 `route_cost`。
- `root_cause=unsupported_op` 时，必须包含 op 名与节点 idx。
- `confidence < 0.5` 时，`recommended_actions` 最多 1 条，且 Judge 应优先选择“再取一次证据”而不是直接改。

---

## 8. Evidence / Candidate / Verdict

```json
{
  "candidate_id": "cand-7f3a-02",
  "dp_id": "7f3a1c2b9d0e",
  "artifacts": {
    "dfg": "sha256:...",
    "pins": "sha256:...",
    "bitstream": "sha256:...",
    "map_report": "sha256:...",
    "sim_log": "sha256:..."
  },
  "evidence": {
    "build": {
      "nodes": 26,
      "mii": 3,
      "res_mii": 3,
      "rec_mii": 3,
      "op_hist": {"MAC": 8, "LOAD": 9}
    },
    "map": {"ok": true, "ii": 3, "lat": 3, "seconds": 42.1, "pins_applied": 8, "pins_rejected": 0},
    "sim": {"ran": true, "matches": 4096, "mismatches": 0, "cycles": 1180, "traces": 4},
    "qor": {"ii": 3, "ii_over_mii": 1.0, "cycles": 1180, "area_proxy": 1.0},
    "cost": {"wall_s": 61.3, "llm_calls": 2, "tokens": 3100, "usd": 0.004}
  },
  "verified": true,
  "timestamp": "2026-10-26T12:00:00+08:00"
}
```

`verified` 只能由 Conductor 写；agent 写 `verified` 会被 schema 拒绝。

```json
{
  "decision": "accept",
  "pareto_rank": 1,
  "reasons": ["verified", "ii=mii", "best cycles"],
  "next": "continue",
  "rejected": [
    {"candidate_id": "cand-...", "reason": "unverified"},
    {"candidate_id": "cand-...", "reason": "ii_exceeds_contract"}
  ]
}
```

---

## 9. Agent I/O

### 9.1 放置输出

```json
{
  "analysis": "short, <= 80 words",
  "pins": [{"node": 0, "x": 0, "y": 0, "t": 0}],
  "unpinned": [7, 8, 9],
  "mode": "prefer",
  "confidence": 0.66,
  "evidence_refs": ["facts:scc", "facts:node_bank"],
  "expected": {"ii": 3, "risk": "port OUT1 at (2,1)"}
}
```

校验：`pins` 非空、节点唯一、`0<=x<xdim`、`0<=y<ydim`、`0<=t<ii`、op 支持、bank 规则。`mode` 必须是合同允许的值。

### 9.2 设计移动

```json
{
  "op": "repin_region",
  "args": {"nodes": [12, 13], "mode": "prefer"},
  "rationale": "phase gap 1 < hops 3",
  "evidence_refs": ["cdd:cdd-9c1a2b3d"],
  "expected_effect": {"ii": -1, "confidence": 0.5},
  "cost_estimate": {"map_calls": 1, "llm_calls": 0}
}
```

Materializer 的返回：

```json
{"ok": false, "reason": "ILLEGAL_MOVE", "detail": {"field": "ii", "current": 3, "min": 3}}
```

### 9.3 诊断输出

```json
{
  "cdd": {"...": "..."},
  "alternatives": [],
  "needs_more_evidence": null
}
```

若 `needs_more_evidence` 非空，Conductor 先执行取证工具（如再跑一次带 `SIMULATOR=1` 的仿真），再让 A6 继续，避免模型在信息不足时编根因。

---

## 10. 工具 API

```python
def build_dfg(src: Path, kernel: str, cfg: DfgConfig) -> ToolResult[DfgArtifact]: ...
def build_traces(src: Path, kernel: str, cfg: TraceConfig) -> ToolResult[TraceArtifact]: ...
def dfg_stats(dfg: Path) -> ToolResult[Facts]: ...

def arch_lint(adl: Path) -> ToolResult[ArchReport]: ...
def arch_patch(template: Path, patch: dict, out: Path) -> ToolResult[ArchArtifact]: ...
def update_mem_alloc(adl: Path, dfg: Path, banks: int) -> ToolResult[None]: ...

def estimate_mii(dfg: Path, adl: Path, xdim: int, ydim: int) -> ToolResult[MiiReport]: ...
def run_map(dfg: Path, adl: Path, *, method: int, ii: int, pins: Path | None,
            pins_mode: int = 2, timeout_s: int = 600, out_dir: Path) -> ToolResult[MapAttempt]: ...
def run_sim(bitstream: Path, traces: list[Path], mem_alloc: Path, *,
            xdim: int, ydim: int, mem_size: int, timeout_s: int = 120) -> ToolResult[SimResult]: ...

def qor(evidence: Evidence) -> ToolResult[QorReport]: ...
def validate_pins(facts: Facts, pins: list[Pin], ii: int) -> ToolResult[ValidationReport]: ...
```

所有函数都必须接受 `out_dir`，不得写当前目录；所有路径必须是绝对路径或经 `paths.py` 归一化。

---

## 11. 事件日志

`events.jsonl`，每行一个事件：

```json
{"ts": "2026-10-26T12:00:00+08:00", "campaign": "gemm-001", "seq": 42,
 "type": "state.transition", "from": "S6", "to": "S7",
 "payload": {"dp_id": "7f3a1c2b9d0e", "reason": "route_fail"},
 "budget": {"wall_s": 1200, "map_calls": 12, "sim_calls": 3, "llm_calls": 5, "tokens": 12000}}
```

事件类型：`state.transition`、`agent.call`、`agent.output`、`tool.call`、`tool.result`、`gate.check`、`verdict`、`error`。

`replay` 命令从事件日志重建 `CampaignState`，并在指定状态停下；用于调试与论文复现。

---

## 12. 错误码

| code | stage | 含义 | retryable |
|---|---|---|---|
| `BUILD_DFG_FAIL` | build | clang/opt/pass 失败 | 否（需改 app） |
| `UNSUPPORTED_OP` | build | DFG 含架构不支持的 op | 否 |
| `DFG_TOO_LARGE` | build | 节点数超容量 | 否 |
| `ARCH_ILLEGAL` | arch | ADL lint/连接/op 失败 | 否 |
| `BANK_OVERFLOW` | arch | 变量超出 bank 容量 | 否 |
| `PLACEMENT_ILLEGAL` | placement | 静态规则失败 | 是（改 pin） |
| `ROUTE_FAIL` | route | 路由失败 | 是（改 pin/II/mapper） |
| `II_LIMIT` | route | 达到 `ii_max` 仍失败 | 否（改设计点） |
| `SIM_MISMATCH` | sim | 功能失配 | 是（诊断后） |
| `SIM_ABORT` | sim | 断言/崩溃 | 是（诊断后） |
| `TIMEOUT` | any | 超时 | 是（降级） |
| `OOM` | any | 内存不足 | 是（降级） |
| `LLM_INVALID_JSON` | llm | 输出不符合 schema | 是（最多 2 次） |
| `BUDGET_EXHAUSTED` | any | 预算不足 | 否 |
| `NONDET` | verify | 同配置结果不一致 | 否（隔离） |
| `INTERNAL` | any | 未分类错误 | 否（人工） |

---

## 13. 版本与兼容

- `schema_version` 出现在每个 artifact 的顶层；破坏性变更升 major。
- 旧 artifact 在读取时做迁移；迁移函数集中在 `schemas/migrate.py`。
- Morpher 上游 submodule 的 commit 记录在 `runs/<campaign>/env.json`；跨 commit 的结果不能直接比较。
