# Phase 03: Pipeline + run Subcommand + Interface Extension - Pattern Map

**Mapped:** 2026-05-12
**Files analyzed:** 4 (2 NEW: pipeline.py / test_pipeline.py; 1 MODIFIED: batch_pipeline.py; 2 fixtures NEW)
**Analogs found:** 4 / 4（全部命中现有实现）

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `scripts/pipeline.py` (NEW) | orchestrator (async) | event-driven + request-response (顶层 gather + 患者粒度 stages) | `scripts/retriever.py:AsyncQMDService` (注入式 async lifecycle) + `scripts/batch_pipeline.py:743-887:generate_batch_prompt` (prompt 迁移源) | role-match + 部分 exact |
| `tests/test_pipeline.py` (NEW) | test (asyncio + mock httpx) | request-response (mock 桩) | `tests/test_retriever_async.py` (8 cases) + `tests/test_llm_client.py` (19 cases) | exact |
| `tests/fixtures/mock_qmd_hit.json` (NEW) | test-fixture (json) | data-load | `tests/fixtures/mock_llm_response.json` (Phase 2 落地) | exact |
| `tests/fixtures/mock_patient.json` (NEW) | test-fixture (json) | data-load | `tests/fixtures/patient_001.json` (Phase 2 reserved) | exact |
| `scripts/batch_pipeline.py` (MODIFIED) | CLI dispatcher | request-response | 同文件 `main()` (lines 2277-2378) 自身既有 7 个子命令注册风格 | exact (self-reference) |

---

## Pattern Assignments

### `scripts/pipeline.py` (orchestrator, async)

**Analogs:**
- `scripts/retriever.py:AsyncQMDService` (异步注入式生命周期 + Semaphore + httpx 注入 / 不持有 http 所有权)
- `scripts/llm_client.py:AsyncLLMClient` (frozen dataclass profile + httpx 注入 + 三路重试)
- `scripts/batch_pipeline.py:743-887:generate_batch_prompt` (prompt 迁移源 → single-patient form)
- `scripts/kb_metadata.py:_atomic_write_json` (lines 236-247) (POSIX rename 原子写)
- `scripts/kb_metadata.py:normalize_disease + filter_chunks_by_disease + load_synonym_map` (lines 103-211) (Phase 1 双层过滤)

---

#### 1) Imports / 顶层布局模式

**Source:** `scripts/llm_client.py:1-21` + `scripts/retriever.py:1-23`

```python
"""Patient-level concurrent pipeline orchestrator.

Phase 3 (PIP-01..06): 顶层 asyncio.gather + 患者级 Semaphore，共享 httpx.AsyncClient
注入到 AsyncQMDService 与 AsyncLLMClient。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from scripts.kb_metadata import (
    filter_chunks_by_disease,
    filter_orgs_by_disease,
    load_synonym_map,
    normalize_disease,
)
from scripts.llm_client import (
    AsyncLLMClient,
    LLMFailure,
    LLMProfile,
    PATIENT_RECOMMENDATION_SCHEMA,
)
from scripts.retriever import AsyncQMDService
```

注意 `from __future__ import annotations` 必带（Phase 2 落地共识）；类型注解大量出现 `Optional[X]` 而非 PEP 604 `X | None`，详见下方 anti-patterns。

---

#### 2) 顶层 async 编排（D-01, D-03, D-04）—— 取自 retriever.py 注入模式扩展

**Source pattern:** `scripts/retriever.py:269-289` (AsyncQMDService.__aenter__ 注入式生命周期)

**Target Phase 3 pattern:**

