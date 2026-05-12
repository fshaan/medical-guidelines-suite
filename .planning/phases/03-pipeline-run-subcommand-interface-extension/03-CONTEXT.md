# Phase 3: Pipeline + run Subcommand + Interface Extension - Context

**Gathered:** 2026-05-12
**Status:** Ready for planning
**Mode:** `--auto --chain`（决策面由 refactor_plan §四.3 / §五 + Phase 1-2 已落地的异步范式锁死，本步骤固化 Phase 3 实现层选择并自动推进至 plan-phase）

<domain>
## Phase Boundary

新增 `scripts/pipeline.py:run_pipeline` 作为按患者并发的总编排器：顶层 `asyncio.gather` + 患者级 `Semaphore(5)`（Python 3.9.6 **不支持 TaskGroup**，PIP-01 中的"TaskGroup"措辞重释义为「在单一 event loop 下 gather 并发」），共享同一 `httpx.AsyncClient` 实例注入到 `AsyncQMDService` 与 `AsyncLLMClient`；通过 `batch_pipeline.py run` 子命令暴露；扩展 `validate / generate --patients-dir` 接口；旧 `split / orchestrate / verify-batch / merge` 子命令注册为 hidden（仍可调用，`--help` 不显示）。本 phase 是 v3.1 milestone 的 ship gate —— 10 例 fixture 端到端 `<10 min` 在此达成。

**不在本 Phase 范围内**（属 Phase 4 / 永久 deferred）：
- 删除 `cmd_orchestrate / _auto_split_batch / cmd_split / cmd_verify_batch` 源码及对应 4 个测试文件（Phase 4，stabilize 一周后）
- `--help` 文案彻底收敛到 5 个子命令（Phase 4 QG-07）
- 流式 LLM 输出（v3.2 STR-01）
- LLM failover 自动切换（v3.2，本 phase 单 profile 固定）
- 多病种患者拆分（v3.2 MD-01）
- Prometheus metrics / 结构化日志（v3.2 OBS-01..03）
- 患者数据持久化 DB（永久 Out of Scope —— Output/ 文件即结果）

</domain>

<decisions>
## Implementation Decisions

### Pipeline 顶层编排（PIP-01, PIP-02）

- **D-01:** Python 3.9.6 下 `TaskGroup` 不可用 —— 顶层用 `asyncio.gather(*[_run_one_patient(p) for p in patients], return_exceptions=True)` 包在 patient-level `asyncio.Semaphore(N)` 内
  - 推荐理由：CLAUDE.md / Phase 1 CONTEXT §上游约束已明确禁 `TaskGroup`；`return_exceptions=True` 让单患者失败不击垮 gather 整体
  - 拒绝：用 `taskgroup` backport 包 —— Phase 1/2 没引入此依赖，Phase 3 不引新依赖
  - ROADMAP / REQUIREMENTS 中 "TaskGroup" 字面释义为「顶层并发原语」，不绑定具体 API

- **D-02:** `_run_one_patient(patient, *, qmd, llm, output_dir, sem)` 串行 stages：
  1. `extract_features(patient)` —— 纯函数，从 dict 提取诊断/分期/年龄等
  2. `build_queries(features, synonym_map)` —— 走 Phase 1 `kb_metadata.normalize_disease` → 生成 N 条检索 query
  3. `hits = await asyncio.gather(*[qmd.query(q) for q in queries])` —— QMD sem 限流（Phase 1 D-03）
  4. `filtered = filter_chunks_by_disease(dedupe(hits), chunks_meta, canonical_key)`（Phase 1 D-10 函数）
  5. `messages = build_patient_prompt(patient, filtered)` —— system+user 双消息，迁移 `batch_pipeline.py:743-887:generate_batch_prompt` 单患者形态
  6. `result, score, status = await llm.complete_structured_with_feedback(messages, PATIENT_RECOMMENDATION_SCHEMA, feedback_check=compute_citation_coverage, threshold=0.5, patient_id=patient.id)`
  7. 写 shard：`Output/patients/<patient_id>.json`（成功 / partial）或 `Output/_failed/<patient_id>.json`（异常）
  - 任何 stage 抛 `LLMFailure / asyncio.CancelledError` 之外的异常 → 写 `_failed/`，patient sem 释放，不影响其它患者

