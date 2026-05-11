# Phase 1: Async Retriever + KB Metadata Sidecar - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `01-CONTEXT.md` — this log preserves the alternatives considered.

**Date:** 2026-05-11
**Phase:** 1 — Async Retriever + KB Metadata Sidecar
**Mode:** `--auto --chain`（autonomous selection of recommended options，每条 logged as `[auto] ... → Selected: ...`）
**Areas discussed:** Async retriever 接口策略、KB metadata 侧车产出时机、synonym_map.yaml 设计、文件落点

---

## Async Retriever 接口策略

| Option | Description | Selected |
|--------|-------------|----------|
| 同步 `QMDService` 降级为 thin shim（`asyncio.run` 包装异步实现） | 单一真实源；现有 sync 测试零修改通过 | ✓ |
| sync + async 双实现并存 | 性能略好（sync 无事件循环开销）但代码重复 + 行为 drift 风险 | |
| 完全删除 sync 类，强制所有调用方迁移 | 影响 `tests/test_retriever.py` 与 `test_qmd_integration.py`（173 测试基线破坏） | |

**Auto-selected:** thin shim
**Rationale:** `refactor_plan_2026-05-11.md §四.2` 明确 "同步 `QMDService` 保留为 thin shim（内部 `asyncio.run`）"；Phase 1 success criterion #1 要求"已有 `tests/test_qmd_integration.py` 不修改通过"。

---

## QMD 子进程异步化范围

| Option | Description | Selected |
|--------|-------------|----------|
| 仅 HTTP I/O 异步，子进程启动/停止保持同步 | 一次 pipeline 一个 server，启动 ~1-2s single-shot 无并发收益 | ✓ |
| 全异步（`asyncio.create_subprocess_exec`） | 引入额外复杂度（async signal handling），无实际性能收益 | |

**Auto-selected:** 仅 HTTP I/O 异步
**Rationale:** `refactor_plan §四.2` 明确"QMD 子进程启动/停止仍然同步（一个 pipeline run 一个 QMD server）"。

---

## 全局并发 Semaphore 注入模式

| Option | Description | Selected |
|--------|-------------|----------|
| `AsyncQMDService.__init__(semaphore=None)` 注入式，None 时内部 `Semaphore(8)` | Phase 3 `pipeline.py` 共享同一 sem 时无需 monkeypatch | ✓ |
| 硬编码 `Semaphore(8)` 实例字段 | 简单但 Phase 3 无法共享外部 sem | |
| 模块级全局 sem | 多 `AsyncQMDService` 实例时不可控 | |

**Auto-selected:** 注入式
**Rationale:** `REQUIREMENTS.md RTR-02` 要求 CLI `--concurrency-qmd` 可覆盖；注入式满足 Phase 3 时 `pipeline.py` 用同一 sem 串联 QMD 调用的需求。

---

## MCP session 失效重试策略

| Option | Description | Selected |
|--------|-------------|----------|
| 失效检测 = HTTP 400 OR response 缺 session header，retry 一次 re-init 后重发 | 与 refactor_plan §四.2 字面一致 | ✓ |
| 无限重试直到成功 | 风险：QMD 永久挂死会无限循环 | |
| 不重试，直接抛 | 短暂网络抖动也会失败，UX 差 | |

**Auto-selected:** retry 一次 re-init
**Rationale:** `REQUIREMENTS.md RTR-03` 明确"中途失效自动 re-initialize 重试一次"。

---

## KB Metadata 侧车产出时机

| Option | Description | Selected |
|--------|-------------|----------|
| `cmd_index` 一次性产出 `.metadata/{chunks,org_disease_coverage}.json` + `synonym_map.yaml` | 与 QMD `embed` 同步；运行时只读 | ✓ |
| `pipeline run` 首次执行时 lazy 构建 | 把 I/O 压到 critical path；race condition 风险 | |
| 独立 `metadata` 子命令手动触发 | 多一个步骤，运维易忘 | |

**Auto-selected:** `cmd_index` 一次性产出
**Rationale:** `REQUIREMENTS.md KBM-01/KBM-02` 明确"`cmd_index` 构建 QMD 索引时**额外**产出"。

---

## chunk-level `disease_tags` 推断算法

| Option | Description | Selected |
|--------|-------------|----------|
| 文件名 + 路径启发式（KB 已按 `<org>/extracted/<org>-<disease>-...md` 命名） | 零依赖、即时可跑、覆盖 ~90% case | ✓ |
| LLM 抽取 `.md` 首段后归一化 | 额外 LLM 依赖、Phase 1 不应引入 | |
| 手工标注 `disease_tags.yaml` | 97 份指南纯手工维护成本高 | |

**Auto-selected:** 文件名启发式
**Rationale:** Phase 1 不依赖 LLM（LLM 客户端在 Phase 2 落地）；KB 现有命名约定足以覆盖主体场景，未命中 case 走"保留"兜底（KBM-06）。

---

## `org_disease_coverage.json` 数据来源

