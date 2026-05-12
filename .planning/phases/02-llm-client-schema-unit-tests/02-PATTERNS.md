# Phase 2: LLM Client + Schema + Unit Tests — 模式映射

**Mapped:** 2026-05-12
**Files analyzed:** 4（3 新增 + 1 入仓 yaml）
**Analogs found:** 4 / 4（核心异步范式来自 Phase 1 `AsyncQMDService`，schema/enum 来自现 `_EVIDENCE_LEVEL_MEANINGS` 表）

---

## 1. File Classification（角色 / 数据流 / 类比匹配度）

| New File | Type | Role | Data Flow | Closest Analog | Match Quality |
|----------|------|------|-----------|----------------|---------------|
| `scripts/llm_client.py` | CREATE（~250 LOC） | service（HTTP 客户端 + 重试 + schema 强约束） | request-response（async） | `scripts/retriever.py:AsyncQMDService`（Phase 1 落地） | **exact**（同 service role，同 async/httpx/sem 模式，差异：LLM 无子进程） |
| `config/llm_profiles.yaml` | CREATE（入仓） | config（静态元数据） | static-config | `~/.config/qmd/index.yml`（外部）+ `synonym_map.yaml`（Phase 1 运行时） | **partial**（同 yaml 风格，但 Phase 1 yaml 在 KB 外部不入仓；llm_profiles.yaml 是仓内 config） |
| `tests/test_llm_client.py` | CREATE | test（async 单元测试） | request-response（mocked） | `tests/test_retriever_async.py`（Phase 1 落地） | **exact**（同 `@pytest.mark.asyncio` + `AsyncMock` 风格） |
| `tests/fixtures/patient_001.json` + `mock_llm_response.json` | CREATE | test-fixture（schema-valid 样例） | static-data | `tests/conftest.py:mock_kb` fixture 风格 | **role-match**（同 fixture 思路，但 fixture 是文件而非 pytest fixture） |

---

## 2. Pattern Assignments（per-file 代码切片）

### 2.1 `scripts/llm_client.py`（CREATE，~250 LOC）— role: service, data flow: request-response (async)

**Analog 1：`scripts/retriever.py:AsyncQMDService`（Phase 1 落地的异步基线）**

**构造参数风格直接照搬**（retriever.py `AsyncQMDService.__init__`）：

```python
class AsyncLLMClient:
    """OpenAI 兼容异步 LLM 客户端，支持 strict JSON Schema 输出。"""

    def __init__(
        self,
        profile: LLMProfile,
        http: httpx.AsyncClient,           # 注入式（必传，调用方持有生命周期）
        *,
        semaphore: asyncio.Semaphore | None = None,
    ):
        self.profile = profile
        self._http = http                  # 不创建 owned client（与 AsyncQMDService 关键差异：QMD 默认自建 client，LLM 强制注入）
        self._sem = semaphore or asyncio.Semaphore(profile.concurrency)
```

> **差异点 vs `AsyncQMDService`**：QMD 的 `http_client` 默认 `None` 时内部自建（便于独立单元测试）；LLM client 把 http 设为**必传**参数（不是 keyword-only），因为 Phase 3 `pipeline.py` 会显式共享一个 `httpx.AsyncClient` 给 QMD + LLM，**不允许** LLM 自建。Phase 2 单元测试用 `httpx.AsyncClient(transport=httpx.MockTransport(handler))` 或直接 patch `client.post` 注入。

**Semaphore 限流模式**（retriever.py async query 主体）：

```python
async def complete_structured(self, messages, schema, *, schema_name="patient_recommendation",
                               patient_id: str | None = None) -> dict:
    async with self._sem:
        return await self._post_with_retry(messages, schema, schema_name, patient_id)
```

**Analog 2：`scripts/retriever.py:AsyncQMDService._post_with_retry`（session retry 模式）**

LLM 的 transport-level retry 与 QMD 的 session-id retry 形式相似（都是"失败 → 等 → 重试"），但策略不同：

| 维度 | QMD `_post_with_retry`（Phase 1） | LLM `_post_with_retry`（Phase 2） |
|------|----------------------------------|--------------------------------|
| 触发 | HTTP 400 或缺 session header | HTTP 429/5xx 或 `httpx.TimeoutException` |
| 重试次数 | 1 次（re-initialize 后再发） | 3 次（指数退避） |
| 退避 | 无（立即重试） | base=1s, `2 ** attempt` |
| 失败语义 | `raise_for_status()` 上抛 | 抛 `LLMFailure(stage="transport")` |

