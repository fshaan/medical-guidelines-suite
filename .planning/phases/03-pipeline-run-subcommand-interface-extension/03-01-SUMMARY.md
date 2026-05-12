---
phase: 03-pipeline-run-subcommand-interface-extension
plan: 01
subsystem: pipeline
tags: [asyncio, httpx, gather, semaphore, qmd, llm, pipeline]

requires:
  - phase: 01-async-retriever-kb-metadata-sidecar
    provides: AsyncQMDService (注入式 httpx + semaphore), kb_metadata 双层过滤
  - phase: 02-llm-client-schema-unit-tests
    provides: AsyncLLMClient + LLMProfile + complete_structured_with_feedback + PATIENT_RECOMMENDATION_SCHEMA

provides:
  - scripts/pipeline.py: run_pipeline + _run_one_patient + 11 helper functions
  - 患者级并发编排器（asyncio.gather + patient Semaphore）
  - compute_citation_coverage 算法（[n] 引用覆盖率）
  - _scan_resume 两段扫描（跳过已完成 / 重试 _failed）
  - _merge_rag_results 派生聚合
  - 23 个新单元/集成测试（16 helper + 7 pipeline）

affects: [03-02-CLI-extension, 03-03-E2E-verification]

tech-stack:
  added: []
  patterns:
    - "注入式 httpx.AsyncClient 共享连接池（QMD + LLM 同一实例）"
    - "三 Semaphore 互不嵌套（patient / qmd / llm）"
    - "try/finally 强制写 rag_results.json（Ctrl-C 安全）"
    - "异常分级：LLMFailure → KeyError/ValueError → CancelledError(raise)"
    - "tmp + replace 原子写（POSIX rename）"

key-files:
  created:
    - scripts/pipeline.py
    - tests/test_pipeline_helpers.py
    - tests/test_pipeline.py
    - tests/fixtures/mock_qmd_hit.json
    - tests/fixtures/mock_patient.json
  modified: []

key-decisions:
  - "D-01: asyncio.gather + return_exceptions=True 替代 TaskGroup（Python 3.9.6）"
  - "D-03: 共享 httpx.AsyncClient 注入到 QMD + LLM"
  - "D-04: 三 Semaphore 互不嵌套，独立计数"
  - "D-05: 异常分级，禁止 except Exception 通配"
  - "D-07: finally 块强制 merge，Ctrl-C 安全"
  - "D-08: _scan_resume 两段扫描 + _failed unlink"
  - "D-14: build_patient_prompt 迁移自 generate_batch_prompt 单患者形态"
  - "D-15: compute_citation_coverage 扫 [n] / retrieval_sources 比率"

patterns-established:
  - "Mock 高层 API（AsyncQMDService/AsyncLLMClient）而非 httpx 桩路由，简化测试"
  - "_atomic_write_json 字节复制自 kb_metadata.py（保持模块边界）"

requirements-completed: [PIP-01, PIP-02, PIP-03, PIP-04, PIP-05, PIP-06]

duration: 12min
completed: 2026-05-12
---

# Phase 3 Plan 1: pipeline.py 核心模块 Summary

**并发患者级流水线编排器：asyncio.gather + 共享 httpx + 三 Semaphore 限流 + citation coverage 反馈 + resume 断点续传**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-12T09:34:14Z
- **Completed:** 2026-05-12T09:46:00Z
- **Tasks:** 2
- **Files modified:** 5 (created)

## Accomplishments
- `scripts/pipeline.py` 432 LOC：13 个导出函数（11 helper + run_pipeline + _run_one_patient）
- 23 个新测试全部通过（16 helper 单测 + 7 集成测试）
- Python 3.9.6 anti-pattern 零出现（TaskGroup / except* / PEP 604 / except Exception）
- 全套 275 tests passing, 0 failures（Phase 1+2 不回归）

## Task Commits

TDD 提交序列（RED → GREEN × 2 tasks）：

1. **Task 1 RED: helper 测试** - `9a41965` (test)
2. **Task 1 GREEN: helper 实现** - `4193dab` (feat)
3. **Task 2 RED: 集成测试** - `92cd5b1` (test)
4. **Task 2 GREEN: run_pipeline 实现** - `56f2187` (feat)

## Files Created/Modified
- `scripts/pipeline.py` (432 LOC) - 并发编排器：run_pipeline + _run_one_patient + 11 helper
- `tests/test_pipeline_helpers.py` (317 LOC) - 16 个同步 helper 单测
- `tests/test_pipeline.py` (537 LOC) - 7 个 async 集成测试（happy/partial/failure/resume/concurrency）
- `tests/fixtures/mock_qmd_hit.json` - QMD hit fixture
- `tests/fixtures/mock_patient.json` - 患者数据 fixture

## Decisions Made
- Mock 策略选择直接 patch AsyncQMDService/AsyncLLMClient 类而非 httpx URL 路由桩（更简洁，避免 QMD session_id 管理复杂性）
- `_atomic_write_json` 字节复制自 kb_metadata.py 而非 import（保持模块边界：KB 工具 vs 患者编排器）
- `compute_citation_coverage` 中 `guideline_results` 缺失返回 0.0 而非 1.0（与"无可引用视为不缺"区分：缺失 key = 输入错误，不算覆盖完整）
- QMD mock 提供足够响应（每患者 ~4 queries × 患者数）避免 side_effect 耗尽

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] 集成测试 mock 策略从 httpx URL 路由改为高层 API mock**
- **Found during:** Task 2 集成测试实现
- **Issue:** 原计划用 httpx URL 路由桩（/mcp → QMD, /chat/completions → LLM），但 AsyncQMDService.__aenter__ 需要 session_id 设置，mock _wait_for_ready_async 跳过后 session_id 为 None 导致 RuntimeError
- **Fix:** 改为直接 patch AsyncQMDService 和 AsyncLLMClient 类，mock query() 和 complete_structured_with_feedback() 返回值
- **Files modified:** tests/test_pipeline.py
- **Committed in:** 56f2187

**2. [Rule 3 - Blocking] QMD mock 响应数量不足**
- **Found during:** Task 2 集成测试调试
- **Issue:** _make_qmd_mock 默认只提供 1 组 QMD 响应，但每患者需 ~4 次 query 调用
- **Fix:** _make_qmd_mock 接受 num_patients 参数，默认生成 num_patients × 5 响应
- **Files modified:** tests/test_pipeline.py
- **Committed in:** 56f2187

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** 均为测试实现细节调整，不影响生产代码设计

## Issues Encountered
None

## User Setup Required
None - 无外部服务配置

## Next Phase Readiness
- `scripts/pipeline.py` 完整就绪，Plan 03-02 可直接 `from scripts.pipeline import run_pipeline`
- Plan 03-02 需在 `batch_pipeline.py:main` 中添加 `subparsers.add_parser("run", ...)` + `asyncio.run(run_pipeline(args))`
- Plan 03-03 E2E 验收需真实 vLLM + QMD + KB 环境

---
*Phase: 03-pipeline-run-subcommand-interface-extension*
*Completed: 2026-05-12*
