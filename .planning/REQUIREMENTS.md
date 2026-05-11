# Requirements: medical-guidelines-suite

**Defined:** 2026-05-11
**Core Value:** 临床医生输入一次患者信息 → 自动跨指南检索 → 生成结构化循证推荐对比（共识/差异），10 例端到端 <10min

## v1 Requirements

<!-- v3.1 async-pipeline milestone scope. 来源：docs/refactor_plan_2026-05-11.md 13 项决策 + Phase 1-4 success criteria。 -->

### Retriever（异步检索）

- [ ] **RTR-01**: `AsyncQMDService` 用 `httpx.AsyncClient` 处理 HTTP I/O，支持 `async with` 上下文
- [ ] **RTR-02**: 全局 `asyncio.Semaphore(8)` 限 QMD 在飞请求数，CLI `--concurrency-qmd` 可覆盖
- [ ] **RTR-03**: QMD MCP session header `Mcp-Session-Id` 在 `__aenter__` 一次初始化，中途失效自动 re-initialize 重试一次
- [ ] **RTR-04**: 同步 `QMDService` 降级为 thin shim（内部 `asyncio.run`），现有 `tests/test_retriever.py` 与 `test_qmd_integration.py` 不修改通过

### KB Metadata（病种侧车元数据）

- [ ] **KBM-01**: `cmd_index` 构建 QMD 索引时额外产出 `$KB_ROOT/.metadata/chunks.json`（{file_path: {disease_tags, org, guideline_version}}）
- [ ] **KBM-02**: `cmd_index` 同时产出 `$KB_ROOT/.metadata/org_disease_coverage.json`（{org: [canonical_disease_keys]}）
- [ ] **KBM-03**: 首次 `index` 种子化 `$KB_ROOT/.metadata/synonym_map.yaml`（覆盖 KB 现有 ~10 种癌种）
- [ ] **KBM-04**: 病种归一化：`patient.disease_type` → canonical_key 走 synonym_map（"胃腺癌" → "gastric"）
- [ ] **KBM-05**: 双层过滤：org 级前置（`org_disease_coverage`）+ chunk 级后置（`chunks.json`）
- [ ] **KBM-06**: chunk_keys 为空（标签缺失）→ 保留（保守兜底，避免误丢）

### LLM Client（结构化输出）

- [ ] **LLM-01**: `AsyncLLMClient.complete_structured(messages, schema)` 走 OpenAI 兼容 `response_format={"type":"json_schema","strict":true}`
- [ ] **LLM-02**: `PATIENT_RECOMMENDATION_SCHEMA` 含 `evidence_level` 完整枚举（Category 1-3 / I-V,A-E / I-III级 / 1A-3 类 / 强弱推荐 / N/A，~20+ 变体）
- [ ] **LLM-03**: `LLMProfile` 数据类支持多 profile（qwen3-vllm-lan 主，deepseek-cloud fallback）
- [ ] **LLM-04**: 重试策略：429/5xx/timeout 指数退避 3 次；schema 失败重试一次；citation_coverage<0.5 带反馈再调一次仍不达标接受并标 `status: "partial"`
- [ ] **LLM-05**: 最终失败抛 `LLMFailure(patient_id, last_error, stage)` 让上游捕获

### Pipeline（按患者并发流水线）

- [ ] **PIP-01**: `scripts/pipeline.py:run_pipeline` 顶层 `asyncio.TaskGroup` + 患者级 `asyncio.Semaphore(5)`
- [ ] **PIP-02**: 单患者子流水线 `_run_one_patient`：extract_features → build_queries → `asyncio.gather(qmd.query × N)` → dedupe + 双层过滤 → build_patient_prompt(system+user) → llm.complete_structured → 算 citation_coverage → 写 shard
- [ ] **PIP-03**: 成功患者写 `Output/patients/<patient_id>.json`，失败写 `Output/_failed/<patient_id>.json`（含 error/stage/last_llm_output），互不影响
- [ ] **PIP-04**: `--resume` 模式 shard 存在跳过；`_failed/` 中的患者自动重试
- [ ] **PIP-05**: 退出码：所有 patient PASS（含 partial）→ 0；任一 patient 进 `_failed/` → 1
- [ ] **PIP-06**: `run` 末尾合并产出 `Output/rag_results.json` 作为派生 aggregate

### CLI（命令折叠）

- [ ] **CLI-01**: 新增 `run` 子命令：`--patients --output-dir --llm-profile --concurrency-patients --concurrency-qmd --resume`
- [ ] **CLI-02**: `validate` 新增 `--patients-dir` 路径，保留 `--input` 兼容 deprecated
- [ ] **CLI-03**: `generate` 新增 `--patients-dir` 路径，保留 `--input` 兼容 deprecated
- [ ] **CLI-04**: `index` 扩展为同时产出 `.metadata/*.json` 侧车
- [ ] **CLI-05**: Phase 3 期间旧子命令（split/orchestrate/verify-batch/merge）注册为 hidden（`--help` 不显示但仍可调用）
- [ ] **CLI-06**: Phase 4 一次性删除 `cmd_orchestrate`/`_auto_split_batch`/`cmd_split`/`cmd_verify_batch` 及对应测试文件