```python
async def run_pipeline(args: argparse.Namespace) -> int:
    profile = LLMProfile.from_env(name=args.llm_profile)
    patients = _load_patients(args.patients)
    output_dir = Path(args.output_dir).resolve()
    (output_dir / "patients").mkdir(parents=True, exist_ok=True)
    (output_dir / "_failed").mkdir(parents=True, exist_ok=True)

    # resume scan
    to_run = _scan_resume(patients, output_dir, resume=args.resume)

    wall_start = time.monotonic()
    # 顶层共享 httpx.AsyncClient —— QMD + LLM 共连接池
    async with httpx.AsyncClient(timeout=httpx.Timeout(profile.timeout_s)) as http:
        qmd_sem = asyncio.Semaphore(args.concurrency_qmd)
        pat_sem = asyncio.Semaphore(args.concurrency_patients)
        async with AsyncQMDService(
            http_client=http,
            semaphore=qmd_sem,
        ) as qmd:
            llm = AsyncLLMClient(
                profile,
                http=http,
                semaphore=asyncio.Semaphore(profile.concurrency),
            )
            synonym_map = load_synonym_map(Path(args.kb_root))
            chunks_meta, coverage = _load_kb_metadata(args.kb_root)

            results = await asyncio.gather(
                *[
                    _run_one_patient(
                        p, qmd=qmd, llm=llm, output_dir=output_dir,
                        pat_sem=pat_sem, synonym_map=synonym_map,
                        chunks_meta=chunks_meta, coverage=coverage,
                    )
                    for p in to_run
                ],
                return_exceptions=True,   # D-01: 单 patient 失败不击垮 gather
            )

    wall = time.monotonic() - wall_start
    aggregate = _merge_rag_results(output_dir, wall)
    _atomic_write_json(output_dir / "rag_results.json", aggregate)
    return 0 if aggregate["summary"]["failed"] == 0 else 1
```

**关键复用点：**
- `async with httpx.AsyncClient(...)`：直接 mirror `retriever.py:277` 的注入式模式
- `AsyncQMDService(http_client=http, semaphore=qmd_sem)`：Phase 1 D-03 已暴露的两个 kwargs（参见 `retriever.py:238-251`）；`_owns_http=False` 自动生效
- `AsyncLLMClient(profile, http=http, semaphore=...)`：Phase 2 已暴露签名（参见 `llm_client.py:217-226`）
- `gather(..., return_exceptions=True)`：Phase 1 测试已验证（参见 `tests/test_retriever_async.py:165-200` 并发用法）

---

#### 3) `_run_one_patient` 串行 stages（D-02）

**Source pattern:** `scripts/llm_client.py:269-304` (`_post_with_retry` 内 `async with self._sem` 包裹 stages) + `scripts/batch_pipeline.py:558-598` (`extract_patient_features`) + `scripts/batch_pipeline.py:436-475` (`build_queries`)

**Target Phase 3 pattern:**

```python
async def _run_one_patient(
    patient: dict,
    *,
    qmd: AsyncQMDService,
    llm: AsyncLLMClient,
    output_dir: Path,
    pat_sem: asyncio.Semaphore,
    synonym_map: dict,
    chunks_meta: dict,
    coverage: dict,
) -> None:
    async with pat_sem:                      # patient-level 限流（D-04，独立于 qmd/llm sem）
        pid = patient.get("patient_id", "unknown")
        pname = patient.get("patient_name", "?")
        t_start = time.monotonic()
        try:
            # Stage 1: 特征提取（纯函数，从 batch_pipeline 复用）
            from scripts.batch_pipeline import extract_patient_features, build_queries
            features = extract_patient_features(patient)
            queries = build_queries(patient, features)

            # Stage 2: 病种归一化（Phase 1 函数）
            canonical = normalize_disease(patient.get("disease_type"), synonym_map)

            # Stage 3: QMD 并发查询（Phase 1 D-03 sem 自动生效）
            hits_per_query = await asyncio.gather(*[qmd.query(q) for q in queries])
            hits = _dedupe_hits([h for sub in hits_per_query for h in sub])

            # Stage 4: 双层过滤（Phase 1 D-10）
            allowed_orgs = filter_orgs_by_disease(coverage, canonical)
            hits = [h for h in hits if _hit_org(h) in allowed_orgs]
            hits = filter_chunks_by_disease(hits, chunks_meta, canonical)

            # Stage 5: 构造 prompt（迁移自 generate_batch_prompt 单患者形态）
            messages = build_patient_prompt(patient, hits)

            # Stage 6: LLM 调用（含 feedback 重试一次）
            result, score, status = await llm.complete_structured_with_feedback(
                messages,
                PATIENT_RECOMMENDATION_SCHEMA,
                feedback_check=compute_citation_coverage,
                threshold=0.5,
                patient_id=pid,
            )

            wall = time.monotonic() - t_start
            shard = {
                "patient_id": pid,
                "status": status,                    # "ok" or "partial"
                "citation_coverage": score,
                "wall_time_s": round(wall, 2),
                "result": result,
            }
            _atomic_write_json(output_dir / "patients" / f"{pid}.json", shard)
            print(f"[{pid}] {pname} ... {status.upper()} ({wall:.1f}s, coverage={score:.2f})", flush=True)

        except LLMFailure as e:
            _write_failed(output_dir, pid, str(e.last_error), e.stage, last_llm_output=None)
            print(f"[{pid}] {pname} ... FAIL ({e.stage}: {e.last_error})", flush=True)
        except (KeyError, ValueError) as e:
            _write_failed(output_dir, pid, str(e), stage="build", last_llm_output=None)
            print(f"[{pid}] {pname} ... FAIL (build: {e})", flush=True)
        except asyncio.CancelledError:
            raise   # D-05：CancelledError 必须向上传播，不写 shard
```

