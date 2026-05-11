# Phase 1: Async Retriever + KB Metadata Sidecar - Context

**Gathered:** 2026-05-11
**Status:** Ready for planning
**Mode:** `--auto --chain`（决策面已由 grill-me 13 轮闭环，本步骤固化实现层选择并自动推进至 plan-phase）

<domain>
## Phase Boundary

把 `scripts/retriever.py` 中的同步 QMD 客户端重构为 `AsyncQMDService`（基于 `httpx.AsyncClient` + 全局 `asyncio.Semaphore`），并在 `cmd_index` 一次性产出 `$KB_ROOT/.metadata/{chunks.json, org_disease_coverage.json, synonym_map.yaml}` 三个病种侧车文件。所有现有同步 API 通过 thin shim 保持 backward compatibility，173 条测试基线不动。

**不在本 Phase 范围内**（属于 Phase 2/3）：
- LLM 客户端、JSON Schema strict 输出、`AsyncLLMClient.complete_structured`
- `scripts/pipeline.py`、`run` 子命令、患者级并发流水线
- per-patient shard 输出、`_failed/`、`--resume`
- 任何 `batch_pipeline.py` 子命令的删除（Phase 4）
- 双层过滤的"应用"（chunk-level filter at query-time）—— 本 Phase 只交付**数据**（侧车 JSON）和**词表归一化函数**，调用方实际接入留 Phase 3

</domain>

<decisions>
## Implementation Decisions

### Async Retriever 接口策略

- **D-01:** 单一异步实现，同步 `QMDService` 降级为 thin shim
  - `AsyncQMDService` 是新真相，`QMDService.__enter__/__exit__/query/search` 用 `asyncio.run(self._async.method(...))` 适配
  - 推荐选项（first/recommended in refactor_plan §四.2）：保持 sync API 字节级兼容，`tests/test_retriever.py` 与 `tests/test_qmd_integration.py` 无修改通过
  - 拒绝：双实现（sync + async 并存）→ 重复代码 + 行为 drift 风险

- **D-02:** QMD 子进程生命周期保持同步（一次 pipeline run 一个 server）
  - `__aenter__` 仍 `subprocess.Popen("qmd mcp --http")` + 同步 `_wait_for_ready`；只把 HTTP I/O 异步化
  - 推荐理由：QMD 子进程启动本身 ~1-2s 且 single-shot，异步化没有收益且增加复杂度

- **D-03:** 全局并发上限 `asyncio.Semaphore(8)`，CLI `--concurrency-qmd` 覆盖
  - Semaphore 实例**注入式**：由 `AsyncQMDService.__init__(semaphore=None)` 接受外部 sem；为 None 时内部新建 `Semaphore(8)`
  - 推荐理由：Phase 3 `pipeline.py` 需共享同一 sem，注入式避免硬编码上限

- **D-04:** MCP `Mcp-Session-Id` 失效检测 = "HTTP 400 OR response 缺 session header"
  - 触发条件下：**同一 `_async_query()` 内 retry 一次** = 重发 `initialize` 后再发原请求
  - 失败仍报错（不无限循环）；超时（180s）走 `httpx.ReadTimeout` 上抛由调用方处理
  - 推荐理由：refactor_plan §四.2 明确"中途失效自动 re-initialize 重试一次"

### KB Metadata 侧车产出时机

- **D-05:** `cmd_index` 一次性产出三个侧车文件（与 QMD `embed` 同步）
  - 推荐选项：`cmd_index` 末尾扫描 `kb_root/<org>/extracted/*.md` 收集 `disease_tags` 写入 `.metadata/chunks.json`，聚合产出 `org_disease_coverage.json`，首次种子化 `synonym_map.yaml`
  - 拒绝：lazy 首次 `run` 时构建 → 把 IO 成本压到 critical path 上、增加 race condition

- **D-06:** chunk-level `disease_tags` 推断走**文件路径 + 文件名启发式**
  - KB 已按 `org/extracted/<org>-<disease>-<version>.md` 命名（参考 `NCCN/extracted/`），文件名 stem 含病种关键词
  - 推断算法：解析文件名 → 经 `synonym_map.yaml` 归一化 → 写入 `chunks.json[file_path].disease_tags`
  - 找不到匹配 → `disease_tags: []`（标签缺失），下游"保留"策略兜底（KBM-06）
  - 拒绝：LLM 抽取首段 → 引入额外 LLM 依赖，且 Phase 1 不依赖 LLM；留 v3.2 增强

- **D-07:** `org_disease_coverage.json` 从 `chunks.json` 聚合派生（不双重维护）
  - 算法：`org_disease_coverage[org] = sorted(set(chunk.disease_tags for chunk in chunks if chunk.org == org))`
  - 拒绝：手工静态 yaml → 与 chunks.json 容易 drift；新增指南文件需手动维护两处

### synonym_map.yaml 设计