**推荐结构**（手写循环，不引入 httpx-retry）：

```python
async def _post_with_retry(self, messages, schema, schema_name, patient_id):
    payload = self._build_payload(messages, schema, schema_name)
    last_error: Exception | None = None
    for attempt in range(4):                   # 1 initial + 3 retries
        try:
            resp = await self._http.post(
                f"{self.profile.base_url}/chat/completions",
                json=payload,
                headers=self._auth_headers,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
                await asyncio.sleep(1.0 * (2 ** attempt))
                continue
            resp.raise_for_status()
            return self._parse_and_validate(resp.json(), schema, patient_id)
        except httpx.TimeoutException as e:
            last_error = e
            if attempt == 3:
                break
            await asyncio.sleep(1.0 * (2 ** attempt))
    raise LLMFailure(patient_id, last_error, stage="transport")
```

**Schema 失败 retry（独立路径，不进 transport budget）**：

```python
def _parse_and_validate(self, response_json, schema, patient_id):
    """解析 vLLM 响应，校验 schema；失败抛 ValueError 由上游决定 retry。"""
    try:
        content = response_json["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        # vLLM strict 模式服务端已校验；客户端冗余校验仅为防御非 strict profile（DeepSeek）
        jsonschema.validate(parsed, schema)
        return parsed
    except (KeyError, json.JSONDecodeError, jsonschema.ValidationError) as e:
        raise SchemaError(str(e), patient_id) from e

# complete_structured 内部：
try:
    return await self._post_with_retry(...)
except SchemaError:
    # 立即重试一次，不退避
    return await self._post_with_retry(...)
```

> **决策**：planner 在 PLAN.md 中需选定是否真的引入 `jsonschema` 依赖（vLLM strict 已服务端强制，客户端校验冗余）。**推荐**：引入 `jsonschema>=4.0`（已是事实标准，~20KB），覆盖 DeepSeek `json_object` profile 的客户端兜底；写入 `requirements.txt`。

**Analog 3：feedback retry 的高阶组合**（仓库无先例，需建立）

```python
async def complete_structured_with_feedback(
    self,
    messages: list[dict],
    schema: dict,
    *,
    schema_name: str = "patient_recommendation",
    feedback_check: Callable[[dict], float],
    threshold: float = 0.5,
    feedback_template: str | None = None,
    patient_id: str | None = None,
) -> tuple[dict, float, str]:
    """一次调用 + 阈值不足时带反馈重试一次。

    Returns:
        (result_dict, final_score, status)  # status ∈ {"ok", "partial"}
    """
    result = await self.complete_structured(messages, schema, schema_name=schema_name, patient_id=patient_id)
    score = feedback_check(result)
    if score >= threshold:
        return result, score, "ok"
    feedback_msg = (feedback_template or _DEFAULT_FEEDBACK_TEMPLATE).format(
        prev=score, threshold=threshold
    )
    augmented = messages + [{"role": "user", "content": feedback_msg}]
    result2 = await self.complete_structured(augmented, schema, schema_name=schema_name, patient_id=patient_id)
    score2 = feedback_check(result2)
    return result2, score2, ("ok" if score2 >= threshold else "partial")
```

**`LLMFailure` 异常**（CONTEXT.md D-08）：

```python
class LLMFailure(Exception):
    def __init__(self, patient_id: str | None, last_error: Exception, stage: str):
        self.patient_id = patient_id
        self.last_error = last_error
        self.stage = stage              # "transport" | "schema" | "feedback"
        super().__init__(f"LLM call failed at stage={stage} for patient={patient_id}: {last_error}")
```

**`PATIENT_RECOMMENDATION_SCHEMA` 字面量**（CONTEXT.md D-04 / D-05）：

完整 23 个 `evidence_level` enum 值必须**逐项列出**（不允许 `...`）：