### Configuration（profile + env）

- [ ] **CFG-01**: env 变量 `LLM_PROFILE / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_TIMEOUT / LLM_STRUCTURED_MODE / LLM_CONCURRENCY` 全部 wired
- [ ] **CFG-02**: 可选 `config/llm_profiles.yaml`（env 优先级 > yaml）支持 qwen3-vllm-lan / deepseek-cloud 两个 profile
- [ ] **CFG-03**: env `PIPELINE_CONCURRENCY_PATIENTS / PIPELINE_CONCURRENCY_QMD` 与 CLI flag 互通

### Quality Gates（端到端验证）

- [ ] **QG-01**: 10 例 fixture（`Input/2026-4-23.xlsx`）端到端 wall time <10min
- [ ] **QG-02**: 所有 10 例 `validate` 0 FAIL，`citation_coverage` ≥ 0.5（或标 `partial` 后被接受）
- [ ] **QG-03**: 结直肠癌 3 例（贾常山/李学/肖庆周）的 ESMO/JGCA/CACA `guideline_results` 不含"胃癌"字样
- [ ] **QG-04**: 全部 `evidence_level` 落在 schema enum 内（jq 验证）
- [ ] **QG-05**: 零 JSON 解析错误（schema strict 服务端强制）
- [ ] **QG-06**: `pytest tests/` 全绿，与现有 173 测试基线对齐
- [ ] **QG-07**: `batch_pipeline.py --help` Phase 4 后只剩 5 个子命令（parse / run / validate / generate / index）

## v2 Requirements

<!-- v3.2+ deferred。 -->

### Streaming

- **STR-01**: LLM 流式输出（chat completions stream=True）
- **STR-02**: 患者级进度 SSE/WebSocket 推送

### Observability

- **OBS-01**: 结构化日志（loguru/structlog），含 patient_id / stage / latency
- **OBS-02**: Prometheus metrics endpoint（qmd_hits、llm_latency、citation_coverage 分布）
- **OBS-03**: 失败原因聚类报告（每周）

### Multi-Disease

- **MD-01**: 单患者多病种（如胃癌+肝转移）拆分独立查询并合并
- **MD-02**: NCCN 综合指南的 chunk 级多病种标签

## Out of Scope

| Feature | Reason |
|---------|--------|
| 托管 LLM API 主路径（OpenAI/Claude API） | 数据合规要求内网，仅作 fallback profile |
| QMD 索引层 metadata filtering | QMD 不支持，应用层侧车 JSON 已覆盖 |
| 患者数据持久化 DB | Output/ 文件即结果，DB 引入合规复杂度 |
| 实时流式 JSON | strict schema 必须一次完整输出，流式留 v3.2 |
| GUI / Web 前端 | CLI + Markdown 报告满足临床团队，Web 待用户验证 |
| 多病种患者拆分（v3.1） | 当前按主诊断 `disease_type` 匹配，v3.2 再做 |

## Traceability

<!-- 由 roadmapper 在 Phase 8 填充。empty initially。 -->

| Requirement | Phase | Status |
|-------------|-------|--------|
| RTR-01 | TBD | Pending |
| RTR-02 | TBD | Pending |
| RTR-03 | TBD | Pending |
| RTR-04 | TBD | Pending |
| KBM-01 | TBD | Pending |
| KBM-02 | TBD | Pending |
| KBM-03 | TBD | Pending |
| KBM-04 | TBD | Pending |
| KBM-05 | TBD | Pending |
| KBM-06 | TBD | Pending |
| LLM-01 | TBD | Pending |
| LLM-02 | TBD | Pending |
| LLM-03 | TBD | Pending |
| LLM-04 | TBD | Pending |
| LLM-05 | TBD | Pending |
| PIP-01 | TBD | Pending |
| PIP-02 | TBD | Pending |
| PIP-03 | TBD | Pending |
| PIP-04 | TBD | Pending |
| PIP-05 | TBD | Pending |
| PIP-06 | TBD | Pending |
| CLI-01 | TBD | Pending |
| CLI-02 | TBD | Pending |
| CLI-03 | TBD | Pending |
| CLI-04 | TBD | Pending |
| CLI-05 | TBD | Pending |
| CLI-06 | TBD | Pending |
| CFG-01 | TBD | Pending |
| CFG-02 | TBD | Pending |
| CFG-03 | TBD | Pending |
| QG-01 | TBD | Pending |
| QG-02 | TBD | Pending |
| QG-03 | TBD | Pending |
| QG-04 | TBD | Pending |
| QG-05 | TBD | Pending |
| QG-06 | TBD | Pending |
| QG-07 | TBD | Pending |

**Coverage:**
- v1 requirements: 37 total
- Mapped to phases: 0 (filled by roadmapper)
- Unmapped: 37 ⚠️ (pending roadmap)

---
*Requirements defined: 2026-05-11 (source: docs/refactor_plan_2026-05-11.md grill-me 13-round closure)*
*Last updated: 2026-05-11 after initialization*
