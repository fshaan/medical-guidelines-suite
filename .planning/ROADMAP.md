# Roadmap: medical-guidelines-suite

**Milestone:** v3.1 async-pipeline
**Defined:** 2026-05-11
**Granularity:** coarse
**Source of truth:** [docs/refactor_plan_2026-05-11.md](../docs/refactor_plan_2026-05-11.md) (grill-me 13-round closure)

## Core Value

临床医生输入一次患者信息 → 自动跨指南检索 → 生成结构化循证推荐对比，10 例端到端 <10min（当前 ~60min 中 LLM 人工 50min + QMD 串行 3min）。

## Phases

- [ ] **Phase 1: Async Retriever + KB Metadata Sidecar** — 异步化 QMD 检索层并产出病种侧车元数据
- [ ] **Phase 2: LLM Client + Schema + Unit Tests** — 接入内网 vLLM，定义 strict JSON Schema，单元测试覆盖
- [ ] **Phase 3: Pipeline + run Subcommand + Interface Extension** — 端到端 async 流水线落地（关键 gate）
- [ ] **Phase 4: Delete Batch Concept + Rewrite Tests** — 清理旧 7 阶段产物，CLI 折叠为 5 子命令

## Summary

| # | Phase | Goal | Requirements | Success Criteria | Duration |
|---|-------|------|--------------|------------------|----------|
| 1 | Async Retriever + KB Metadata Sidecar | QMD 检索异步化 + 病种侧车元数据生成 | RTR-01..04, KBM-01..06 (10) | 3 | 2d |
| 2 | LLM Client + Schema + Unit Tests | 内网 vLLM 客户端 + strict schema + 重试策略 | LLM-01..05, CFG-01, CFG-02 (7) | 3 | 2d |
| 3 | Pipeline + run + Interface Extension | async 总编排器落地，10 例 <10min | PIP-01..06, CLI-01..05, CFG-03, QG-01..06 (18) | 5 | 3d |
| 4 | Delete Batch Concept + Rewrite Tests | 旧 7 阶段命令一次性删除，CLI 收敛为 5 个 | CLI-06, QG-07 (2) | 3 | 1d |

## Phase Details

### Phase 1: Async Retriever + KB Metadata Sidecar

**Goal:** QMD 检索层异步化（`AsyncQMDService` + 全局信号量），同时在索引阶段产出病种侧车元数据（`$KB_ROOT/.metadata/*.json` + `synonym_map.yaml`），为后续双层病种过滤打基础。

**Depends on:** Nothing (first phase)

**Requirements:** RTR-01, RTR-02, RTR-03, RTR-04, KBM-01, KBM-02, KBM-03, KBM-04, KBM-05, KBM-06

**Success Criteria** (what must be TRUE):
  1. `pytest tests/test_retriever_async.py tests/test_retriever.py tests/test_kb_metadata.py -v` 全绿，且已有 `tests/test_qmd_integration.py` 不修改通过（同步 `QMDService` 作为 thin shim 行为不变）
  2. 运行 `batch_pipeline.py index --kb-root $MEDICAL_GUIDELINES_DIR` 后，`$KB_ROOT/.metadata/chunks.json`、`$KB_ROOT/.metadata/org_disease_coverage.json`、`$KB_ROOT/.metadata/synonym_map.yaml` 三个文件实际生成且 schema 合法
  3. `synonym_map.yaml` 覆盖 KB 现有 ~10 种癌种（gastric / colorectal / neuroendocrine 等），病种归一化 `"胃腺癌" → "gastric"` 在单元测试中通过；chunk_keys 为空时保守保留

**Duration estimate:** 2 days

**Plans:** TBD

### Phase 2: LLM Client + Schema + Unit Tests

**Goal:** 新增 `scripts/llm_client.py`，以 `AsyncLLMClient.complete_structured(messages, schema)` 走 OpenAI 兼容 `json_schema strict=true`，定义 `PATIENT_RECOMMENDATION_SCHEMA`（`evidence_level` 完全枚举 20+ 变体），并配齐重试 / feedback / failure 策略与多 profile 配置。

**Depends on:** Phase 1（共享 `httpx.AsyncClient` 风格 + Semaphore 模式）

**Requirements:** LLM-01, LLM-02, LLM-03, LLM-04, LLM-05, CFG-01, CFG-02

**Success Criteria** (what must be TRUE):
  1. 对内网 vLLM（`qwen3-vllm-lan` profile）跑 1 例 fixture（`tests/fixtures/patient_001.json`），stdout 是 schema-valid JSON，所有 `evidence_level` 落在 enum 内
  2. 单元测试覆盖三条重试路径：429/5xx/timeout 指数退避 3 次、schema 失败重试一次、citation_coverage<0.5 带反馈重试一次后接受并标 `status: "partial"`；最终失败抛 `LLMFailure(patient_id, last_error, stage)`
  3. `LLM_PROFILE / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_TIMEOUT / LLM_STRUCTURED_MODE / LLM_CONCURRENCY` 全部 env wired；可选 `config/llm_profiles.yaml` 支持 `qwen3-vllm-lan`（主）/ `deepseek-cloud`（fallback）两个 profile，env 优先级 > yaml