```python
_EVIDENCE_LEVEL_ENUM = [
    # CSCO（8）
    "1A类", "1B类", "2A类", "2B类", "3类",
    "I级推荐", "II级推荐", "III级推荐",
    # NCCN（4）
    "Category 1", "Category 2A", "Category 2B", "Category 3",
    # ESMO（9）
    "I,A", "I,B", "II,A", "II,B", "II,C", "III,C", "IV,C", "IV,D", "V,E",
    # JGCA/CACA（4）
    "强推荐", "弱推荐", "Strong", "Weak",
    # 通用（2）
    "不适用", "N/A",
]

PATIENT_RECOMMENDATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "guideline_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "guideline": {"type": "string",
                                  "enum": ["CSCO", "NCCN", "ESMO", "JGCA", "CACA"]},
                    "guideline_version": {"type": "string", "minLength": 3},
                    "recommendation": {"type": "string", "minLength": 30},
                    "evidence_level": {"type": "string", "enum": _EVIDENCE_LEVEL_ENUM},
                    "source_file": {"type": "string"},
                    "retrieval_sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_file": {"type": "string"},
                                "score": {"type": "number"},
                            },
                            "required": ["source_file", "score"],
                        },
                    },
                },
                "required": ["guideline", "guideline_version", "recommendation",
                             "evidence_level", "source_file", "retrieval_sources"],
            },
        },
        "consensus": {"type": "array", "items": {"type": "string"}},
        "differences": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["guideline_results", "consensus", "differences"],
}
```

> **核对源**：每个 enum 项必须与 `scripts/batch_pipeline.py:1896-1923` 的 `_EVIDENCE_LEVEL_MEANINGS` 正则一一对应（去掉正则的 `\s*` / `\b` 等元字符后的字面量）。Planner 在 PLAN.md 中需逐条核对计数（28 条正则 → 23 个 enum，差额来自正则同时匹配多种格式如 `r"Strong|强推荐"` 拆成两个 enum）。

---

### 2.2 `config/llm_profiles.yaml`（CREATE，入仓）— role: config, data flow: static-config

**Analog：`docs/refactor_plan_2026-05-11.md:第六节` yaml 样例 + Phase 1 `synonym_map.yaml` 加载逻辑**

**文件内容**（refactor_plan §六，直接落库）：

```yaml
profiles:
  qwen3-vllm-lan:
    base_url: http://10.0.x.x:8000/v1     # 占位符，运维侧通过 env LLM_BASE_URL 覆盖实际 IP
    model: Qwen3.5-35B-A3B
    api_key_env: LLM_API_KEY
    timeout_s: 180
    structured_mode: json_schema
    concurrency: 5
  deepseek-cloud:
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    timeout_s: 60
    structured_mode: json_object
    concurrency: 10
```

**加载逻辑（在 `llm_client.py` 内）**：

```python
_DEFAULT_YAML_PATH = Path("config/llm_profiles.yaml")

def _load_profiles_yaml(path: Path = _DEFAULT_YAML_PATH) -> dict[str, dict]:
    """读 yaml；不存在返回 {}（不报错，env-only 路径下完全可工作）。"""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("profiles", {})
```

**Source**：`scripts/kb_metadata.py:load_synonym_map`（Phase 1 落地）已建立"yaml 不存在则返回 seed 默认"的模式。Phase 2 沿用，但默认是空 dict（env-only 仍可工作）。

**入仓决策**（CONTEXT.md D-12 + D-11 末段）：
- 本文件**入仓**：内容仅 base_url / model / timeout 等元数据，**不含 secrets**（`api_key_env` 仅指向 env 名）。
- 运维侧用 env `LLM_BASE_URL` 覆盖 `10.0.x.x` 占位符。`.gitignore` 不需调整。

---

### 2.3 `tests/test_llm_client.py`（CREATE）— role: test, data flow: request-response (async, mocked)

**Analog：`tests/test_retriever_async.py`（Phase 1 落地的 async mock 风格）**

**Mock 模式**（Phase 1 已建立）：

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from scripts.llm_client import AsyncLLMClient, LLMProfile, LLMFailure, PATIENT_RECOMMENDATION_SCHEMA


@pytest.fixture
def profile():
    return LLMProfile(
        name="test",
        base_url="http://test.local/v1",
        model="test-model",
        api_key_env="TEST_API_KEY",
        timeout_s=10,
        structured_mode="json_schema",
        concurrency=2,
    )