**关键复用点：**
- `async with pat_sem`：直接 mirror `llm_client.py:314`（`async with self._sem`）
- `LLMFailure(stage="transport"|"schema")`：Phase 2 已实现（`llm_client.py:35-44`），调用方仅需 catch
- `complete_structured_with_feedback(...) -> tuple[dict, float, str]`：Phase 2 已实现并返回 `(result, score, status)`，第三位即 D-05 表中的 "ok"/"partial"（`llm_client.py:327-355`）

---

#### 4) `_atomic_write_json` —— 复用 Phase 1 实现

**Source:** `scripts/kb_metadata.py:236-247`

```python
def _atomic_write_json(path: Path, data: dict) -> None:
    """tmp + rename 原子写：同目录写 .tmp 文件再 Path.replace（POSIX rename 原子）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
```

**Phase 3 决策**：D-09 选择「字节级复制此实现到 `pipeline.py`」，**不**从 `kb_metadata` import（保持模块边界清晰：kb_metadata 是 KB 工具，pipeline 是患者编排器；都需要原子写但语义独立）。Planner 可酌情合并到一个 utility，但推荐复制 5 行胜过新增 utility 模块。

D-09 增强建议：在 `tmp.write_text` 后增加 `f.flush() + os.fsync(f.fileno())` —— 但当前 kb_metadata 版本未做，Phase 3 一致即可（POSIX rename 原子性已足够）。

---

#### 5) `build_patient_prompt` 迁移（D-14）

**Source:** `scripts/batch_pipeline.py:743-887:generate_batch_prompt`

**迁移差异**：
- batch_prompt 是 `(batch, kb_profile, kb_root, batch_idx, total_batches, output_file) → str`，返回**单段 markdown 文本**包含 5 个患者
- patient_prompt 是 `(patient: dict, retrieval_hits: list[dict]) → list[dict]`，返回 **OpenAI chat messages 双消息**

**保留逻辑**：
- `<MANDATORY_RULES>` 9 条（lines 761-771）—— 几乎全部保留，去掉第 2 条 "relevant_orgs" / 第 8 条 guideline_version 格式（已由 strict schema 强制）
- 患者临床信息逐字段铺排（lines 786-805）—— 单患者保留
- 检索结果按 org 分组 + `[chunk_id]` 编号（lines 807-841）—— 编号改为 `[1] [2] [3]` 全局序号（D-14 + D-15）
- 结尾 JSON template（lines 850-885）—— **删除**（strict schema 已生效）

**新增**：system message 单独抽出为 medical persona + schema 描述；user message 含 patient profile + retrieval excerpts。

**Target shape:**

```python
def build_patient_prompt(patient: dict, retrieval_hits: list[dict]) -> list[dict]:
    system = (
        "你是一位资深肿瘤科医生 + 临床指南专家。基于以下指南检索结果，"
        "为该患者生成跨指南循证推荐对比。\n"
        "- 输出 strict JSON，schema 由 response_format 强制\n"
        "- 每条 guideline_results 至少引用 2 个 retrieval_sources 编号 [n]\n"
        "- evidence_level 必须落在 schema enum 内\n"
        "- consensus / differences 各列出 2-4 条\n"
        "- 全部 user-facing 文本使用简体中文"
    )
    user_parts: list[str] = []
    user_parts.append(f"## 患者 {patient.get('patient_name', '?')} ({patient.get('patient_id', '?')})\n")
    for k, v in patient.items():
        if k in ("features", "retrieval_results") or v is None:
            continue
        user_parts.append(f"- {k}: {v}")
    user_parts.append("\n## 检索结果\n")
    for i, hit in enumerate(retrieval_hits, 1):
        path = hit.get("path", "")
        score = hit.get("score", 0.0)
        content = hit.get("content", "")
        user_parts.append(f"[{i}] {path} (score={score:.2f})\n{content}\n")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
```