- **D-08:** 首次 `index` 种子化覆盖 KB 现有 ~10 种癌种 + 可扩展 `overrides.yaml`
  - 必含（refactor_plan §四.4 已给样例）：`gastric / colorectal / neuroendocrine / esophageal / hepatic / pancreatic / breast / lung / cervical / lymphoma`
  - 每个 canonical_key 下挂 5-10 个中英文同义词（"胃癌" / "胃腺癌" / "EGJ" / "食管胃结合部腺癌" 等）
  - 用户已有 yaml 不覆盖（detect via `if synonym_map.yaml exists: skip`）

- **D-09:** 词表查找走"双向规则化匹配"
  - 输入侧（patient.disease_type）：lower + strip + 移除"癌/瘤/肿瘤"等通用后缀后查表
  - 词表侧：每个 alias 同样 lower/strip 后比对
  - 不做 LLM 模糊匹配；不做 fuzzy edit-distance（避免误判）
  - 命中失败 → 返回 `None`（病种未知），下游 `filter_orgs_by_disease` 保守"全保留"

- **D-10:** `kb_metadata.py` 暴露纯函数 API（无副作用，便于测试）
  ```python
  def normalize_disease(disease_type: str, synonym_map: dict) -> str | None: ...
  def filter_chunks_by_disease(hits: list[dict], chunks_meta: dict, canonical_key: str | None) -> list[dict]: ...
  def filter_orgs_by_disease(coverage: dict, canonical_key: str | None) -> list[str]: ...
  ```
  - Phase 3 `pipeline.py` 直接 import 调用；Phase 1 不接入到 query path（数据交付）

### 文件落点

- **D-11:** 新增 `scripts/kb_metadata.py`（独立模块，~150 行）
  - 含：YAML 加载、词表归一化、chunks.json 生成、coverage 派生、文件名启发式解析
  - `batch_pipeline.py:411-433` 现有 `_extract_disease_keywords` + `filter_orgs_by_disease` Phase 1 **暂不删除**，Phase 3 切换调用后再清理

- **D-12:** `.metadata/` 目录 git 状态 — **不入仓**
  - `.metadata/` 产出在 `$MEDICAL_GUIDELINES_DIR` 下（KB 根），KB 整体已通过环境变量隔离不入仓
  - `synonym_map.yaml` 一旦人工编辑（overrides.yaml），由运维 backup，不要求版本化

### Claude's Discretion

- 单元测试文件结构：`tests/test_retriever_async.py` 与 `tests/test_kb_metadata.py` 的具体 case 划分由 planner 在 Phase 1 plan 中拆解，但需覆盖：
  - retriever：`__aenter__`/`__aexit__` 生命周期、session 失效 retry、Semaphore 限流验证、并发 query 正确性
  - kb_metadata：normalize hit/miss、chunks.json schema、coverage 聚合正确性、空 disease_tags 兜底
- 是否引入 `pytest-asyncio` 还是手写 `asyncio.run` 包装：交 planner 决定（推荐前者，已是社区标准）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents (researcher / planner / executor) MUST read these before planning or implementing.**

### Source of Truth（必读）

- `docs/refactor_plan_2026-05-11.md` — grill-me 13 轮闭环的完整重构方案
  - §四.2 `scripts/retriever.py` 重构 — `AsyncQMDService` 设计、Semaphore、session 失效重试
  - §四.4 `scripts/kb_metadata.py` 新增 — `.metadata/{chunks,org_disease_coverage}.json` schema、synonym_map.yaml 样例、层级匹配算法
  - §九 端到端验证方案 — Phase 1 验证命令清单（pytest + `index` + `ls .metadata/*`）
  - §十.4 病种词表覆盖不全风险及"保守兜底"决策

### Project-Level

- `.planning/PROJECT.md` — v3.1 milestone 范围、key decisions（13 项）、约束
- `.planning/REQUIREMENTS.md` — Phase 1 范围：RTR-01..04（4 条）+ KBM-01..06（6 条），共 10 条
- `.planning/ROADMAP.md` §Phase 1 — Goal、Success Criteria（3 条）、Duration（2d）
- `.planning/STATE.md` — performance baseline 表（QMD 3min 串行 → 30-60s 并发 8）

### 现有代码（Phase 1 修改 / 复用）

- `scripts/retriever.py:43-216` — 当前同步 `QMDService` 全文，待重构入口
- `scripts/batch_pipeline.py:411-433` — `_extract_disease_keywords` + `filter_orgs_by_disease`，词表归一化的现有简化版（Phase 3 切换后清理）
- `scripts/batch_pipeline.py:1249-1335` — `cmd_index` 函数体，扩展点：在 "Index complete" 输出前插入侧车产出步骤
- `tests/test_retriever.py`、`tests/test_qmd_integration.py` — 同步 API 行为基线，**不允许修改**（thin shim 必须通过）

### 上游约束

