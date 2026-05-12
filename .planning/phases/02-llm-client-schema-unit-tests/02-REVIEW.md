---
phase: 02-llm-client-schema-unit-tests
reviewed: 2026-05-12T00:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - scripts/llm_client.py
  - tests/test_llm_client.py
  - tests/test_llm_profile.py
  - tests/fixtures/mock_llm_response.json
  - tests/fixtures/patient_001.json
  - config/llm_profiles.yaml
  - requirements.txt
findings:
  critical: 2
  warning: 6
  info: 5
  total: 13
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-05-12
**Depth:** standard
**Files Reviewed:** 7
**Status:** issues_found

## Summary

AsyncLLMClient 落地总体符合 PLAN：三条独立重试路径（transport 指数退避 / schema 立即重试 / feedback 反馈重试）的计数语义正确、`api_key_env` 间接化未泄露密钥到 dataclass / repr、`_load_profiles_yaml` 对 missing / malformed / 非 dict 三种边界都返回 `{}` 不抛错、`strict=true` 仅在 `json_schema` 模式注入。29 个新测试覆盖了 happy path、3 条重试路径、信号量限流、4xx 立即失败、env > yaml > default 优先级、api_key 懒解析等关键路径。

但有两个 BLOCKER 必须修：

1. **`complete_structured` 的 schema 重试在两层 try 之间会丢失非 SchemaError 异常的类型契约** —— 内层 `_post_with_retry` 若抛出 `LLMFailure(stage="transport")`（4xx 分支），会被外层吞掉的设想并未发生（外层只 catch `SchemaError`），但 PLAN 写「3xx 重定向时 `raise_for_status` 抛 `HTTPStatusError`」时该异常不会被包成 `LLMFailure` —— 调用方拿到的是裸 httpx 异常，破坏「失败一律走 LLMFailure」的契约。
2. **`timeout_s` 字段是悬空配置** —— `LLMProfile.timeout_s` 被 `from_env`、YAML、env 三处读写并写入测试断言（`tests/test_llm_profile.py:62/82`），但 `AsyncLLMClient` 从未把它应用到 `httpx.AsyncClient` 上；运营方调高 `LLM_TIMEOUT=300` 实际不会生效，超时由外部注入的 client 决定。配置漂移 + 静默失效是数据丢失级风险（长 prompt 会被截短为外层默认 timeout）。

另有 6 项 WARNING（feedback 重试缺上一轮 assistant 上下文、concurrency 缺正整数校验、`_DEFAULT_YAML_PATH` 相对路径、stale docstring「23-item」、模块常量定义顺序、type annotation 显式 `Optional`）和 5 项 INFO（fixture 未引用、placeholder 模型名、enum 测试 must_have 集合缺 ESMO 必检项、`additionalProperties` 与 OpenAI strict 规范冲突的兼容性提示、yaml 非 dict 分支无独立测试）。

## Critical Issues

### CR-01: `_post_with_retry` 中 `raise_for_status()` 抛出的 HTTPStatusError 绕过 LLMFailure 包装契约

**File:** `scripts/llm_client.py:286`
**Issue:**
循环体内的 try 只 catch `httpx.TimeoutException`：
```python
for attempt in range(4):
    try:
        resp = await self._http.post(...)
        if resp.status_code == 429 or resp.status_code >= 500: ...
        if 400 <= resp.status_code < 500:
            raise LLMFailure(..., stage="transport")
        resp.raise_for_status()      # ← 3xx 重定向或 httpx 自身 follow_redirects=False 时抛 HTTPStatusError
        return self._parse_and_validate(...)
    except httpx.TimeoutException as e: ...
```
当响应是 3xx（httpx 默认 `follow_redirects=False`，3xx 不会被自动 follow）或处于 redirect-limit 超限场景，`raise_for_status()` 会抛 `httpx.HTTPStatusError`。该异常：
- 不被 `except httpx.TimeoutException` 接住；
- 不被外层 `complete_structured` 的 `except SchemaError` 接住；
- 直接以裸 `httpx.HTTPStatusError` 形态冒泡到调用方。