---

#### 6) `compute_citation_coverage`（D-15）—— 新算法，无现成代码

**Source:** refactor_plan §四.3 描述 + `scripts/batch_pipeline.py:1774-1780` (老版 coverage 检查参考)

```python
_CITATION_RE = re.compile(r"\[(\d+)\]")

def compute_citation_coverage(result: dict) -> float:
    """扫 guideline_results[*].recommendation 中的 [n] → unique set / retrieval_sources 总数。

    边界（D-15）：
    - recommendation 无 [n] → coverage = 0
    - retrieval_sources 为空 → coverage = 1（无可引用即视为不缺）
    """
    cited: set[int] = set()
    total_sources = 0
    for gr in result.get("guideline_results", []) or []:
        rec = gr.get("recommendation", "") or ""
        for m in _CITATION_RE.finditer(rec):
            cited.add(int(m.group(1)))
        total_sources += len(gr.get("retrieval_sources", []) or [])
    if total_sources == 0:
        return 1.0
    return len(cited) / total_sources
```

---

#### 7) `_merge_rag_results`（D-07, PIP-06）—— 简化版 cmd_merge

**Source:** `scripts/batch_pipeline.py:1376-1463:cmd_merge`（参考输出 schema 与 patient_lookup 模式）

```python
def _merge_rag_results(output_dir: Path, wall_time_s: float) -> dict:
    """合并 patients/*.json + _failed/*.json → rag_results.json schema."""
    patients_dir = output_dir / "patients"
    failed_dir = output_dir / "_failed"
    patients = []
    failures = []
    ok = partial = failed = 0

    for p in sorted(patients_dir.glob("*.json")):
        shard = json.loads(p.read_text(encoding="utf-8"))
        patients.append({
            "id": shard["patient_id"],
            "status": shard["status"],
            "citation_coverage": shard.get("citation_coverage"),
            "result": shard.get("result"),
        })
        if shard["status"] == "ok":
            ok += 1
        elif shard["status"] == "partial":
            partial += 1

    for p in sorted(failed_dir.glob("*.json")):
        shard = json.loads(p.read_text(encoding="utf-8"))
        failures.append({
            "id": shard["patient_id"],
            "error": shard.get("error"),
            "stage": shard.get("stage"),
        })
        failed += 1

    return {
        "patients": patients,
        "failures": failures,
        "summary": {
            "total": ok + partial + failed,
            "ok": ok,
            "partial": partial,
            "failed": failed,
            "wall_time_s": round(wall_time_s, 2),
        },
    }
```

**关键差异 vs cmd_merge：**
- cmd_merge 用 `rag_batch_*.json` glob + `_extract_patient_list` 扁平→嵌套（lines 1396-1423）
- _merge_rag_results 用 `patients/*.json` 直接读 shard（per-patient shard 是 canonical）
- summary 字段是 v3.1 新增（cmd_merge 只有 `patient_count / source_batches`）

---

### `tests/test_pipeline.py` (test, asyncio + mock httpx)

**Analogs:**
- `tests/test_retriever_async.py` (10 cases —— mock httpx pattern + AsyncMock fixtures)
- `tests/test_llm_client.py` (19 cases —— retry patterns + 信号量限流 + LLMFailure 断言)

---

#### 测试文件顶部 fixtures（复用 Phase 1+2 模式）

**Source:** `tests/test_llm_client.py:27-51`

```python
"""Tests for run_pipeline: happy path / partial / failure isolation / resume / atomic / sem."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scripts.pipeline import (
    run_pipeline,
    _run_one_patient,
    compute_citation_coverage,
    build_patient_prompt,
    _atomic_write_json,
    _merge_rag_results,
)


@pytest.fixture
def mock_qmd_hit():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "mock_qmd_hit.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def mock_patient():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "mock_patient.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def mock_http():
    """Shared mock httpx.AsyncClient — handles BOTH qmd POST and llm POST.

    Side-effect routing by URL: /mcp → qmd response, /chat/completions → llm response.
    """
    return AsyncMock(spec=httpx.AsyncClient)
```

---

#### Mock httpx 桩 —— 双桩路由模式

**Source:** `tests/test_retriever_async.py:33-44` + `tests/test_llm_client.py:41-51`

QMD 和 LLM **共享同一个 httpx.AsyncClient mock**（D-03 顶层共享），测试桩需按 URL 路由响应：