- `CLAUDE.md` — 知识库路径解析（`MEDICAL_GUIDELINES_DIR` env 优先级）、依赖工具版本（QMD npm、MinerU uv、Docling pip）、QMD 模型配置（embeddinggemma-300M 768 维硬编码）
- `~/.config/qmd/index.yml` — QMD 索引层模型配置（embed/rerank/generate），Phase 1 不修改

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`QMDService._MCP_HEADERS` / `_INIT_PAYLOAD`** (`scripts/retriever.py:26-40`) — 协议常量可直接复用于 `AsyncQMDService`
- **`QMDService._parse_mcp_response`** (`scripts/retriever.py:188-216`) — staticmethod，纯函数无 I/O，异步实现直接 reuse 不需要 async 化
- **`QMDService._check_port_available`** (`scripts/retriever.py:155-161`) — 同步 socket check，复用即可（端口检查同步无成本）
- **`_extract_disease_keywords` + `filter_orgs_by_disease`** (`scripts/batch_pipeline.py:411-433`) — 当前简化词表（硬编码 `cn_to_en` dict），Phase 1 替换为 yaml-driven `kb_metadata.normalize_disease`，但保留旧函数到 Phase 3 切换完成

### Established Patterns

- **Subprocess lifecycle via context manager** (`scripts/retriever.py:63-93`) — `__enter__`/`__exit__` 处理 `subprocess.Popen` + cleanup；异步版需遵循同样的"启动失败立即 kill" 防御模式
- **MCP session-id 单次初始化** (`scripts/retriever.py:163-186`) — `_wait_for_ready` 在轮询健康 + 抓 `mcp-session-id` header 后才返回 ready；异步版保持同一 invariant（`__aenter__` 完成 = session 就绪）
- **`cmd_index` per-org 循环** (`scripts/batch_pipeline.py:1255-1335`) — 已遍历 `kb_root/<org>/extracted/*.md`，扫描结果可直接复用为 chunks.json 输入

### Integration Points

- **`AsyncQMDService` ↔ `QMDService` shim 边界**：`scripts/retriever.py` 顶层文件，新增异步类 + 改写同步类 4 个方法体（`__enter__/__exit__/query/search`）
- **`kb_metadata.py` ↔ `cmd_index` 调用边界**：`scripts/batch_pipeline.py:1335` 行（"Index complete" print 之前）插入 `kb_metadata.build_sidecar(kb_root, orgs_found)` 一次调用
- **`.metadata/` 目录创建**：`$MEDICAL_GUIDELINES_DIR/.metadata/`，由 `kb_metadata.build_sidecar` 用 `mkdir(parents=True, exist_ok=True)` 保证
- **`synonym_map.yaml` 加载**：运行时由 `pipeline.py`（Phase 3）通过 `kb_metadata.load_synonym_map(kb_root)` 读取；Phase 1 只保证文件结构合法

</code_context>

<specifics>
## Specific Ideas

- **`AsyncQMDService` dataclass / fields 参考 `LLMProfile` 形态**（refactor_plan §四.1）：保持 frozen dataclass 风格，构造参数 `port / timeout_s / semaphore / http_client`，方便 Phase 2/3 注入测试桩
- **`synonym_map.yaml` 完整样例已在 refactor_plan §四.4 给出**（含 `gastric / colorectal / neuroendocrine` 三组），seed 文件直接搬用并扩展到 10 种癌种
- **chunks.json schema 与 refactor_plan §四.4 一致**：`{file_path: {disease_tags: [canonical_keys], org: str, guideline_version: str}}` —— `guideline_version` 可从 `extracted/<file>.md` 第一行 H1 标题尾部年份提取（若无则空字符串）
- **测试用 fixture**：用现有 `tests/test_retriever.py` 的 mock 模式（`MockQMDProcess` 类）作为参考，扩展为 `MockAsyncQMDProcess` 或直接复用 + asyncio 包装

</specifics>

<deferred>
## Deferred Ideas

- **LLM 抽取 chunk-level `disease_tags`** — D-06 决策保留为 v3.2+ 增强；当前文件名启发式覆盖 ~90% case 即可
- **`overrides.yaml` 边角 case 手工补丁** — refactor_plan §四.4 提及但 Phase 1 不交付（首次 `index` 不生成空 overrides.yaml，由运维按需手动创建）
- **多病种文件（NCCN 综合指南）的 chunk 级多病种标签** — `MD-02`（v3.2 范围），Phase 1 文件名启发式只取主要病种标签
- **QMD 索引层 metadata filtering** — Out of Scope（QMD 不支持，永久走侧车路径）
- **patient_disease_type 经 LLM 推断**（如临床描述自由文本） — 当前由 `parse` 阶段 Excel 字段直接提供，不在 Phase 1 / Phase 3 范围
- **`.metadata/` 入仓** — D-12 明确不入仓，KB 整体在 `$MEDICAL_GUIDELINES_DIR` 外部，永久 deferred

### Reviewed Todos (not folded)

None — `gsd-sdk query todo.match-phase 1` 返回 0 matches，无未关联 todo 需要处理。

</deferred>

---

*Phase: 1-Async Retriever + KB Metadata Sidecar*
*Context gathered: 2026-05-11*
*Auto-mode: `--auto --chain`（recommended options per refactor_plan §四 决策已闭环）*