- **D-03:** 顶层 `httpx.AsyncClient` 单实例注入到 QMD + LLM（连接池共享）
  ```python
  async with httpx.AsyncClient(timeout=httpx.Timeout(profile.timeout_s)) as http:
      qmd_sem = asyncio.Semaphore(args.concurrency_qmd)
      pat_sem = asyncio.Semaphore(args.concurrency_patients)
      async with AsyncQMDService(http=http, semaphore=qmd_sem, kb_root=kb_root) as qmd:
          llm = AsyncLLMClient(profile, http=http, semaphore=asyncio.Semaphore(profile.concurrency))
          # gather all patients
  ```
  - QMD `_owns_http=False`（外部注入，不在 `__aexit__` 关闭 http）—— Phase 1 D-03 注入式模式直搬
  - LLM `concurrency` 独立于 patient sem（避免 LLM 在飞数被 patient 数 cap 死）；典型 `LLM_CONCURRENCY=5, concurrency_patients=5, concurrency_qmd=8`
  - **`httpx.Timeout(profile.timeout_s)`** 必须传给 `AsyncClient`（Phase 2 fix CR-02 已确保 client 也带 timeout，但顶层 client 也需要同步配置；优先级 LLM_TIMEOUT > profile.timeout_s）

- **D-04:** 双 Semaphore 互不嵌套
  - patient-level sem 在 `_run_one_patient` 入口 acquire（保护「在飞患者数 ≤ N」）
  - QMD sem 在 `qmd._async_query` 内 acquire（保护「在飞 QMD HTTP 请求数 ≤ M」）
  - LLM 自己的 sem 在 `complete_structured` 内 acquire（已是 Phase 2 实现）
  - 三个 sem 独立计数，不共享实例

### 错误处理与退出码（PIP-03, PIP-05）

- **D-05:** 单患者异常分级
  | 异常类型 | 处理 | 落点 |
  |---------|------|------|
  | `LLMFailure(stage="transport")` | 不重试（Phase 2 已 retry 3 次） | `_failed/<id>.json` with `error/stage/last_llm_output=null` |
  | `LLMFailure(stage="schema")` | 不重试（Phase 2 已 retry 1 次） | `_failed/<id>.json` with `last_llm_output=<最后一次 LLM 原文>` |
  | `LLMFailure(stage="feedback")` | 不会触发（Phase 2 feedback 路径不抛错，接受 partial） | N/A |
  | `httpx.RequestError`（漏网） | 写 `_failed/`（Phase 2 fix 已包成 LLMFailure，理论不会出现） | `_failed/<id>.json` |
  | `KeyError / ValueError`（extract/build 阶段） | 写 `_failed/`，`error.stage="build"` | `_failed/<id>.json` |
  | `asyncio.CancelledError` | 不写 shard，向上传播（让 gather 优雅取消其它任务） | N/A |
  | `compute_citation_coverage < threshold` 二次仍不达标 | 接受为 partial，写 `patients/<id>.json` with `status="partial"` | `Output/patients/` |

- **D-06:** 退出码语义（PIP-05）
  - 全部 patients 写到 `Output/patients/`（含 partial）→ exit 0
  - 任一 patient 进 `Output/_failed/` → exit 1
  - 用户 Ctrl-C → exit 130（POSIX 默认，asyncio cleanup 后退出）

- **D-07:** `Output/rag_results.json` 是派生 aggregate（PIP-06）
  - run 末尾 read `Output/patients/*.json` 合并为 `{patients: [{id, status, result}, ...], failures: [{id, error, stage}, ...], summary: {total, ok, partial, failed, wall_time_s}}`
  - 与现产物 (`scripts/batch_pipeline.py:cmd_merge` 输出) **结构等价**（验收靠 `validate --input Output/rag_results.json` 不报错；schema 与现 aggregate 结构对齐）
  - run 中断（Ctrl-C）也尽力写一份 rag_results.json（捕获 `KeyboardInterrupt` 在 `finally` 中执行 merge）