调用方按合约只准备 catch `LLMFailure`，会被未声明的异常击穿。批量 pipeline 里这意味着 worker 协程被未捕获异常杀掉，patient_id 上下文丢失。

**Fix:**
把 `raise_for_status()` 删掉（前面已经手工处理了 429 / 5xx / 4xx），或者把它包进 `LLMFailure`：

```python
# Option A: remove raise_for_status — 显式分支已经覆盖了所有 ≥400 状态
# ...
        if 400 <= resp.status_code < 500:
            raise LLMFailure(...)
        # 2xx / 3xx → 走 parse；3xx 通常会让 resp.json() 自己挂掉
        return self._parse_and_validate(resp.json(), schema, patient_id)

# Option B: 把 raise_for_status 包成 LLMFailure
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise LLMFailure(patient_id, e, stage="transport") from e
```
推荐 Option A —— 现有显式分支已经处理 4xx/5xx，`raise_for_status` 是死代码加副作用。同时在 `tests/test_llm_client.py` 补一个 3xx 用例（如 status=302）断言抛 `LLMFailure(stage="transport")`。

---

### CR-02: `LLMProfile.timeout_s` 是悬空字段，调高 LLM_TIMEOUT 不会真正生效

**File:** `scripts/llm_client.py:113, 149, 204-216`
**Issue:**
`LLMProfile.timeout_s` 通过 `from_env`、YAML、env 三条路径被精心读入和测试（`test_llm_profile.py:62, 82, 117-122`），但 `AsyncLLMClient.__init__` 接收外部注入的 `httpx.AsyncClient` 后从不使用 `profile.timeout_s`。`self._http.post(url, json=payload, headers=...)` 没有传 `timeout=`，因此实际超时由外部 client 的默认 5s（httpx 默认）决定。

后果：
- 配置文档承诺 `timeout_s: 180`，运营方按文档调到 `LLM_TIMEOUT=300`，实际任意单调 LLM 请求仍按外部 client 的 timeout 截断；
- 长 patient prompt 在 vLLM 上首字延迟 > 5s 时直接 TimeoutException → 走完三轮 transport 重试 → LLMFailure；
- 「批量跑 100 个患者，超时配高一点」这种运维直觉静默失败。

这违反 PLAN.md REQ「LLM-04: timeout_s 可通过 LLM_TIMEOUT env 覆盖」—— 测试只验证字段值被写入 dataclass，未验证字段是否被 httpx 真实使用，等于绿测断言但行为不存在。

**Fix:**
在 `_post_with_retry` 调 post 时显式传 timeout：
```python
resp = await self._http.post(
    url, json=payload, headers=self._auth_headers,
    timeout=self.profile.timeout_s,
)
```
并补单元测试断言 `mock_http.post.await_args.kwargs["timeout"] == profile.timeout_s`。
或者在 `AsyncLLMClient.__init__` 显式校验 `http.timeout` 与 `profile.timeout_s` 一致并 warn —— 但显式传 timeout 更直接。

另一个选项是把 `LLMProfile.timeout_s` 从 dataclass 删掉，并提供一个构造 helper `LLMProfile.build_http_client()` 返回配置好 timeout 的 `httpx.AsyncClient`，让职责清晰。任选其一。

---

## Warnings

### WR-01: feedback 重试未携带模型上轮输出，校正信号缺上下文

**File:** `scripts/llm_client.py:336-339`
**Issue:**
```python
augmented = list(messages) + [{"role": "user", "content": feedback_msg}]
result2 = await self.complete_structured(augmented, schema, ...)
```
`augmented` 把原始 `messages` 后面直接追加一条 user feedback，但**没有插入** assistant 上轮回复。LLM 看到的对话是：「user：原始 prompt → user：你上轮 citation_coverage 太低请重试」，中间没有 assistant turn。这种 user-then-user 序列在 OpenAI / vLLM 上是 ill-formed dialogue：
- 模型无法知道「上轮」具体是什么内容；
- 反馈信息引用 `retrieval_sources[n-1]` 编号，但模型没有上轮输出做对照；
- 部分服务端（DeepSeek、vLLM strict chat template）会拒绝 consecutive same-role 消息。

