---
phase: 01
slug: async-retriever-kb-metadata-sidecar
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-05-12
---

# Phase 01 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Reconstructed retroactively (State B) after Phase 1 完成 + review-feedback 闭环。

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 0.26 (strict mode) |
| **Config file** | none — bare discovery; tests/conftest.py 提供共享 fixtures |
| **Quick run command** | `python3 -m pytest tests/test_retriever_async.py tests/test_kb_metadata.py tests/test_index.py -q` |
| **Full suite command** | `python3 -m pytest tests/ -q` |
| **Estimated runtime** | ~2 seconds（mock-only，无 QMD 启动） |

---

## Sampling Rate

- **After every task commit:** quick run（async + kb_metadata + index 子集）
- **After every plan wave:** 全套件
- **Before `/gsd-verify-work`:** 全套件 green
- **Max feedback latency:** 3 秒

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-A1 | 01 | 1 | RTR-01 | — | AsyncQMDService 异步上下文管理器 lifecycle 完整 | unit | `python3 -m pytest tests/test_retriever_async.py::test_async_qmd_service_lifecycle -v` | ✅ | ✅ green |
| 01-01-A2 | 01 | 1 | RTR-02 | — | Semaphore(8) 限流 + 注入式 semaphore 覆盖 | unit | `python3 -m pytest tests/test_retriever_async.py::test_async_qmd_semaphore_limits_in_flight -v` | ✅ | ✅ green |
| 01-01-A3 | 01 | 1 | RTR-03 | — | session header 400 → reinit + 重发；200 + no header 不触发 | unit | `python3 -m pytest tests/test_retriever_async.py::test_async_qmd_session_invalid_retries_once tests/test_retriever_async.py::test_async_qmd_200_no_session_header_does_not_retry -v` | ✅ | ✅ green |
| 01-01-A4 | 01 | 1 | RTR-04 | — | sync `QMDService` 4 测试零修改通过（D-01 物理共存验证） | unit | `python3 -m pytest tests/test_retriever.py -v` | ✅ | ✅ green |
| 01-01-A5 | 01 | 1 | RTR-01 | — | startup transport error 容忍（ReadTimeout/RemoteProtocolError 不绕过 deadline） | unit | `python3 -m pytest tests/test_retriever_async.py::test_async_qmd_wait_for_ready_tolerates_read_timeout -v` | ✅ | ✅ green |
| 01-01-A6 | 01 | 1 | RTR-01 | — | __aexit__ subprocess 清理在 aclose 异常下不泄漏；不覆盖 caller 异常 | unit | `python3 -m pytest tests/test_retriever_async.py -k "aclose_error" -v` | ✅ | ✅ green |
| 01-02-B1 | 02 | 2 | KBM-03 | T-02-01 | seed_synonym_map 首次落盘；已存在跳过保护人工 overrides | unit | `python3 -m pytest tests/test_kb_metadata.py -k "seed_synonym_map" -v` | ✅ | ✅ green |
| 01-02-B2 | 02 | 2 | KBM-04 | — | normalize_disease：zh/en/case/suffix/miss/empty 6 边界 | unit | `python3 -m pytest tests/test_kb_metadata.py -k "normalize" -v` | ✅ | ✅ green |
| 01-02-B3 | 02 | 2 | KBM-05 | — | filter_orgs / filter_chunks 双层过滤命中与不命中分支 | unit | `python3 -m pytest tests/test_kb_metadata.py -k "filter_" -v` | ✅ | ✅ green |
| 01-02-B4 | 02 | 2 | KBM-06 | — | 空 disease_tags / 缺失 meta / canonical=None → 全保留（保守兜底） | unit | `python3 -m pytest tests/test_kb_metadata.py -k "keep_when_tags_empty or keep_when_meta_missing or all_miss_returns_all or none_returns_all" -v` | ✅ | ✅ green |
| 01-02-B5 | 02 | 2 | KBM-01,02 | T-02-02,T-02-03 | build_sidecar 三文件落盘；chunks key qmd:// URL lowercase；org 字段 lowercase 防 PHI 泄露 | unit | `python3 -m pytest tests/test_kb_metadata.py -k "build_sidecar" -v` | ✅ | ✅ green |
| 01-02-B6 | 02 | 2 | KBM-01 | — | sidecar 原子 tmp+rename 写盘，.tmp 不残留（WR-05 修复） | unit | `python3 -m pytest tests/test_kb_metadata.py::test_build_sidecar_writes_atomically_no_tmp_residue -v` | ✅ | ✅ green |
| 01-02-B7 | 02 | 2 | KBM-04 | — | _extract_year 取最大值 + 1990-2100 区间过滤（IN-02 修复） | unit | `python3 -m pytest tests/test_kb_metadata.py -k "extract_year" -v` | ✅ | ✅ green |
| 01-02-B8 | 02 | 2 | KBM-04 | — | infer_chunk_tags WR-04 known false-positive 边界 documenting test | unit | `python3 -m pytest tests/test_kb_metadata.py::test_infer_chunk_tags_known_false_positive_on_organ_token -v` | ✅ | ✅ green |
| 01-03-C1 | 03 | 2 | KBM-01,02 | — | cmd_index 端到端：QMD collection/embed/context 调用计数 | integration | `python3 -m pytest tests/test_index.py::test_cmd_index_creates_collections_and_contexts -v` | ✅ | ✅ green |
| 01-03-C2 | 03 | 2 | KBM-01,02 | — | cmd_index 入口集成：.metadata/{chunks,coverage}.json + synonym_map.yaml 真的写盘 | integration | `python3 -m pytest tests/test_index.py::test_cmd_index_invokes_build_sidecar_and_writes_metadata -v` | ✅ | ✅ green |
| 01-03-C3 | 03 | 2 | KBM-01,02 | — | build_sidecar 抛错时 cmd_index warn-only 不 abort（Metal 134 容忍精神对齐） | integration | `python3 -m pytest tests/test_index.py::test_cmd_index_continues_when_build_sidecar_fails -v` | ✅ | ✅ green |
| 01-X-D1 | review | feedback | — | — | batch_pipeline qmd:// org URL 解析（codex P1 修复） | unit | `python3 -m pytest tests/test_batch_pipeline_org_filter.py -v` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing infrastructure covers all phase requirements.*