### Resume 语义（PIP-04）

- **D-08:** `--resume` 模式两段扫描
  1. 扫 `Output/patients/<patient_id>.json` 存在 → 该 patient 已完成，跳过（不重新检索 / 不重新调 LLM）
  2. 扫 `Output/_failed/<patient_id>.json` 存在 → 该 patient 列入本次重试队列，**删除旧 _failed 文件**后重新跑
  - 既不在 `patients/` 也不在 `_failed/` 的 patient → 视为新 patient 跑全流程
  - resume 模式下 `rag_results.json` 在 run 末尾完整重生成（合并所有 `patients/*.json`，不区分本次 vs 上次）

- **D-09:** 写 shard 必须**原子**（避免中断半成品）
  - 模式：`with open(target.with_suffix(".tmp"), "w") as f: json.dump(..., f); f.flush(); os.fsync(f.fileno())` → `target.tmp.rename(target)`
  - macOS / Linux 上 `rename` 在同一文件系统是原子操作，符合 POSIX
  - 不写 `<patient_id>.json.lock`（asyncio 单进程内 sem 已保证不会并发写同一文件）

### CLI 设计（CLI-01..05, CFG-03）

- **D-10:** `run` 子命令 args（CLI-01）
  ```
  batch_pipeline.py run
    --patients <patients.json>           # Required, output of `parse`
    --output-dir <dir>                   # Required, e.g. Output/
    --llm-profile <name>                 # Default: env LLM_PROFILE or qwen3-vllm-lan
    --concurrency-patients <int>         # Default: env PIPELINE_CONCURRENCY_PATIENTS or 5
    --concurrency-qmd <int>              # Default: env PIPELINE_CONCURRENCY_QMD or 8
    --resume                             # Skip existing shards, retry _failed/
    --kb-root <path>                     # Default: env MEDICAL_GUIDELINES_DIR or ./guidelines/
  ```
  - 优先级（CFG-03）：CLI flag > env > 默认
  - `--llm-profile` 不接受多值（v3.2 才上自动 failover）

- **D-11:** `validate / generate` 接口扩展（CLI-02, CLI-03）
  - `validate --patients-dir <dir>` 扫该目录所有 `*.json` 逐个走 per-patient 校验逻辑（每个文件视为单患者结果）
  - `validate --input <file>` 保留兼容，触发 `DeprecationWarning: --input is deprecated, use --patients-dir for v3.1 pipeline`
  - 同 patterns 适用于 `generate`（输出按 patient_id 派生 `<patient_id>.md` 报告或 aggregate `report.md`，由 planner 在 PLAN 中定形态；优先 per-patient md）

- **D-12:** `index` 扩展为产出 `.metadata/*.json` 侧车（CLI-04）
  - 已在 Phase 1 D-05 实现，Phase 3 这个 req 实质上是「保证 index 调用 build_sidecar」—— 行为已就位，本 phase 只需测试覆盖

- **D-13:** 旧子命令 hidden（CLI-05）
  - argparse 实现：`subparsers.add_parser("orchestrate", help=argparse.SUPPRESS)`（`help=SUPPRESS` 让 `--help` 不显示，但 `batch_pipeline.py orchestrate ...` 仍可执行）
  - 影响子命令：`split / orchestrate / verify-batch / merge` 四个
  - 测试覆盖：assert 这四个仍能调用并返回相同退出码（保留 stabilize 期回退能力）

### Prompt 构造（PIP-02 中的 build_patient_prompt）