@pytest.fixture
def mock_http():
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.mark.asyncio
async def test_complete_structured_happy_path(profile, mock_http, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test")
    mock_http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "choices": [{"message": {"content": json.dumps(VALID_RESPONSE_FIXTURE)}}]
        },
    )
    client = AsyncLLMClient(profile, http=mock_http)
    result = await client.complete_structured(
        [{"role": "user", "content": "test"}],
        PATIENT_RECOMMENDATION_SCHEMA,
        patient_id="p001",
    )
    assert result == VALID_RESPONSE_FIXTURE
    mock_http.post.assert_called_once()
```

**Test case 清单**（LLM-01..05 全覆盖）：

| Case | 验证 | LLM-X |
|------|------|-------|
| `test_complete_structured_happy_path` | 单次成功调用，返回 parsed dict | LLM-01 |
| `test_response_format_strict_schema_payload` | payload 含 `response_format.type=="json_schema" + strict=True` | LLM-01 |
| `test_evidence_level_enum_completeness` | `PATIENT_RECOMMENDATION_SCHEMA` enum 含全部 23 个变体（逐项 assert in） | LLM-02 |
| `test_evidence_level_enum_matches_legacy_table` | enum 集合与 `batch_pipeline._EVIDENCE_LEVEL_MEANINGS` 提取的字面量一致 | LLM-02 |
| `test_schema_required_fields` | guideline_results 项 6 个 required 字段缺一即 schema fail | LLM-02 |
| `test_retry_on_429` | side_effect = [429, 429, 200] → 3 次调用，最终成功 | LLM-04 |
| `test_retry_on_5xx` | side_effect = [503, 200] → 2 次调用，最终成功 | LLM-04 |
| `test_retry_on_timeout` | side_effect = [TimeoutException, TimeoutException, TimeoutException, TimeoutException] → 抛 LLMFailure(stage="transport") | LLM-04, LLM-05 |
| `test_retry_exponential_backoff` | patch `asyncio.sleep`，断言传入参数 = [1.0, 2.0, 4.0] | LLM-04 |
| `test_schema_failure_retry_once` | side_effect = [invalid_json, valid_json] → 2 次调用成功 | LLM-04 |
| `test_schema_failure_max_retries` | side_effect = [invalid_json, invalid_json] → 抛 LLMFailure(stage="schema") | LLM-04, LLM-05 |
| `test_feedback_retry_accepts_partial` | feedback_check = [0.3, 0.4]（两次都低于 0.5）→ status="partial" 接受 | LLM-04 |
| `test_feedback_retry_recovers_above_threshold` | feedback_check = [0.3, 0.7] → status="ok"，total 2 次调用 | LLM-04 |
| `test_feedback_skip_when_first_call_meets_threshold` | feedback_check = [0.8] → 1 次调用，status="ok" | LLM-04 |
| `test_llm_failure_carries_patient_id_and_stage` | LLMFailure(p_id="p001", stage="transport").patient_id == "p001" | LLM-05 |
| `test_semaphore_limits_inflight` | 注入 Semaphore(2)，并发 5 个 complete_structured，断言任一时刻 in-flight ≤2 | LLM-01（注入式 sem） |

**Profile / config 测试**（CFG-01, CFG-02）：

| Case | 验证 | CFG-X |
|------|------|-------|
| `test_llm_profile_from_env_all_set` | 7 个 LLM_* env 全设 → LLMProfile 字段一致 | CFG-01 |
| `test_llm_profile_from_env_partial_falls_back_to_yaml` | env 仅 LLM_PROFILE，yaml 有完整配置 → 取 yaml 值 | CFG-01, CFG-02 |
| `test_llm_profile_yaml_falls_back_to_default` | yaml 缺 timeout_s → 取 dataclass 默认 180 | CFG-02 |
| `test_load_profiles_yaml_missing_returns_empty` | 文件不存在 → 返回 {} 不报错 | CFG-02 |
| `test_load_profiles_yaml_real_file` | 加载 `config/llm_profiles.yaml` → 含 qwen3-vllm-lan + deepseek-cloud 两 key | CFG-02 |
| `test_llm_timeout_invalid_raises` | `LLM_TIMEOUT=abc` → ValueError（非静默 fallback） | CFG-01 |
| `test_api_key_resolved_lazily` | profile.api_key_env="LLM_API_KEY"; os.environ["LLM_API_KEY"]="sk-x"; client._auth_headers 含 "Authorization: Bearer sk-x" | CFG-01 |

---

### 2.4 `tests/fixtures/patient_001.json` + `mock_llm_response.json`（CREATE）

**Analog：`tests/conftest.py:mock_kb` fixture 风格 + refactor_plan §九 验证样例**

**`patient_001.json`**（refactor_plan §九 Phase 2 验证用，单例患者）：

```json
{
  "patient_id": "test_001",
  "name": "测试患者",
  "gender": "男",
  "age": 65,
  "disease_type": "胃癌",
  "primary_site": "胃窦",
  "pathology": "腺癌中分化",
  "staging_prefix": "c",
  "t_stage": "T3",
  "n_stage": "N1",
  "m_stage": "M0",
  "tnm_stage": "III A",
  "diagnosis_summary": "男性，65岁，胃窦腺癌中分化，cT3N1M0 III A"
}
```

> 用于 ROADMAP Success Criteria #1 的人工验收（`python -m scripts.llm_client --fixture tests/fixtures/patient_001.json`）。**不在自动 pytest 范围内**。

**`mock_llm_response.json`**（schema-valid，用于 retry 路径 unit test 注入 `mock.post.return_value.json`）：

```json
{
  "guideline_results": [
    {
      "guideline": "CSCO",
      "guideline_version": "2024",
      "recommendation": "对于 cT3N1M0 胃腺癌，推荐根治性手术 D2 淋巴结清扫，术后辅助化疗 SOX 方案 6 个月。",
      "evidence_level": "1A类",
      "source_file": "csco-gastric-2024.md",
      "retrieval_sources": [
        {"source_file": "csco-gastric-2024.md", "score": 0.92}
      ]
    }
  ],
  "consensus": ["术后辅助化疗"],
  "differences": []
}
```

**约束**：
- `evidence_level` 必须在 23 个 enum 内
- `guideline_version` ≥ 3 字符
- `recommendation` ≥ 30 字符
- `retrieval_sources` 至少 1 项

---

## 3. Shared Patterns（跨多个新文件复用）

### 3.1 `from __future__ import annotations`（必加）

**Source**：`scripts/retriever.py:12` + Phase 1 全部新文件已遵循

Apply to：`scripts/llm_client.py`、`tests/test_llm_client.py`。Python 3.9.6 下 `dict[str, list]` / `int | None` 才能在运行期不报 TypeError。

### 3.2 Frozen dataclass 字段顺序约定

**Source**：本仓**无先例**，本 Phase 第一次引入；planner 锚定以下顺序（必填字段在前、可选默认在后）：

```python
@dataclass(frozen=True)
class LLMProfile:
    name: str
    base_url: str
    model: str
    api_key_env: str = "LLM_API_KEY"
    max_tokens: int = 4096
    temperature: float = 0.1
    timeout_s: int = 180
    structured_mode: str = "json_schema"
    concurrency: int = 5