PLAN.md 写「feedback×1 with message」，但这条 message 期望模型基于上轮做修正，而非凭空重生成。

**Fix:**
```python
# 把上轮 result 序列化回 assistant turn
augmented = list(messages) + [
    {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
    {"role": "user", "content": feedback_msg},
]
```
并在 `test_feedback_retry_recovers_above_threshold` 增加断言：`second_call_msgs[-2]["role"] == "assistant"` 且 `second_call_msgs[-2]["content"]` 是上轮 result 的 JSON。

---

### WR-02: concurrency 缺正整数校验，concurrency=0 会让 Semaphore 永久死锁

**File:** `scripts/llm_client.py:150, 216`
**Issue:**
`_pick_int("LLM_CONCURRENCY", "concurrency", default=5)` 仅做 `int(v)` 转换，不校验范围。若运维写 `LLM_CONCURRENCY=0` 或 `-1`，`asyncio.Semaphore(0)` 会让首个 `async with self._sem` 永久阻塞 —— 整个批 worker 静默挂死，无错误日志。

**Fix:**
在 `from_env` 或 `__post_init__` 校验：
```python
if concurrency < 1:
    raise ValueError(f"concurrency must be >= 1, got {concurrency}")
```
同时补 `test_llm_concurrency_zero_raises` 用例。

---

### WR-03: `_DEFAULT_YAML_PATH = Path("config/llm_profiles.yaml")` 用相对路径，cwd 漂移时静默失败