```python
def _mk_qmd_resp(*, status=200, session_id="s1", body=None):
    """QMD response shape — mcp-session-id header + structuredContent."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"mcp-session-id": session_id} if session_id else {}
    resp.json = MagicMock(return_value=body or {"result": {"structuredContent": {"results": []}}})
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


def _mk_llm_resp(*, status=200, content=None):
    """LLM response shape — choices[0].message.content with JSON."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.request = MagicMock()
    valid = {"guideline_results": [...], "consensus": [...], "differences": [...]}
    resp.json = MagicMock(return_value={
        "choices": [{"message": {"content": content or json.dumps(valid)}}]
    })
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


async def _routed_post(*args, **kwargs):
    url = args[0] if args else kwargs.get("url", "")
    body = kwargs.get("json", {})
    if "/mcp" in url:
        if body.get("method") == "initialize":
            return _mk_qmd_resp()
        return _mk_qmd_resp(body={...})  # structured QMD hits
    if "/chat/completions" in url:
        return _mk_llm_resp()
    raise AssertionError(f"unexpected url: {url}")
```

---

#### Test cases 1-7（D-17）—— 与 retriever/llm_client 测试用法 1:1 对齐

| # | Test name | Pattern source | Key assertion |
|---|-----------|----------------|---------------|
| 1 | `test_run_pipeline_happy_path` | `test_complete_structured_happy_path` (test_llm_client.py:54-65) | `output_dir/patients/p{1,2,3}.json` 三个 shard 写入 + exit 0 |
| 2 | `test_run_pipeline_partial_accepted` | `test_feedback_retry_accepts_partial` (test_llm_client.py:256-267) | 1 shard `status="partial"`，2 个 `"ok"`，exit 0 |
| 3 | `test_run_pipeline_one_failure_isolated` | `test_retry_on_timeout_exhausts` (test_llm_client.py:168-181) | `_failed/p005.json` 含 `error/stage/last_llm_output`，其它 2 个写到 `patients/`，exit 1 |
| 4 | `test_run_pipeline_resume_skips_existing_shards` | 无直接 analog（新模式） | 预放 `patients/p001.json` → `mock_http.post` 不被调用对 p001 |
| 5 | `test_run_pipeline_resume_retries_failed_shards` | 无直接 analog（新模式） | 预放 `_failed/p001.json` + resume → 旧 _failed 被删，新 shard 写入 |
| 6 | `test_atomic_write_no_partial_on_crash` | 无直接 analog（新单元） | mock `Path.write_text` 抛异常 → 验证目标 `.json` 不存在，`.tmp` 可能存在 |
| 7 | `test_concurrency_caps_in_flight` | `test_async_qmd_semaphore_limits_in_flight` (test_retriever_async.py:96-129) + `test_semaphore_limits_in_flight` (test_llm_client.py:280-301) | `in_flight["peak"] <= 2`（patient sem=2，5 个患者） |

**关键复用**：测试 7 完全 mirror `tests/test_retriever_async.py:99-129` 的 in_flight counter 模式：

```python
in_flight = {"current": 0, "peak": 0}

async def slow_post(*args, **kwargs):
    in_flight["current"] += 1
    in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
    await asyncio.sleep(0.05)
    in_flight["current"] -= 1
    return _mk_llm_resp()
```

---

### `tests/fixtures/mock_qmd_hit.json` (test-fixture)

**Analog:** `tests/fixtures/mock_llm_response.json` (Phase 2 落地)

**Schema** —— mirror `retriever.py:200-213` 的 `_parse_mcp_response` 输出形态：

```json
{
  "content": "对于晚期结直肠癌（mCRC），FOLFOX 或 CAPOX 为一线化疗推荐 ...",
  "path": "qmd://nccn/nccn-colorectal-2026.md",
  "score": 0.87,
  "context": "Treatment for Metastatic Disease"
}
```

---

### `tests/fixtures/mock_patient.json` (test-fixture)

**Analog:** `tests/fixtures/patient_001.json` (Phase 2 reserved，按 `parse_structured` 输出形态)

**Schema** —— mirror `batch_pipeline.py:cmd_parse` 输出的 patients[*] 形态（必含字段供 `extract_patient_features` 与 `build_queries` 使用）：