**Duration estimate:** 2 days

**Plans:** TBD

### Phase 3: Pipeline + run Subcommand + Interface Extension

**Goal:** 新增 `scripts/pipeline.py:run_pipeline` 作为按患者并发的总编排器（顶层 `asyncio.TaskGroup` + 患者级 `Semaphore(5)`），通过 `batch_pipeline.py run` 子命令暴露；扩展 `validate / generate` 的 `--patients-dir` 接口；旧子命令注册为 hidden。这是 milestone 的关键 gate——10 例 fixture 端到端 <10min 在此达成。

**Depends on:** Phase 2（依赖 `AsyncLLMClient` 与 `AsyncQMDService` 已就位）

**Requirements:** PIP-01, PIP-02, PIP-03, PIP-04, PIP-05, PIP-06, CLI-01, CLI-02, CLI-03, CLI-04, CLI-05, CFG-03, QG-01, QG-02, QG-03, QG-04, QG-05, QG-06

**Success Criteria** (what must be TRUE):
  1. `time batch_pipeline.py run --patients Output/patients.json --output-dir Output/ --concurrency-patients 5 --llm-profile qwen3-vllm-lan` 在 10 例 fixture（`Input/2026-4-23.xlsx`）wall time **<10 min**
  2. 派生 `Output/rag_results.json` 与现产物结构等价；`Output/patients/<patient_id>.json` × 10 全部生成；失败患者进 `Output/_failed/`（含 error/stage/last_llm_output）；`--resume` 模式 shard 存在跳过、`_failed/` 自动重试；退出码语义正确（全 PASS→0，任一失败→1）
  3. 所有 10 例 `validate --patients-dir Output/patients/` **0 FAIL**，`citation_coverage` ≥ 0.5（或标 `partial` 后被接受）
  4. 结直肠癌 3 例（贾常山/李学/肖庆周）的 ESMO/JGCA/CACA `guideline_results` **不含"胃癌"字样**；全部 `evidence_level` 落在 schema enum 内（jq 验证）；**零 JSON 解析错误**（schema strict 服务端强制）
  5. `pytest tests/` 全绿，与现有 173 测试基线对齐；旧 split/orchestrate/verify-batch/merge 子命令注册为 hidden（`--help` 不显示但仍可调用，保留 stabilize 期回退能力）

**Duration estimate:** 3 days

**Plans:** TBD

### Phase 4: Delete Batch Concept + Rewrite Tests

**Goal:** Phase 3 ship 并 stabilize 一周后，一次性删除 `cmd_orchestrate / _auto_split_batch / cmd_split / cmd_verify_batch` 及对应 4 个测试文件，更新文档，使 `batch_pipeline.py --help` 收敛到 5 个子命令。

**Depends on:** Phase 3 ship 后 **stabilize 一周**（避免旧调用方未迁移完成就被删）

**Requirements:** CLI-06, QG-07

**Success Criteria** (what must be TRUE):
  1. `pytest tests/` 全绿，旧测试文件（`tests/test_orchestrate.py`、`tests/test_split.py`、`tests/test_verify_batch.py`、`tests/test_prompt.py`）已删除，per-patient 版替代测试已就位
  2. `batch_pipeline.py --help` 只剩 **5 个子命令**：`parse / run / validate / generate / index`；`cmd_orchestrate / _auto_split_batch / cmd_split / cmd_verify_batch` 源码已删除
  3. `SKILL.md`、`README.md`、`CHANGELOG.md` 同步更新为「4 阶段流水线」叙事，旧 7 阶段表述全部清理

**Duration estimate:** 1 day

**Plans:** TBD

## Dependencies

```
Phase 1 → Phase 2 → Phase 3 → [stabilize 1 week] → Phase 4
```

Sequential execution. Phase 3 是关键性能 gate；Phase 4 显式延迟一周以验证 production 稳定性后再清理旧代码。

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Async Retriever + KB Metadata Sidecar | 0/? | Not started | - |
| 2. LLM Client + Schema + Unit Tests | 0/? | Not started | - |
| 3. Pipeline + run + Interface Extension | 0/? | Not started | - |
| 4. Delete Batch Concept + Rewrite Tests | 0/? | Not started | - |

## Coverage

- v1 requirements: 37 total
- Mapped to phases: 37 ✓
- Unmapped: 0

---
*Roadmap created: 2026-05-11 (transcribed from docs/refactor_plan_2026-05-11.md §七 实施分阶段 + §九 端到端验证方案)*