**File:** `scripts/llm_client.py:174**
**Issue:**
`Path("config/llm_profiles.yaml")` 相对当前 cwd 解析。如果某个调用方在 `Output/` 下执行脚本，或者从 IDE / launcher 启动 cwd 不在 repo 根，`_load_profiles_yaml` 会找不到文件 → 返回 `{}` → `from_env` 在没有完整 env 时报 `base_url is required` —— 而错误信息提示「define profiles.xxx.base_url in `config/llm_profiles.yaml`」，但用户检查工作区根目录文件明明在那里，会困惑很久。

**Fix:**
```python
_DEFAULT_YAML_PATH = Path(__file__).resolve().parent.parent / "config" / "llm_profiles.yaml"
```
锚定到 `scripts/llm_client.py` 的相对位置，让 cwd 无关。

---

### WR-04: 模块顶部 docstring 与代码事实不符 —— 「23-item evidence_level enum」

**File:** `scripts/llm_client.py:6`
**Issue:**
模块 docstring：「`PATIENT_RECOMMENDATION_SCHEMA` with 23-item evidence_level enum」。但实际 `_EVIDENCE_LEVEL_ENUM` 是 8+4+9+4+2 = **27 项**，测试 `test_evidence_level_enum_completeness` 也断言 `len == 27`。PATTERNS.md §2.1 expansion 后未同步 docstring。

`02-01-PLAN.md:69` 也仍写「23 个 evidence_level enum」，是 PATTERNS expansion 之前的旧文案。

**Fix:**
```python
"""...
Provides AsyncLLMClient ... and the
PATIENT_RECOMMENDATION_SCHEMA with 27-item evidence_level enum.
"""
```
顺手在 PLAN.md / SUMMARY.md 把「23」改成「27」（不改也行，phase 已收）。

---

### WR-05: 模块常量定义在 `LLMProfile` 类**之后**，类方法内引用前向依赖

**File:** `scripts/llm_client.py:117-171, 174-184`
**Issue:**
`LLMProfile.from_env` 在第 125 行引用 `_ENV_PROFILE`、`_DEFAULT_PROFILE_NAME` 等模块常量，但这些常量在第 176-184 行才定义 —— 也就是在类定义**之后**。这在 Python 运行时是 OK 的（方法体在调用时才查 globals），但：
- 任何在 import 时立刻调用 `LLMProfile.from_env()` 的代码（例如顶层默认参数 `def fn(p=LLMProfile.from_env()): ...`）都会触发 NameError；
- `_DEFAULT_YAML_PATH` 同理（在第 155 行 error message 里被引用，定义在第 174 行）；
- 静态分析器 / IDE 可能误报。

**Fix:**
把第 174-184 的常量块整体上移到第 22 行（imports 之后、class LLMFailure 之前）。零行为变化，纯结构清理。

---

### WR-06: `str | None` PEP 604 syntax 与 Python 3.9.6 目标冲突（虽运行时安全）

**File:** `scripts/llm_client.py:26, 38, 120, 302, 325, 326`
**Issue:**
Phase context 明确：「Python 3.9.6 type annotation compatibility (no `X | Y`, use `Optional[X]` / `Union[X, Y]`)」。本文件多处使用 `str | None`：

```python
def __init__(self, patient_id: str | None, last_error: Exception, stage: str):  # L26
def __init__(self, message: str, patient_id: str | None = None):                # L38
name: str | None = None,                                                        # L120
patient_id: str | None = None,                                                  # L302/326
feedback_template: str | None = None,                                           # L325
```

由于文件顶部 `from __future__ import annotations`（L9），所有注解被延迟为字符串、不在 runtime 评估，所以模块加载在 3.9.6 上不会崩。**但**：
- 任何用 `typing.get_type_hints(LLMFailure.__init__)` 做 introspection 的工具（pydantic v2、attrs converters、FastAPI deps、某些 lint）在 3.9 上 evaluate 时会抛 `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`；
- 与项目 convention「3.9 兼容」目标矛盾，新人改动很可能去掉 `from __future__ import annotations` 后立刻爆炸。

**Fix:**
统一改成 `Optional[str]`（或 `Union[str, None]`）并 `from typing import Optional`：
```python
from typing import Callable, Optional
def __init__(self, patient_id: Optional[str], last_error: Exception, stage: str):
```
保留 `from __future__ import annotations` 作为防御层，但显式 typing 兼容是项目标准。

---

## Info

### IN-01: 字典 schema 未声明 `additionalProperties: false`，与 OpenAI strict spec 冲突（但 CONTEXT D-04 明确 opt-out）

**File:** `scripts/llm_client.py:57-95`
**Issue:**
OpenAI 官方 `response_format={"type":"json_schema","strict":true}` 规范要求每个 object schema 必须显式 `additionalProperties: false` 且每个 property 都在 `required` 里，否则 OpenAI 服务端会在 chat completion 阶段直接 422 拒绝。

本 schema 三层 object（root / guideline_results.items / retrieval_sources.items）都没有 `additionalProperties: false`。`02-CONTEXT.md:80` 明确决定：「**不**加 `additionalProperties: false`（vLLM strict 模式已经强制；额外字段被服务端拒绝）」—— 这是 vLLM-only 的部署假设。

风险点（仅 info 级）：
- 一旦切到 `deepseek-cloud` profile（实际 yaml 里 `structured_mode: json_object`，绕过 strict 模式）或任何 OpenAI 真身，schema 会被拒；
- profile 与 schema 严格性的耦合是隐式 —— 文档没写「这个 schema 仅适用于 vLLM」。

**Fix:**
不改代码，但在 `PATIENT_RECOMMENDATION_SCHEMA` 上方加一行 comment：
```python
# NOTE: 故意不加 additionalProperties: false —— 仅适用于 vLLM strict 模式。
# 切到 OpenAI 官方 / 兼容严格实现时需要 per-object 加 additionalProperties: false。
# 详见 02-CONTEXT.md D-04。
```

---

### IN-02: `tests/fixtures/patient_001.json` 未被任何测试 / 脚本引用

**File:** `tests/fixtures/patient_001.json`
**Issue:**
`grep -rn patient_001 tests/ scripts/` 0 命中。该 fixture 与 Phase 2 测试集无关 —— phase 2 全部测试用 `mock_llm_response.json` 走 mock，不需要患者档案。它可能是为后续 Phase 3 prompt builder 预留，但 phase 2 收尾时把它纳入「files_reviewed」让人困惑。

**Fix:**
两选一：(a) 在 fixture 同目录加 `README.md` 注明「reserved for Phase 3 prompt builder unit tests」；(b) 推迟到 Phase 3 引入。当前 commit 不必动。

---

### IN-03: `Qwen3.5-35B-A3B` 不是真实 Qwen 模型标识符

**File:** `config/llm_profiles.yaml:8`
**Issue:**
`model: Qwen3.5-35B-A3B` —— Qwen 公开模型系列里没有 `Qwen3.5-35B-A3B`（A3B 是 active 3B params 的 MoE 后缀，对应实际型号是 `Qwen3-30B-A3B`、`Qwen2.5-MoE-...`）。这看起来是手动写的占位 / 笔误。

由于 `base_url: http://10.0.x.x:8000/v1` 是 LAN 占位符，整个 profile 不会真请求；但测试 `test_load_profiles_yaml_real_file` 把这个错误模型名 hardcode 成断言（`d["qwen3-vllm-lan"]["model"] == "Qwen3.5-35B-A3B"`），将来真要换型号时还得改测试。

