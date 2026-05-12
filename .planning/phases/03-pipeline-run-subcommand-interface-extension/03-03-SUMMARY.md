---
phase: 03-pipeline-run-subcommand-interface-extension
plan: 03
subsystem: testing
tags: [e2e, smoke-test, acceptance, mock-httpx, rag-results, exit-code]

requires:
  - phase: 03-pipeline-run-subcommand-interface-extension
    provides: scripts/pipeline.py run_pipeline(args) → int (Plan 03-01)
  - phase: 03-pipeline-run-subcommand-interface-extension
    provides: batch_pipeline.py run 子命令 + main() dispatch (Plan 03-02)

provides:
  - tests/test_pipeline_e2e_smoke.py 3 cases（CI 可运行，mock httpx 双桩）
  - docs/phase3_e2e_acceptance.md 真实 vLLM 验收命令清单（D-18）
  - checkpoint:human-verify（用户跑完真实 10 例后填写 Sign-off 表）

affects: [Phase-4-stabilize]

tech-stack:
  added: []
  patterns:
    - "E2E smoke test 模式：mock AsyncQMDService + AsyncLLMClient（高层 API mock）+ tmp_path 隔离"
    - "main() dispatch 测试：patch asyncio.run + sys.exit 捕获 + 参数透传验证"

key-files:
  created:
    - tests/test_pipeline_e2e_smoke.py
    - docs/phase3_e2e_acceptance.md
  modified: []

key-decisions:
  - "D-18: E2E 真实验收为人工命令（不进 pytest），验收清单固化到 docs/phase3_e2e_acceptance.md"
  - "E2E smoke 测试复用 Plan 03-01 的高层 API mock 策略（patch AsyncQMDService/AsyncLLMClient 类）"
  - "Case 2 直接 patch asyncio.run 而非 subprocess 调 main()（简化验证，避免进程管理）"

patterns-established:
  - "验收文档 7 章节结构：前置条件 + run + validate + 反例 + enum + generate + pytest + hidden"
  - "Sign-off 表模板：7 行检查项 + 实测值列 + 责任人/日期"

requirements-completed: [QG-01, QG-02, QG-03, QG-04, QG-05, QG-06]

duration: 4min
completed: 2026-05-12
---

# Phase 3 Plan 3: E2E Smoke Tests + 验收清单 Summary

**CI 可运行 E2E smoke（3 cases: rag_results schema + main dispatch + 异常退出码）+ 真实 vLLM 10 例验收命令清单 + Sign-off 表**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-05-12T10:35:06Z
- **Completed:** 2026-05-12T10:38:49Z
- **Tasks:** 2（Task 3 checkpoint 不阻塞，自动跳过）
- **Files modified:** 2（全部新建）

## Accomplishments
- `tests/test_pipeline_e2e_smoke.py` 3 cases 全绿：完整链路（rag_results.json schema + evidence_level enum 校验）+ CLI dispatch（参数透传）+ 异常传播（exit 1 + _failed/ schema）
- `docs/phase3_e2e_acceptance.md` 7 章节 + Sign-off 表 + 失败诊断指引
- 289 tests passing（286 existing + 3 new），0 failures
- Python 3.9.6 anti-patterns 零出现

## Task Commits

1. **Task 1: E2E smoke tests** - `e9cc674` (test)
2. **Task 2: 验收文档** - `1fa5552` (docs)
3. **Task 3: checkpoint:human-verify** — 创建但不阻塞（用户跑完真实 10 例后填写 Sign-off）

## Files Created/Modified
- `tests/test_pipeline_e2e_smoke.py` (387 LOC) - 3 个 E2E smoke 测试用例
- `docs/phase3_e2e_acceptance.md` (140 LOC) - 真实环境验收命令清单 + Sign-off 表

## Decisions Made
- E2E smoke 测试复用 Plan 03-01 的 mock 策略（patch 类而非 httpx URL 路由）—— 保持一致性
- Case 2 使用 `patch("asyncio.run")` 拦截而非 subprocess 调 `batch_pipeline.py` —— 简化测试，避免进程管理复杂性
- 验收文档 Sign-off 表含 7 行检查项 + 实测值列，覆盖 QG-01..QG-06 + CLI-05

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
**真实环境验收需手动执行。** 参见 `docs/phase3_e2e_acceptance.md`：
- 内网 vLLM 可达
- QMD 索引 + $MEDICAL_GUIDELINES_DIR 完整
- Input/2026-4-23.xlsx 就位

## Next Phase Readiness
- Phase 3 全部 3 个 Plan 已完成（pipeline.py + CLI 改造 + E2E 验收层）
- Phase 3 ship gate 等待用户执行真实环境 10 例验收（docs/phase3_e2e_acceptance.md）
- 验收通过后触发 Phase 4 stabilize 一周倒计时（refactor_plan §七）

---
*Phase: 03-pipeline-run-subcommand-interface-extension*
*Completed: 2026-05-12*
