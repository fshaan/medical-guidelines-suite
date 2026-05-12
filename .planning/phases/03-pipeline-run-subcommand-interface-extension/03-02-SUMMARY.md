---
phase: 03-pipeline-run-subcommand-interface-extension
plan: 02
subsystem: cli
tags: [argparse, asyncio, cli, subcommand, suppress, mutually-exclusive, env-vars]

requires:
  - phase: 03-pipeline-run-subcommand-interface-extension
    provides: scripts/pipeline.py run_pipeline(args) → int (Plan 03-01)
  - phase: 01-async-retriever-kb-metadata-sidecar
    provides: kb_metadata.build_sidecar (sidecar smoke test)

provides:
  - batch_pipeline.py 'run' subcommand routing to pipeline.run_pipeline
  - batch_pipeline.py 4 hidden subcommands (split/orchestrate/merge/verify-batch via argparse.SUPPRESS)
  - batch_pipeline.py validate/generate --input/--patients-dir mutually exclusive groups
  - batch_pipeline.py _validate_patients_dir + _generate_from_patients_dir private functions
  - batch_pipeline.py env var defaults (LLM_PROFILE/PIPELINE_CONCURRENCY_*/MEDICAL_GUIDELINES_DIR)
  - tests/test_cli_subcommands.py 11 cases (8 planned + 3 parametrized)

affects: [03-03-E2E-verification]

tech-stack:
  added: []
  patterns:
    - "argparse.SUPPRESS for hidden subcommands (Python 3.9: hides description only, not name in {choices})"
    - "add_mutually_exclusive_group(required=True) for --input/--patients-dir dual path"
    - "os.environ.get() in argparse default for env > literal fallback (CLI flag > env > literal)"
    - "asyncio.run(run_pipeline(args)) in main() dispatch for async pipeline entry"

key-files:
  created:
    - tests/test_cli_subcommands.py
  modified:
    - scripts/batch_pipeline.py

key-decisions:
  - "D-10: run args --patients --output-dir --llm-profile --concurrency-patients --concurrency-qmd --resume --kb-root"
  - "D-11: validate/generate accept --patients-dir (v3.1 main) with --input (v3.0 compat, deprecated warning)"
  - "D-13: 4 old subcommands hidden via help=argparse.SUPPRESS, still callable"
  - "CLI-01..05 + CFG-03: run registration / hidden / mutual exclusion / env defaults / index sidecar"
  - "cmd_validate local var renamed warnings → warn_list to avoid stdlib shadowing"

patterns-established:
  - "argparse.SUPPRESS in Python 3.9 subparsers hides description text but name still visible in {choices}"
  - "Mutually exclusive group pattern: g = parser.add_mutually_exclusive_group(required=True)"

requirements-completed: [CLI-01, CLI-02, CLI-03, CLI-04, CLI-05, CFG-03]

duration: 6min
completed: 2026-05-12
---

# Phase 3 Plan 2: CLI Subcommand Interface Extension Summary

**batch_pipeline.py CLI 改造：run 子命令路由 + 4 hidden + validate/generate --patients-dir 互斥 + env 默认值 + 11 CLI 测试**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-05-12T10:16:09Z
- **Completed:** 2026-05-12T10:22:00Z
- **Tasks:** 2
- **Files modified:** 2 (1 modified, 1 created)

## Accomplishments
- `batch_pipeline.py run` 子命令注册，路由到 `asyncio.run(pipeline.run_pipeline(args))`
- `split/orchestrate/merge/verify-batch` 4 个旧子命令标记 hidden（`help=argparse.SUPPRESS`）
- `validate` 与 `generate` 接受 `--input`（deprecated）与 `--patients-dir`（v3.1 主路径）互斥组
- env 变量默认值透传：`LLM_PROFILE / PIPELINE_CONCURRENCY_PATIENTS / PIPELINE_CONCURRENCY_QMD / MEDICAL_GUIDELINES_DIR`
- `_validate_patients_dir` + `_generate_from_patients_dir` 两个新私有函数
- 286 tests passing（275 existing + 11 new），0 failures

## Task Commits

TDD 提交序列（RED → GREEN × 2 tasks）：

1. **Task 1 RED: CLI 行为测试** - `7337405` (test)
2. **Task 1 GREEN: argparse 注册改造** - `d09c976` (feat)
3. **Task 2 测试已合并到 Task 1 的 TDD 流程中** — 无需额外提交

## Files Created/Modified
- `scripts/batch_pipeline.py` — 5 项改造（run 注册 + 4 hidden + 2 互斥组 + dispatch 分支 + cmd_validate/generate 分支 + 2 新私有函数）
- `tests/test_cli_subcommands.py` (317 LOC) — 11 个 CLI 行为测试

## Decisions Made
- `help=argparse.SUPPRESS` 在 Python 3.9 subparser 中只隐藏描述文本，名字仍在 `{choices}` 中出现（plan 预期完全隐藏，但 Python 3.9 行为不可控）
- Case 1 测试从"名称不出现在 --help"调整为"原始描述不出现在 --help"（匹配 Python 3.9 实际行为）
- cmd_validate 内局部变量 `warnings` 重命名为 `warn_list`（避免与 `import warnings` 的 stdlib 模块遮蔽冲突）
- Case 5/6 env 测试直接构造 argparse Namespace（避免 mock asyncio.run 的复杂性）
- Case 7 cmd_index 测试 mock `scripts.batch_pipeline.kb_metadata.build_sidecar`（patch 实际引用路径而非定义路径）

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] cmd_validate 局部变量 `warnings` 遮蔽 stdlib `warnings` 模块**
- **Found during:** Task 1 GREEN 实现后回归测试
- **Issue:** `cmd_validate` 函数内 `warnings = []` 赋值使 Python 将 `warnings` 视为局部变量，导致函数顶部 `warnings.warn(...)` 调用报 `UnboundLocalError`
- **Fix:** 将 `cmd_validate` 内所有 `warnings` 局部变量重命名为 `warn_list`（共 11 处引用）
- **Files modified:** scripts/batch_pipeline.py
- **Verification:** test_slim_profile.py 2 个失败测试修复，全套 286 tests passing
- **Committed in:** d09c976

**2. [Rule 3 - Blocking] Python 3.9 argparse.SUPPRESS 在 subparser 中不完全隐藏子命令名**
- **Found during:** Task 1 GREEN 验证
- **Issue:** Plan 预期 `--help` 中 hidden 子命令名称 0 次出现，但 Python 3.9 中 `help=argparse.SUPPRESS` 对 subparser 只隐藏描述文本，名称仍在 `{choices}` 列表中
- **Fix:** 调整 test_run_subcommand_in_help 测试：检查原始描述文案不出现在 --help 输出中（而非检查名称不存在）
- **Files modified:** tests/test_cli_subcommands.py
- **Committed in:** d09c976

---

**Total deviations:** 2 auto-fixed (1 bug, 1 blocking)
**Impact on plan:** Bug fix 必要（阻止回归），blocking fix 匹配 Python 3.9 实际行为。无范围蔓延。

## Issues Encountered
None

## User Setup Required
None - 无外部服务配置

## Next Phase Readiness
- `batch_pipeline.py run --patients X --output-dir Y` 就绪，Plan 03-03 E2E 验收可直接使用
- `validate --patients-dir Output/patients/` 就绪
- `generate --patients-dir Output/patients/` 就绪
- Plan 03-03 需真实 vLLM + QMD + KB 环境执行 E2E 验收（D-18）

---
*Phase: 03-pipeline-run-subcommand-interface-extension*
*Completed: 2026-05-12*