| Option | Description | Selected |
|--------|-------------|----------|
| 从 `chunks.json` 聚合派生（`set(chunk.disease_tags)` per org） | 单一真相，无 drift 风险 | ✓ |
| 手工维护静态 yaml | 与 chunks.json 容易 drift；新增指南需手动维护两处 | |

**Auto-selected:** 聚合派生
**Rationale:** Single source of truth 原则；refactor_plan §四.4 样例值（`"ESMO": ["gastric"]`、`"NCCN": ["gastric","colorectal","rectal","esophageal"]`）天然适合从 chunks 聚合得到。

---

## synonym_map.yaml 种子化范围

| Option | Description | Selected |
|--------|-------------|----------|
| 种子覆盖 KB 现有 ~10 种癌种 + 已存在 yaml 跳过覆盖 | 满足 KBM-03；保护运维已编辑内容 | ✓ |
| 全量 NCCN 主要癌种预填（~40+ 种） | 超出当前 KB 范围，多数无对应 chunk 浪费 | |
| 空文件让运维手动填充 | KBM-03 success criterion #3 要求"覆盖 KB 现有 ~10 种癌种" | |

**Auto-selected:** 种子化 ~10 种 + 已存在跳过
**Rationale:** `ROADMAP.md Phase 1 Success Criteria #3` 明确"synonym_map.yaml 覆盖 KB 现有 ~10 种癌种"；保护已存在文件避免覆盖运维 overrides。

---

## 词表匹配算法

| Option | Description | Selected |
|--------|-------------|----------|
| 双向规则化匹配（lower + strip 通用后缀 "癌/瘤/肿瘤"） | 简单、确定性、易测试 | ✓ |
| fuzzy edit-distance 匹配 | 误判风险（"乳腺癌" vs "前列腺癌"距离短） | |
| LLM 语义匹配 | 引入 LLM 依赖，Phase 1 不应有 | |

**Auto-selected:** 规则化匹配
**Rationale:** D-09 — 临床场景下命中失败保守返回 `None`（KBM-06 兜底"保留"），不引入 fuzzy 误判风险。

---

## `kb_metadata.py` 模块位置 + API 形态

| Option | Description | Selected |
|--------|-------------|----------|
| 独立 `scripts/kb_metadata.py` 纯函数 API（no side effects） | 易测试、Phase 3 import 简单 | ✓ |
| 并入 `scripts/batch_pipeline.py` | `batch_pipeline.py` 已 2300+ 行，再加 ~150 行恶化可读性 | |
| 并入 `scripts/retriever.py` | 与检索逻辑解耦不清，扩展性差 | |

**Auto-selected:** 独立模块
**Rationale:** refactor_plan §四 显式列出 `scripts/kb_metadata.py` 为新增模块；纯函数 API（`normalize_disease`、`filter_chunks_by_disease`、`filter_orgs_by_disease`）便于 Phase 3 `pipeline.py` 直接 import。

---

## 旧 `_extract_disease_keywords` / `filter_orgs_by_disease` 清理时机

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 1 暂不删除，Phase 3 切换调用后再清理 | 避免影响现行 `cmd_orchestrate` 路径 | ✓ |
| Phase 1 直接替换 `batch_pipeline.py:411-433` | Phase 3 `pipeline.py` 接入前会让 `cmd_orchestrate` 中断 | |

**Auto-selected:** 延迟清理
**Rationale:** v3.1 重构需保持现 7 阶段流水线在 Phase 3 ship 前可用，避免 Phase 1 修改影响线上调用方。

---

## `.metadata/` git 入仓决策

| Option | Description | Selected |
|--------|-------------|----------|
| 不入仓（在 `$MEDICAL_GUIDELINES_DIR` 下，KB 整体已通过 env 隔离） | 与 KB 数据隐私边界一致 | ✓ |
| 入仓 `synonym_map.yaml`（词表是结构化知识，非患者数据） | 路径在 KB 根 `.metadata/` 下，要入仓需移到项目内 + 双写复杂度 | |

**Auto-selected:** 不入仓
**Rationale:** `CLAUDE.md` 与 `devs/CLAUDE.md` 数据边界要求"患者数据/PHI 不入 git"；`.metadata/` 整体作为 KB 派生物，运维需要时手动 backup `synonym_map.yaml`。

---

## Claude's Discretion（交 planner）

- 单元测试 case 拆分粒度：`tests/test_retriever_async.py` 与 `tests/test_kb_metadata.py` 的具体测试用例由 Phase 1 planner 在 PLAN.md 中拆解
- 是否引入 `pytest-asyncio`：推荐引入（社区标准），但具体配置交 planner
- `httpx.AsyncClient` 客户端实例化位置：连接池 reuse vs per-request 由 planner 评估（推荐 reuse）

## Deferred Ideas

参见 `01-CONTEXT.md` `<deferred>` 章节：LLM 抽取 chunk-level tags（v3.2+）、`overrides.yaml`（运维按需）、多病种文件 chunk 级多 tags（MD-02）、QMD 索引层 filter（永久 deferred）。

---

*Discussion mode: `--auto --chain` — single-pass autonomous selection per `modes/auto.md` rules.*
*Source of recommendations: `docs/refactor_plan_2026-05-11.md` §四 (grill-me 13-round closure).*