```json
{
  "patient_id": "p001",
  "patient_name": "贾常山",
  "primary_site": "结肠",
  "disease_type": "结直肠癌",
  "stage": "IV期",
  "metastasis": "肝转移",
  "molecular": "KRAS 野生型, MSI-H",
  "treatment_history": "FOLFOX 6 周期",
  "diagnosis_summary": "晚期结直肠癌伴肝转移"
}
```

---

### `scripts/batch_pipeline.py` (MODIFIED) — CLI 扩展

**Analog (self-reference):** `scripts/batch_pipeline.py:2277-2378:main()`

---

#### 1) 新增 `run` 子命令注册（CLI-01, CFG-03）

**Source pattern:** lines 2302-2314 (`p_orch` 子命令注册风格)

```python
# run (NEW — Phase 3 ship gate)
p_run = sub.add_parser("run", help="按患者并发流水线（v3.1 主路径）")
p_run.add_argument("--patients", required=True, help="patients.json 路径（parse 输出）")
p_run.add_argument("--output-dir", required=True, help="输出目录（含 patients/ _failed/ rag_results.json）")
p_run.add_argument(
    "--llm-profile",
    default=os.environ.get("LLM_PROFILE", "qwen3-vllm-lan"),
    help="LLM profile 名（默认 env LLM_PROFILE 或 qwen3-vllm-lan）",
)
p_run.add_argument(
    "--concurrency-patients",
    type=int,
    default=int(os.environ.get("PIPELINE_CONCURRENCY_PATIENTS", "5")),
    help="患者级并发上限（默认 env 或 5）",
)
p_run.add_argument(
    "--concurrency-qmd",
    type=int,
    default=int(os.environ.get("PIPELINE_CONCURRENCY_QMD", "8")),
    help="QMD 在飞请求上限（默认 env 或 8）",
)
p_run.add_argument("--resume", action="store_true", help="跳过已完成 patients 并重试 _failed/")
p_run.add_argument(
    "--kb-root",
    default=None,
    help="知识库根（默认 env MEDICAL_GUIDELINES_DIR）",
)
```

**env 优先级模式**：`default=os.environ.get(...)` 的 inline 兜底，与 `retriever.py:55` (`int(os.environ.get("QMD_PORT", "8181"))`) 完全一致。CLI flag 显式传入则覆盖 env，符合 D-10 / CFG-03。

---

#### 2) 旧 4 子命令隐藏（CLI-05, D-13）

**Source pattern:** Python argparse 标准做法 `help=argparse.SUPPRESS`

**Modify lines 2292, 2302-2304, 2317, 2344:**

```python
# split (Phase 3: hidden, Phase 4 删除)
p_split = sub.add_parser("split", help=argparse.SUPPRESS)
p_split.add_argument("--input", required=True, help="patients.json 路径")
# ... 其余 args 不变

# orchestrate (Phase 3: hidden)
p_orch = sub.add_parser("orchestrate", help=argparse.SUPPRESS)
# ... 其余 args 不变

# merge (Phase 3: hidden)
p_merge = sub.add_parser("merge", help=argparse.SUPPRESS)
# ... 其余 args 不变

# verify-batch (Phase 3: hidden)
p_verify = sub.add_parser("verify-batch", help=argparse.SUPPRESS)
# ... 其余 args 不变
```

**测试断言**（D-13）：`batch_pipeline.py --help` stdout 不含 "split" / "orchestrate" / "verify-batch" / "merge"，但 `batch_pipeline.py orchestrate --help` 仍可调用并返回正常 help。

---

#### 3) `validate` / `generate` 扩展 `--patients-dir`（CLI-02, CLI-03, D-11）

**Source pattern:** lines 2329-2336 (`p_validate` 现状)

```python
# validate
p_validate = sub.add_parser("validate", help="验证 RAG 结果质量与完整性")
g_val = p_validate.add_mutually_exclusive_group(required=True)
g_val.add_argument("--input", help="rag_results.json 路径（v3.0 兼容，deprecated）")
g_val.add_argument("--patients-dir", help="Output/patients/ 目录（v3.1 主路径）")
p_validate.add_argument("--patients", help="patients.json 路径（可选，对比完整性）")
p_validate.add_argument("--kb-profile", help="orchestration_plan.json（可选）")
```

`cmd_validate` 内分支：
- `args.patients_dir`：扫 `*.json` 走 per-patient 校验
- `args.input`：保留原 logic，**新增** `warnings.warn("--input is deprecated, use --patients-dir", DeprecationWarning)`