- **D-14:** 从 `batch_pipeline.py:743-887:generate_batch_prompt` 迁移单患者形态到 `scripts/pipeline.py:build_patient_prompt(patient, retrieval_hits) -> list[dict]`
  - **签名变化**：从「batch 5 patients in 1 prompt」改为「single patient + retrieval_sources injected」
  - **返回**：`[{"role": "system", "content": <medical persona + schema description>}, {"role": "user", "content": <patient profile + retrieval excerpts + question>}]`
  - **citation 编号约束**：user message 里把 retrieval hits 编号为 `[1] <source_file>: <excerpt>` 形式，要求 LLM 在 `recommendation` 文本中引用 `[n]` 与 `retrieval_sources[n-1]` 对应 —— `citation_coverage` 算的就是这个对应率
  - 老的 batch prompt 函数**保留不删**（Phase 4 才清理），但 Phase 3 不再调用

### citation_coverage 计算（Phase 2 D-07 注入点的真实落地）

- **D-15:** `compute_citation_coverage(result: dict) -> float` 是 Phase 3 新函数（不在 `llm_client.py`）
  - 算法：扫 `result["guideline_results"][*]["recommendation"]` 文本中的 `[n]` 编号 → 取 unique set → 除以 `retrieval_sources` 数量
  - 边界：`recommendation` 无 `[n]` → coverage = 0；`retrieval_sources` 为空 → coverage = 1（无可引用即视为不缺）
  - 阈值 0.5 = "至少引用一半的 retrieval_sources"（refactor_plan §四.3 已定）
  - 落点：`scripts/pipeline.py` 内私有函数（不暴露到其它模块）

### 进度与日志（Claude's Discretion 范围内）

- **D-16:** stdout 进度行单行格式（非 TTY 兼容）
  ```
  [3/10] 贾常山 (gastric → colorectal correction) ... PASS (12.3s, coverage=0.71)
  [4/10] 李学 ... PARTIAL (18.1s, coverage=0.42)
  [5/10] 肖庆周 ... FAIL (timeout, stage=transport)
  ```
  - 不引入 rich / tqdm（保持 0 新依赖）
  - 每个 patient 完成即 print + flush（不批量缓冲）
  - 末尾打印 summary：`Total: 10  OK: 7  Partial: 2  Failed: 1  Wall: 487s  Exit: 1`

### 测试策略（QG-06）

- **D-17:** 新增 `tests/test_pipeline.py` 覆盖
  1. `test_run_pipeline_happy_path` —— 3 patient × mock httpx，全部 PASS
  2. `test_run_pipeline_partial_accepted` —— 1 patient citation_coverage 二次 0.3 → 接受 partial
  3. `test_run_pipeline_one_failure_isolated` —— 1 patient transport 失败 → 写 `_failed/`，其它 2 PASS，exit 1
  4. `test_run_pipeline_resume_skips_existing_shards` —— 预先放 `patients/p001.json`，resume 跑只处理 p002/p003
  5. `test_run_pipeline_resume_retries_failed_shards` —— 预先放 `_failed/p001.json`，resume 删旧 _failed 重跑
  6. `test_atomic_write_no_partial_on_crash` —— mock `json.dump` 中断 → 验证目标文件不存在或完整
  7. `test_concurrency_caps_in_flight` —— mock 慢响应 + sem(2) → 验证 in-flight peak ≤ 2
  - 全部用 `AsyncMock(httpx.AsyncClient)`，**不**调真实 vLLM / QMD

- **D-18:** 10 例 fixture E2E 验收为**人工命令**（不进 pytest）
  - 验收脚本：`time batch_pipeline.py run --patients Output/patients.json --output-dir Output/ --concurrency-patients 5 --llm-profile qwen3-vllm-lan`
  - 检查清单：wall<10min / `validate --patients-dir Output/patients/` 0 FAIL / `jq` 跨越症校验 / `_failed/` 检查
  - 不强求 CI 跑（需真实 vLLM + QMD + KB），但 PLAN.md 必须列出验收命令

### Claude's Discretion