**Fix:**
确认本地 vLLM 实际发布的模型名（`curl /v1/models`），替换为真实值（多半是 `Qwen3-30B-A3B` 或 `Qwen2.5-32B-Instruct`）。测试断言同步更新。

---

### IN-04: `test_evidence_level_enum_completeness` 的 must_have 集合未覆盖 ESMO 全部 9 项

**File:** `tests/test_llm_client.py:99-102`
**Issue:**
```python
assert {"I,A", "II,B", "III,C", "IV,D"}.issubset(set(_EVIDENCE_LEVEL_ENUM))
must_have = {"1A类", "I级推荐", "Category 1", "Category 3",
             "强推荐", "Strong", "弱推荐", "Weak", "N/A", "不适用"}
```
`_EVIDENCE_LEVEL_ENUM` 把 ESMO 从 4 项扩到 9 项（PATTERNS.md §2.1 expansion），但测试 must_have 仍只检查 `I,A / II,B / III,C / IV,D` —— 新增的 `I,B / II,A / II,C / IV,C / V,E` 没有显式断言。若未来 expansion 被回滚，`len == 27` 断言会失败，但若被改成另一种 27-项组合（例如把 ESMO V,E 误删、补一个 CSCO 项），测试不会发现 ESMO 缺项。

**Fix:**
```python
assert {"I,A", "I,B", "II,A", "II,B", "II,C", "III,C", "IV,C", "IV,D", "V,E"}.issubset(
    set(_EVIDENCE_LEVEL_ENUM)
)
```
顺手把所有 9 项写进去，与 PATTERNS expansion 锁定。

---

### IN-05: `_load_profiles_yaml` 「非 dict 顶层」分支无独立测试

**File:** `scripts/llm_client.py:198-201` & `tests/test_llm_profile.py:101-105`
**Issue:**
`_load_profiles_yaml` 有四条防御性返回 `{}` 的路径：(a) 文件不存在；(b) `yaml.YAMLError`；(c) `data` 不是 dict（例如 yaml 顶层是 list）；(d) `data["profiles"]` 不是 dict。

当前测试覆盖：
- (a) `test_load_profiles_yaml_missing_returns_empty` ✓
- (b) `test_load_profiles_yaml_malformed_returns_empty` ✓（malformed 触发 YAMLError）
- (c) 无
- (d) 无

如果未来 refactor 删掉 (c)/(d) 防御，测试不会变红。

**Fix:**
补两个用例：
```python
def test_load_profiles_yaml_top_level_list_returns_empty(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    assert _load_profiles_yaml(p) == {}

def test_load_profiles_yaml_profiles_is_list_returns_empty(tmp_path):
    p = tmp_path / "bad-profiles.yaml"
    p.write_text("profiles:\n  - one\n  - two\n", encoding="utf-8")
    assert _load_profiles_yaml(p) == {}
```

---

_Reviewed: 2026-05-12_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