同款应用到 `p_gen` (lines 2351-2359)。

---

#### 4) 主 dispatch 路由新增（lines 2362-2377）

```python
args = parser.parse_args()
if args.command == "parse":
    cmd_parse(args)
elif args.command == "run":                  # NEW
    from scripts.pipeline import run_pipeline
    sys.exit(asyncio.run(run_pipeline(args)))
elif args.command == "split":
    cmd_split(args)
# ... 其余不变
```

**注意**：`asyncio.run()` 在 Python 3.9.6 可用（PEP 555 已稳定），无替代方案需求。

---

## Shared Patterns

### Pattern A: 注入式 httpx + Semaphore（D-03, D-04）

**Source:** `scripts/retriever.py:238-251` + `scripts/llm_client.py:217-226`

```python
def __init__(
    self,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
):
    self._http = http_client                         # 调用方注入
    self._owns_http = http_client is None            # 不持有所有权时不在 __aexit__ 关闭
    self._sem = semaphore or asyncio.Semaphore(N)    # default fallback
```

**Apply to:** 所有 Phase 3 pipeline.py 与 QMD/LLM 的交互边界。Phase 3 顶层是注入方，**不再**在 `__aexit__` 关闭 http_client（让 `async with httpx.AsyncClient(...)` 上下文负责）。

---

### Pattern B: `gather(*, return_exceptions=True)` + 单 task try/except

**Source:** CONTEXT D-01 + `tests/test_retriever_async.py:165-200`

```python
results = await asyncio.gather(
    *[coro(x) for x in items],
    return_exceptions=True,
)
# results[i] 若是 Exception 则单任务失败但 gather 不击垮其它
for r in results:
    if isinstance(r, Exception):
        ...  # 但 Phase 3 实际靠 _run_one_patient 内部 try/except 落盘 _failed/，
              # 因此 results 中不应该出现 Exception；asyncio.CancelledError 才会冒泡
```

**Apply to:** `run_pipeline` 顶层 gather。

---

### Pattern C: 原子写文件（D-09）

**Source:** `scripts/kb_metadata.py:236-247:_atomic_write_json`

参见上方 §4。Apply to: 所有 `_atomic_write_json` 调用（每个 patient shard、_failed shard、rag_results.json）。

---

### Pattern D: env 优先级 with int parsing

**Source:** `scripts/retriever.py:55` + `scripts/llm_client.py:160-166:_pick_int`

```python
# Inline (CLI default):
default=int(os.environ.get("FOO", "8"))

# In LLMProfile.from_env (more defensive):
def _pick_int(env_key, yaml_key, default):
    v = env.get(env_key)
    if v is not None:
        return int(v)              # 让 ValueError 显式上抛，不静默 fallback
    ...
```

**Apply to:** `--concurrency-patients` / `--concurrency-qmd` / `--llm-profile` defaults。

---

### Pattern E: AsyncMock httpx 桩 with side_effect list

**Source:** `tests/test_retriever_async.py:30-53` + `tests/test_llm_client.py:143-153`

```python
@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_xxx(mock_client_cls, mock_popen):
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[
        _mk_qmd_resp(),   # initialize
        _mk_qmd_resp(body=...),  # tools/call #1
        _mk_llm_resp(),   # llm chat completion
        ...
    ])
    mock_client_cls.return_value = mock_client
```

**Apply to:** 所有 7 个 test_pipeline.py 测试用例。

---

### Pattern F: `_run_one_patient` 内部 try/except 分级（D-05）

**Source:** `scripts/llm_client.py:269-304:_post_with_retry` 的多层 except 链式风格

```python
try:
    ...
except LLMFailure as e:                       # Phase 2 已封装好，直接 catch
    _write_failed(..., stage=e.stage, ...)
except (KeyError, ValueError) as e:           # build/extract 阶段错误
    _write_failed(..., stage="build", ...)
except asyncio.CancelledError:
    raise                                     # 必须透传
```

**绝对不要** catch `Exception` 通配 —— 会吞掉 `asyncio.CancelledError`，破坏 gather 取消语义。

---

## Anti-Patterns（Python 3.9.6 硬约束）