- 单元测试拆分：`test_pipeline.py` vs `test_pipeline_resume.py` 分文件由 planner 决定；推荐合并到单文件（resume 是 mode，不是独立组件）
- atomic write helper：`pipeline._atomic_write_json(path, data)` 独立函数 vs inline；推荐独立函数（PR-04 fixture 也用到）
- 进度行的颜色 / 缩进：默认无色，无缩进；若 TTY 检测 `sys.stdout.isatty()` 为 True 可选加 ANSI 色码（绿/黄/红 = PASS/PARTIAL/FAIL）
- `batch_pipeline.py` 主入口的 `__name__ == "__main__"` 块结构：planner 决定要不要把 `run` 路由提升为单独 `cmd_run` 函数（推荐 yes，保持子命令一致风格）
- pytest fixture 共用：复用 Phase 1 的 `mock_httpx_client` 还是新建独立 fixture —— planner 决定（推荐复用并扩展）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents (researcher / planner / executor) MUST read these before planning or implementing.**

### Source of Truth（必读）

- `docs/refactor_plan_2026-05-11.md`
  - §四.3 `scripts/pipeline.py` 设计 —— `run_pipeline` 顶层 gather、`_run_one_patient` stages、citation_coverage 阈值
  - §五 `batch_pipeline.py` CLI 改造 —— `run / validate / generate / index` 接口、旧子命令 hidden 策略
  - §九 端到端验证方案 —— Phase 3 验证命令清单（time run / validate / jq / 退出码）
  - §十.2 风险与权衡 —— 患者级并发上限、QMD vs LLM sem 关系、resume 边界

### Project-Level

- `.planning/PROJECT.md` —— v3.1 milestone 范围、key decisions（patient-level concurrency=5 是 ship gate）
- `.planning/REQUIREMENTS.md` —— Phase 3 范围：PIP-01..06（6 条）+ CLI-01..05（5 条）+ CFG-03（1 条）+ QG-01..06（6 条），共 **18 条**
- `.planning/ROADMAP.md` §Phase 3 —— Goal、Success Criteria（5 条）、Duration（3d）
- `.planning/STATE.md` —— performance baseline 表（旧 batch 50min 人工 → Phase 3 10例 <10min ship gate）

### Phase 1-2 落地的契约（必读，直接复用）

- `.planning/phases/01-async-retriever-kb-metadata-sidecar/01-CONTEXT.md`
  - §D-01..04 异步 retriever 接口与 sem 注入模式
  - §D-10 `kb_metadata.py` 纯函数 API（`normalize_disease` / `filter_chunks_by_disease` / `filter_orgs_by_disease`）
- `.planning/phases/02-llm-client-schema-unit-tests/02-CONTEXT.md`
  - §D-01..03 `AsyncLLMClient` 签名 + httpx 注入 + strict schema
  - §D-07 三条独立重试路径（transport×3 / schema×1 / feedback×1）
  - §D-10..12 `LLMProfile` + `from_env` + yaml profile
- `.planning/phases/02-llm-client-schema-unit-tests/02-REVIEW.md` —— **Phase 2 双审查 13 findings 已修 9 条，剩 4 INFO 不阻塞 Phase 3**

### 现有代码（Phase 3 改造 / 迁移）

- `scripts/batch_pipeline.py:743-887` —— `generate_batch_prompt`（迁移源，Phase 3 D-14 改造为 `build_patient_prompt`）
- `scripts/batch_pipeline.py:cmd_orchestrate / _auto_split_batch / cmd_split / cmd_verify_batch` —— Phase 3 hidden（CLI-05），Phase 4 删除
- `scripts/batch_pipeline.py:cmd_merge / cmd_validate / cmd_generate` —— `validate / generate` 在此扩展 `--patients-dir`
- `scripts/retriever.py:AsyncQMDService` —— Phase 1 交付，Phase 3 顶层注入
- `scripts/llm_client.py:AsyncLLMClient + LLMProfile + PATIENT_RECOMMENDATION_SCHEMA + compute_structured_with_feedback` —— Phase 2 交付，Phase 3 顶层注入
- `scripts/kb_metadata.py:normalize_disease + filter_chunks_by_disease + filter_orgs_by_disease + load_synonym_map` —— Phase 1 交付，Phase 3 D-02 stage 3-4 调用