```

### 3.3 `pytest-asyncio` strict mode

**Source**：Phase 1 `conftest.py` 已启用（`pytest-asyncio>=0.23,<1.0` in requirements.txt + `asyncio_mode=strict` 隐式 strict via marker）。Phase 2 沿用，每个 async test 必须 `@pytest.mark.asyncio` 装饰；无需修改 conftest。

### 3.4 `httpx.MockTransport` vs `AsyncMock` 二选一

**Source**：Phase 1 `tests/test_retriever_async.py` 使用 `AsyncMock(spec=httpx.AsyncClient)` 直接 patch `client.post`。

Phase 2 同款。但若 planner 在 PLAN.md 中倾向 `httpx.MockTransport(handler)`（更接近真实 http stack，能验证 URL 拼接），也可接受 — 二者均**不破坏** Phase 1 模式。**推荐**：与 Phase 1 一致用 `AsyncMock` 减少认知开销。

### 3.5 yaml 加载防御（FileNotFoundError → {}）

**Source**：`scripts/kb_metadata.py:load_synonym_map`（Phase 1）

Apply to：`scripts/llm_client.py:_load_profiles_yaml`。yaml 不存在/为空 → 返回 `{}`，env-only 路径仍可工作。

---

## 4. Gotchas / Landmines

### 4.1 vLLM strict schema 拒绝 enum 外字面量

vLLM 0.5+ 在 sampling 阶段就拒绝违反 enum 的 token —— **若 enum 漏掉某个变体**（如 `"Category 2a"` 小写形式），模型 retry 也无法产出该 token，最终客户端收到 `"N/A"` 或重排版本。**Mitigation**：planner 在 `_EVIDENCE_LEVEL_ENUM` 中**只用 `_EVIDENCE_LEVEL_MEANINGS` 表的字面量**（不含正则元字符），并加 `"N/A"` + `"不适用"` 兜底。

### 4.2 `asyncio.sleep` 在测试中必须 patch（不能跑真实退避）

`test_retry_exponential_backoff` 若不 patch `asyncio.sleep`，1+2+4=7s 跑测试不可接受。**Pattern**：

```python
@patch("scripts.llm_client.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_backoff(mock_sleep, ...):
    ...
    assert [c.args[0] for c in mock_sleep.call_args_list] == [1.0, 2.0, 4.0]
```

### 4.3 `httpx.HTTPStatusError` 不要传播到 `LLMFailure`

`resp.raise_for_status()` 抛 `HTTPStatusError`，但 4xx（除 429）应该立即失败而非 retry。**推荐**：在 `_post_with_retry` 内显式分类：

```python
if 400 <= resp.status_code < 500 and resp.status_code != 429:
    raise LLMFailure(patient_id, last_error=httpx.HTTPStatusError(...), stage="transport")
```

429 与 5xx 进 retry budget；4xx（认证失败、bad request）立即抛。

### 4.4 `LLM_API_KEY` 不要存进 frozen dataclass

`@dataclass(frozen=True)` 的字段会出现在 `repr()` / 日志 / 序列化里。**绝不**把 `api_key` 明文字段加进 `LLMProfile`；只存 `api_key_env`（env 变量**名**），调用时 `os.environ[self.profile.api_key_env]` 解析值。

### 4.5 yaml `10.0.x.x` 占位符不能被 strict URL 校验

`config/llm_profiles.yaml` 的 base_url `http://10.0.x.x:8000/v1` 是占位符，运维侧用 env 覆盖。Planner 不要在 `LLMProfile.__post_init__` 加 `urllib.parse.urlparse` 严格校验，否则 yaml 加载即失败。**推荐**：构造时不校验，第一次 `await self._http.post(base_url, ...)` 失败时由 httpx 自然报错。

### 4.6 Python 3.9.6 不支持 `match` / `TaskGroup` / `except*`

CONTEXT.md 已警告。`_post_with_retry` 用经典 `for attempt in range(4)` + `if/elif` 分支，不写 `match resp.status_code`。

### 4.7 `jsonschema` 依赖入仓决策

CONTEXT.md Claude's Discretion 未定：planner 决定是否引入 `jsonschema>=4.0`。**推荐**：引入（~20KB，是事实标准），写入 `requirements.txt`；理由：(a) DeepSeek `json_object` profile 没有服务端 strict，客户端校验是唯一兜底；(b) 测试可断言 schema 字面量正确（`jsonschema.Draft7Validator(SCHEMA).check_schema()`）。

---

## 5. No-Analog Notes

| File | Reason | 替代来源 |
|------|--------|----------|
| `complete_structured_with_feedback` 高阶组合 | 仓库无"调用 → 算分 → 不达标重试"模式 | 本 PATTERNS.md §2.1 + refactor_plan §四.1 retry 段 |
| Frozen dataclass | 仓库首次使用 | PEP 557 + Phase 2 PATTERNS.md §3.2 锚定字段顺序 |
| `jsonschema.validate` 客户端校验 | 仓库无 schema 校验先例（QMD 响应直接 dict 解析） | jsonschema 官方文档；planner 在 PLAN.md 决策是否引入依赖 |

---

## 6. Metadata

**Analog search scope**：`scripts/`、`scripts/extraction/`、`tests/`、`docs/refactor_plan_2026-05-11.md`、`.planning/phases/01-*/`
**Files scanned**：`scripts/retriever.py`、`scripts/kb_metadata.py`、`scripts/batch_pipeline.py:1880-1930` + `:411-432` + `:743-887`、`tests/test_retriever_async.py`、`tests/conftest.py`、`tests/test_kb_metadata.py`、`requirements.txt`、`.planning/phases/01-*/01-CONTEXT.md` + `01-PATTERNS.md`、`docs/refactor_plan_2026-05-11.md` §四.1 + §六 + §九 + §十.1
**Async pattern source**：Phase 1 `AsyncQMDService`（落地 2026-05-12 commits a5ca1a1..523a552），Phase 2 直接 inherit
**Pattern extraction date**：2026-05-12