| Anti-Pattern | 必须替换为 | 来源 |
|--------------|-----------|------|
| `async with asyncio.TaskGroup() as tg: tg.create_task(...)` | `await asyncio.gather(*[coro(x) for x in items], return_exceptions=True)` | CONTEXT D-01 / CLAUDE.md |
| `async with asyncio.timeout(N):` | `await asyncio.wait_for(coro, timeout=N)` | CLAUDE.md |
| `try: ... except* (A, B):` | `try: ... except (A, B):` + 手动分支 | CLAUDE.md |
| `x: str \| None`（PEP 604 in runtime-evaluated contexts） | `x: Optional[str]` + `from typing import Optional` | Phase 2 REVIEW WR-06（已统一修复，Phase 3 沿用） |
| `dict[str, list[str]]` 在 dataclass field default | `Dict[str, List[str]]` + `from typing import Dict, List` | Phase 2 已立约 |

**白名单（3.9.6 允许）：**
- `asyncio.run()` — 3.7+ 稳定，Phase 3 顶层入口使用
- `asyncio.gather(*, return_exceptions=True)` — 3.7+ 稳定
- `asyncio.Semaphore(N)` + `async with sem` — 3.7+ 稳定
- `from __future__ import annotations` — 让注解延迟评估，PEP 604 写法在源码层 OK，但 runtime introspection 仍会炸（详见 Phase 2 REVIEW WR-06 —— 因此项目政策仍要求 `Optional[X]`）

**复用现有依赖（requirements.txt 已包含，Phase 3 不引新依赖）：**
- httpx>=0.27 / pytest-asyncio>=0.23 / pyyaml>=6.0 / jsonschema

---

## No Analog Found

| File / Component | Reason | Replacement Strategy |
|------------------|--------|----------------------|
| `compute_citation_coverage` | 全新算法（D-15）—— 仓库内无前身 | 按 refactor_plan §四.3 + D-15 边界规则实现（见 §6） |
| `_scan_resume` (D-08) | Resume 是 Phase 3 新模式，无前身 | 按 D-08 两段扫描语义（patients/ vs _failed/）新写，~15 行 |
| `_failed/<pid>.json` schema | 新落点 | 按 specifics 中的 4 字段 schema 落地（`patient_id/error/stage/last_llm_output/attempted_at`） |
| 进度行单行输出（D-16） | 仓库当前用 batched print，无 per-patient 单行模式 | `print(f"[{pid}] {pname} ... {STATUS} ({wall:.1f}s, coverage={score:.2f})", flush=True)`；不引入 rich/tqdm |

---

## Metadata

**Analog search scope:**
- `scripts/` (retriever.py, llm_client.py, kb_metadata.py, batch_pipeline.py)
- `tests/` (test_retriever_async.py, test_llm_client.py, test_llm_profile.py, conftest.py)
- `tests/fixtures/` (mock_llm_response.json, patient_001.json)

**Files scanned:** 14 source files + 4 planning docs (CONTEXT × 3 + ROADMAP + REQUIREMENTS + PROJECT + REVIEW)

**Pattern extraction date:** 2026-05-12

**Cross-phase contracts validated:**
- Phase 1 `AsyncQMDService.__init__(*, http_client=None, semaphore=None)` 注入式接口存在并可直接复用（`retriever.py:238-251`）✓
- Phase 2 `AsyncLLMClient(profile, http, *, semaphore=None)` 注入式接口存在（`llm_client.py:217-226`）✓
- Phase 2 `complete_structured_with_feedback(...) -> tuple[dict, float, str]` 返回三元组（第三位即 `status="ok"/"partial"`，`llm_client.py:327-355`）✓
- Phase 1 `kb_metadata.normalize_disease / filter_orgs_by_disease / filter_chunks_by_disease / load_synonym_map` 全部可 import（`kb_metadata.py:103-211`）✓
- Phase 1 `_atomic_write_json` 实现可作为参考（`kb_metadata.py:236-247`）✓

**Phase 2 REVIEW 残留 INFO（不阻塞 Phase 3）：**
- IN-01 (additionalProperties 与 OpenAI strict 兼容性) —— Phase 3 仍使用 vLLM strict 模式，不受影响
- IN-02 (patient_001.json 未引用) —— Phase 3 新增 mock_patient.json 取代
- IN-03 (Qwen3.5-35B-A3B 模型名占位) —— 运维侧通过 env 覆盖，Phase 3 不依赖具体模型名
- IN-04 (must_have 集合不全) —— 测试单测项，Phase 3 不触

---

*Pattern map: 03-pipeline-run-subcommand-interface-extension*
*Mapped: 2026-05-12*