### 上游约束

- `CLAUDE.md` —— Python 3.9.6 约束（**禁** `asyncio.TaskGroup` / `asyncio.timeout` / `except*` / PEP 604 `X | Y`），用 `gather + return_exceptions=True` 替代
- `tests/conftest.py` —— pytest-asyncio strict mode 已启用（Phase 1）
- `requirements.txt` —— Phase 3 无新依赖（httpx / pytest-asyncio / pyyaml / jsonschema 全部齐了）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **Phase 1 `AsyncQMDService` 注入模式** (`scripts/retriever.py:AsyncQMDService.__init__`) —— `http=None, semaphore=None` 双注入位 + `_owns_http` 标志；Phase 3 顶层传 `http=shared, semaphore=qmd_sem`，QMD 不持有 http 所有权
- **Phase 2 `AsyncLLMClient.complete_structured_with_feedback`** (`scripts/llm_client.py:317-345`) —— 已封装好「调用 + 算分 + feedback 重试一次」循环；Phase 3 `_run_one_patient` 直接 await 调用，传 `feedback_check=compute_citation_coverage`
- **`PATIENT_RECOMMENDATION_SCHEMA`** (`scripts/llm_client.py:57-101`) —— Phase 2 已包 `additionalProperties: false` 三层（Phase 2 fix IN-01）；Phase 3 直接 import 用
- **`compute_citation_coverage` 算法骨架**（来自 refactor_plan §四.3）—— Phase 3 实现，无现成代码
- **`tests/test_retriever_async.py` 的 mock httpx 模式** —— `AsyncMock(spec=httpx.AsyncClient)` + `mock_http.post.side_effect = [resp1, resp2, ...]`；Phase 3 test_pipeline.py 同款扩展为 QMD + LLM 双桩

### Established Patterns

- **subparsers add_help=SUPPRESS** —— argparse 隐藏子命令的标准做法（参考 Python 官方 docs），Phase 3 D-13 用此实现 CLI-05
- **atomic write via tmp + rename** —— Phase 1 `kb_metadata.build_sidecar` 已实践（`.metadata/chunks.json.tmp` → rename）；Phase 3 patient shard 同款
- **frozen dataclass for config** —— Phase 2 `LLMProfile`（Phase 3 不引入新 dataclass，但 `RunArgs` 可考虑 NamedTuple / dataclass 包 CLI 解析结果）
- **`asyncio.gather(*, return_exceptions=True)` 不击垮 sibling** —— Phase 1 `tests/test_retriever_async.py::test_gather_collects_exceptions` 已验证
- **从 env 读 int with fallback**（仓库当前用 `int(os.environ.get("FOO", "8"))`）—— Phase 3 D-10 复用同模式，CFG-03 env 优先级

### Integration Points

- **`scripts/pipeline.py` 新文件**（独立模块，预估 250-350 LOC）——
  - 入口：`async def run_pipeline(args: argparse.Namespace) -> int:`（返回退出码）
  - 内部：`_run_one_patient` / `compute_citation_coverage` / `build_patient_prompt` / `_atomic_write_json` / `_merge_rag_results`
  - 不在此文件：CLI parsing（仍在 `batch_pipeline.py:main`）
- **`scripts/batch_pipeline.py:main` 集成边界**：
  - 新增 `subparsers.add_parser("run", ...)` + 调用 `asyncio.run(pipeline.run_pipeline(args))`
  - 旧 4 个 subparser 加 `help=argparse.SUPPRESS`
  - `validate / generate` 子命令新增 `--patients-dir` 互斥组（与 `--input` mutually_exclusive_group）
- **`Output/` 目录结构（Phase 3 后）**：
  ```
  Output/
    patients.json             # parse 输出（不变）
    patients/                 # ← Phase 3 新增 per-patient shards
      p001.json
      p002.json
      ...
    _failed/                  # ← Phase 3 新增失败患者
      p005.json               # {patient_id, error, stage, last_llm_output}
    rag_results.json          # 派生 aggregate（与现产物结构等价）
    report.md                 # generate 输出（per-patient 或 aggregate，由 planner 决定）
  ```