- pytest 8.x + pytest-asyncio 0.26 已在 requirements.txt 中（Phase 1 Plan 01 引入）
- tests/conftest.py 已含 `mock_kb` 共享 fixture
- 无新增 framework 安装需要

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| CLI `--concurrency-qmd` 参数实际生效 | RTR-02 (CLI 部分) | Phase 3 范围（CLI-01 引入 `run` 子命令），Phase 1 仅交付 retriever 层 Semaphore 注入 | Phase 3 落地后用 `python3 scripts/batch_pipeline.py run --concurrency-qmd 4 ...` 跑真实 KB 端到端，观察并发上限 |
| 真实 KB 端到端 index → orchestrate 流程 | KBM-01..06 综合 | mock_kb fixture 覆盖逻辑分支；与真实 QMD 服务 + 真实 .md 文件的 integration 需要本地 QMD 启动 | `MEDICAL_GUIDELINES_DIR=/Users/f.sh/MyDocuments/RAG/guidelines python3 scripts/batch_pipeline.py index` 然后 ls $MEDICAL_GUIDELINES_DIR/.metadata/ 确认三文件存在 |
| WR-04 短词误命中实际影响评估 | KBM-04 (边界) | 需要真实 KB 文件名分布统计 false-positive 比率，自动化难以判断「误命中是否实际造成 patient-level 错检索」 | Phase 3 pipeline 跑通后，抽 5-10 例 patient 输出，对照 disease_type 和最终 retrieval_results 中 org 一致性 |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify（17 个 task slot，全部自动化）
- [x] Wave 0 covers all MISSING references（无 missing）
- [x] No watch-mode flags
- [x] Feedback latency < 3s（实测 ~2s）
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-05-12

---

## Validation Audit 2026-05-12

| Metric | Count |
|--------|-------|
| Gaps found | 2 |
| Resolved | 2 |
| Escalated to Manual-Only | 1 (RTR-02 CLI 参数，Phase 3 范围) |
| Tests added in this audit | 2（test_cmd_index_invokes_build_sidecar_and_writes_metadata、test_cmd_index_continues_when_build_sidecar_fails） |
| Full suite after audit | 218 passed, 4 skipped, 0 failed |

### Coverage Summary by Requirement

| Req | Status | Test Count |
|---|---|---|
| RTR-01 | ✅ COVERED | 3 (lifecycle + startup transport tolerance + aexit cleanup) |
| RTR-02 | ✅ COVERED (retriever 层) / ⚠️ Manual (CLI 部分推 Phase 3) | 1 unit + 1 manual |
| RTR-03 | ✅ COVERED | 2 (session retry + reverse no-header) |
| RTR-04 | ✅ COVERED | 4 sync tests (test_retriever.py 未修改) |
| KBM-01 | ✅ COVERED | 3 (build_sidecar unit + cmd_index integration + warn-only) |
| KBM-02 | ✅ COVERED | 3 (build_sidecar unit + cmd_index integration + warn-only) |
| KBM-03 | ✅ COVERED | 2 (creates + skips) |
| KBM-04 | ✅ COVERED | 5 + 3 boundary (normalize + year extraction + organ false-positive boundary) |
| KBM-05 | ✅ COVERED | 4 (orgs/chunks 双层 × hit/miss) |
| KBM-06 | ✅ COVERED | 4 (空 tags/meta/canonical=None 兜底) |

10/10 requirements have automated verification（含 review feedback 反向回归测试 11 个）。