</code_context>

<specifics>
## Specific Ideas

- **`_failed/<patient_id>.json` schema**（refactor_plan §四.3 提及）：
  ```json
  {
    "patient_id": "p005",
    "error": "Connection reset by peer",
    "stage": "transport",
    "last_llm_output": null,
    "attempted_at": "2026-05-12T17:30:42Z"
  }
  ```

- **`Output/patients/<patient_id>.json` schema**（包装 LLM 结果 + 元数据）：
  ```json
  {
    "patient_id": "p001",
    "status": "ok",
    "citation_coverage": 0.71,
    "wall_time_s": 12.3,
    "result": { "guideline_results": [...], "consensus": [...], "differences": [...] }
  }
  ```

- **`rag_results.json` 顶层 schema**（与 `cmd_merge` 现产物对齐 + 新增 summary）：
  ```json
  {
    "patients": [{ "id": "p001", "status": "ok", "result": {...} }, ...],
    "failures": [{ "id": "p005", "error": "...", "stage": "transport" }, ...],
    "summary": { "total": 10, "ok": 7, "partial": 2, "failed": 1, "wall_time_s": 487 }
  }
  ```

- **`build_patient_prompt` 的 system message 模板**（迁移自 `generate_batch_prompt` 但聚焦单患者）：
  ```
  你是一位资深肿瘤科医生 + 临床指南专家。基于以下 N 份指南检索结果，
  为该患者生成跨指南循证推荐对比。
  - 输出 strict JSON，schema 见 response_format
  - 每条 guideline_results 至少引用 2 个 retrieval_sources 编号 [n]
  - evidence_level 必须落在 schema enum 内
  - consensus / differences 各列出 2-4 条
  ```
  - planner 在 PLAN.md 中定稿完整模板（含 user message 拼装格式）

- **fixture：`Input/2026-4-23.xlsx` 10 例患者覆盖**（refactor_plan 已确认）：贾常山 / 李学 / 肖庆周 三例结直肠癌（QG-03 反例校验：返回的 guideline_results 不含"胃癌"字样）+ 7 例其它癌种
- **测试用 fixture：`tests/fixtures/mock_qmd_hit.json` + `tests/fixtures/mock_patient.json`** —— planner 在 PLAN.md 中指定具体内容

</specifics>

<deferred>
## Deferred Ideas

- **流式 LLM 输出** —— `STR-01`（v3.2），strict schema 一次完整输出，Phase 3 不支持
- **多 profile failover**（vLLM 挂切 DeepSeek） —— `STR / OBS` v3.2 范围，Phase 3 单 profile 固定
- **token 用量 / 成本 metrics** —— `OBS-01..03`（v3.2）
- **结构化日志（loguru / structlog）** —— `OBS-01`（v3.2）；Phase 3 用 plain print 到 stdout
- **多病种患者拆分**（贾常山有胃癌+肝转移要拆开查） —— `MD-01`（v3.2），Phase 3 按主诊断 `disease_type` 单病种处理
- **Web GUI / SSE 进度推送** —— `STR-02`（v3.2）/ 永久 Out of Scope（看 v3.2 用户反馈）
- **断点续传到任意 stage**（当前 resume 只在 patient 粒度） —— v3.2 优化，Phase 3 patient-level resume 已满足 100 例量级
- **Phase 4 清理**（删 cmd_orchestrate 等 4 子命令 + 4 测试文件 + SKILL/README/CHANGELOG 收敛文案） —— Phase 4，stabilize 一周后

### Reviewed Todos (not folded)

None —— `gsd-sdk query todo.match-phase 3` 返回 0 matches。

</deferred>

---

*Phase: 03-pipeline-run-subcommand-interface-extension*
*Context gathered: 2026-05-12*
*Auto-mode: `--auto --chain`（recommended options per refactor_plan §四.3 + §五 + Phase 1-2 已落地契约，全部 17 项决策已锁定）*
