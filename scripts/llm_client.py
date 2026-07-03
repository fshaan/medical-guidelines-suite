"""Async LLM client with strict JSON Schema output and multi-path retry.

Provides AsyncLLMClient for OpenAI-compatible structured output via
response_format={"type":"json_schema","strict":true}. Includes three
independent retry paths (transport / schema / feedback) and the
PATIENT_RECOMMENDATION_SCHEMA with 27-item evidence_level enum.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Callable, Optional, Union

import httpx
import jsonschema
import yaml
from pathlib import Path


_ENV_PROFILE = "LLM_PROFILE"
_ENV_BASE_URL = "LLM_BASE_URL"
_ENV_MODEL = "LLM_MODEL"
_ENV_API_KEY_ENV = "LLM_API_KEY_ENV"
_ENV_TIMEOUT = "LLM_TIMEOUT"
_ENV_STRUCTURED_MODE = "LLM_STRUCTURED_MODE"
_ENV_CONCURRENCY = "LLM_CONCURRENCY"
_ENV_MAX_TOKENS = "LLM_MAX_TOKENS"

_DEFAULT_PROFILE_NAME = "qwen3-vllm-lan"
_DEFAULT_YAML_PATH = Path(__file__).resolve().parent.parent / "config" / "llm_profiles.yaml"


class LLMFailure(Exception):
    """LLM call failed after all retries exhausted."""

    def __init__(self, patient_id: Optional[str], last_error: Exception, stage: str):
        self.patient_id = patient_id
        self.last_error = last_error
        self.stage = stage
        super().__init__(
            f"LLM call failed at stage={stage} for patient={patient_id}: {last_error}"
        )


class SchemaError(Exception):
    """Internal control-flow exception for schema validation failures."""

    def __init__(self, message: str, patient_id: Optional[str] = None):
        super().__init__(message)
        self.patient_id = patient_id


class DegenerationError(Exception):
    """LLM 输出退化（重复生成 / 撞 max_tokens 上限）。

    2026-07-03（codex 审查采纳）：决定性对比测试证明 finish_reason=length +
    高重复度几乎总是模型陷入重复循环撞 max_tokens 上限，不是真实输出被截断。
    正常输出 finish=stop（约 1000 token）。携带诊断字段供 pipeline 层换策略
    重试或写 _failed/stage=degeneration，不伪装成 partial（QG-02 诚实）。
    """

    def __init__(self, patient_id, finish_reason, completion_tokens, repetition, content_preview):
        self.patient_id = patient_id
        self.finish_reason = finish_reason
        self.completion_tokens = completion_tokens
        self.repetition = repetition
        self.content_preview = content_preview
        super().__init__(
            f"degeneration: finish={finish_reason} comp_tok={completion_tokens} "
            f"rep={repetition:.2f} (preview: {content_preview!r})"
        )


def _repetition_score(text: str) -> float:
    """0=无重复，1=高度重复。按 200 字符分块，1 - unique/total。"""
    if len(text) < 400:
        return 0.0
    chunk = 200
    chunks = [text[i : i + chunk] for i in range(0, len(text), chunk)]
    if len(chunks) < 2:
        return 0.0
    return 1.0 - len(set(chunks)) / len(chunks)


def _normalize_evidence_level(value) -> str:
    """把模型输出的 evidence_level 变体归一化到 schema enum。

    json_object 模式丢失 strict 的 enum 强制（实测模型常输出 '1A' 而非 '1A类'、
    '1类证据' 等简写）。前缀/去空白匹配到标准 enum；无法匹配兜底 '不适用'
    （保 QG-04 enum 合法，语义损失可接受——模型本就该用标准 enum 值）。
    """
    if not isinstance(value, str) or value in _EVIDENCE_LEVEL_ENUM:
        return value
    for e in _EVIDENCE_LEVEL_ENUM:
        if len(value) >= 2 and e.startswith(value):
            return e
    v = value.replace(" ", "").upper()
    for e in _EVIDENCE_LEVEL_ENUM:
        if e.replace(" ", "").upper() == v:
            return e
    return "不适用"


def _normalize_result(parsed):
    """归一化 PATIENT_RECOMMENDATION_SCHEMA 输出的 evidence_level 变体。
    对非该 schema 的 parsed（如测试用的自由 schema）是 no-op。"""
    if not isinstance(parsed, dict):
        return parsed
    for gr in parsed.get("guideline_results", []) or []:
        if isinstance(gr, dict) and "evidence_level" in gr:
            gr["evidence_level"] = _normalize_evidence_level(gr["evidence_level"])
    return parsed


_EVIDENCE_LEVEL_ENUM: list[str] = [
    # CSCO（8）
    "1A类", "1B类", "2A类", "2B类", "3类",
    "I级推荐", "II级推荐", "III级推荐",
    # NCCN（4）
    "Category 1", "Category 2A", "Category 2B", "Category 3",
    # ESMO（9 —— expanded per PATTERNS.md §2.1）
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
                    "guideline": {
                        "type": "string",
                        "enum": ["CSCO", "NCCN", "ESMO", "JGCA", "CACA"],
                    },
                    "guideline_version": {"type": "string", "minLength": 3},
                    "recommendation": {"type": "string", "minLength": 30, "maxLength": 600},
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
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "guideline", "guideline_version", "recommendation",
                    "evidence_level", "source_file", "retrieval_sources",
                ],
                "additionalProperties": False,
            },
        },
        "consensus": {"type": "array", "items": {"type": "string", "maxLength": 150}},
        "differences": {"type": "array", "items": {"type": "string", "maxLength": 150}},
    },
    "required": ["guideline_results", "consensus", "differences"],
    "additionalProperties": False,
}

_DEFAULT_FEEDBACK_TEMPLATE = (
    "上轮输出 citation_coverage = {prev:.2f} 低于阈值 {threshold:.2f}。\n"
    "请重新检索并补充 retrieval_sources，确保每条 guideline_results 至少引用 2 个独立 source_file，\n"
    "且 recommendation 文本中引用 [n] 编号与 retrieval_sources[n-1].source_file 对应。\n"
    "保持原 schema 不变。"
)


@dataclass(frozen=True)
class LLMProfile:
    name: str
    base_url: str
    model: str
    api_key_env: str = "LLM_API_KEY"
    # 2026-07-03 决定性对比测试（codex 审查）纠正了 2026-07-02 的判断：当时认为
    # "8192 截断在 24309-24461 字符处 → 调回 65536"，但实测证明那些 length 全是
    # 退化（comp_tok 撞满 max_tokens + repetition 0.89-0.97），不是真截断。真实
    # 规律：退化随机触发，max_tokens 不决定是否退化，只决定退化时烧多久（vLLM
    # ~120 tok/s：8192→68s 快速失败，65536→546s ReadTimeout）。正常输出 finish=stop
    # ~1000 token。8192 给 8x 余量，退化时 68s 交由 _llm_with_degeneration_fallback
    # 三档降级处理，而非 65536 几百秒拖垮整批。可经 LLM_MAX_TOKENS env / yaml 覆盖。
    max_tokens: int = 8192
    temperature: float = 0.1
    timeout_s: int = 180
    structured_mode: str = "json_schema"
    concurrency: int = 5

    def __post_init__(self):
        if self.concurrency < 1:
            raise ValueError(
                f"concurrency must be >= 1 (Semaphore(0) deadlocks indefinitely), got {self.concurrency}"
            )
        if self.timeout_s < 1:
            raise ValueError(f"timeout_s must be >= 1, got {self.timeout_s}")
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {self.max_tokens}")

    @classmethod
    def from_env(
        cls,
        name: Optional[str] = None,
        *,
        yaml_path: Optional[Union[str, os.PathLike]] = None,
    ) -> "LLMProfile":
        env = os.environ
        resolved_name = name if name is not None else env.get(_ENV_PROFILE, _DEFAULT_PROFILE_NAME)
        yaml_profiles = _load_profiles_yaml(yaml_path)
        yaml_dict = yaml_profiles.get(resolved_name, {}) if isinstance(yaml_profiles, dict) else {}

        def _pick_str(env_key, yaml_key, default=None):
            v = env.get(env_key)
            if v is not None:
                return v
            if yaml_key in yaml_dict and yaml_dict[yaml_key] is not None:
                return yaml_dict[yaml_key]
            return default

        def _pick_int(env_key, yaml_key, default):
            v = env.get(env_key)
            if v is not None:
                return int(v)
            if yaml_key in yaml_dict and yaml_dict[yaml_key] is not None:
                return int(yaml_dict[yaml_key])
            return default

        base_url = _pick_str(_ENV_BASE_URL, "base_url")
        model = _pick_str(_ENV_MODEL, "model")
        api_key_env = _pick_str(_ENV_API_KEY_ENV, "api_key_env", default="LLM_API_KEY")
        structured_mode = _pick_str(_ENV_STRUCTURED_MODE, "structured_mode", default="json_schema")
        timeout_s = _pick_int(_ENV_TIMEOUT, "timeout_s", default=180)
        concurrency = _pick_int(_ENV_CONCURRENCY, "concurrency", default=5)
        max_tokens = _pick_int(_ENV_MAX_TOKENS, "max_tokens", default=8192)

        if base_url is None:
            raise ValueError(
                f"base_url is required: set {_ENV_BASE_URL} env or define "
                f"profiles.{resolved_name}.base_url in {yaml_path or _DEFAULT_YAML_PATH}"
            )
        if model is None:
            raise ValueError(
                f"model is required: set {_ENV_MODEL} env or define "
                f"profiles.{resolved_name}.model in {yaml_path or _DEFAULT_YAML_PATH}"
            )

        return cls(
            name=resolved_name,
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            timeout_s=timeout_s,
            structured_mode=structured_mode,
            concurrency=concurrency,
            max_tokens=max_tokens,
        )


def _load_profiles_yaml(
    path: Optional[Union[str, os.PathLike]] = None,
) -> dict:
    p = Path(path) if path is not None else _DEFAULT_YAML_PATH
    if not p.exists():
        return {}
    try:
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    profiles = data.get("profiles", {})
    return profiles if isinstance(profiles, dict) else {}


class AsyncLLMClient:
    """OpenAI-compatible async LLM client with strict JSON Schema output."""

    def __init__(
        self,
        profile: LLMProfile,
        http: httpx.AsyncClient,
        *,
        semaphore: Optional[asyncio.Semaphore] = None,
    ):
        self.profile = profile
        self._http = http
        self._sem = semaphore or asyncio.Semaphore(profile.concurrency)

    @property
    def _auth_headers(self) -> dict:
        api_key = os.environ.get(self.profile.api_key_env, "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _build_payload(
        self, messages, schema, schema_name,
        *,
        max_tokens_override: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        structured_mode_override: Optional[str] = None,
    ):
        payload = {
            "model": self.profile.model,
            "messages": messages,
            "temperature": self.profile.temperature,
            "max_tokens": max_tokens_override
            if max_tokens_override is not None
            else self.profile.max_tokens,
        }
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        mode = structured_mode_override or self.profile.structured_mode
        if mode == "json_schema":
            # schema_name 带 schema 内容 hash：vLLM 按 name 缓存编译后的 grammar，
            # 若 schema 变了（如加 maxLength）但 name 不变，会命中旧缓存（实测：
            # 加 maxLength 后仍用无 maxLength 的旧 grammar，导致 recommendation
            # 写到 max_tokens 不闭合）。hash 后缀强制 schema 变化时重编译。
            schema_hash = hashlib.md5(
                json.dumps(schema, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:8]
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{schema_name}_{schema_hash}",
                    "schema": schema,
                    "strict": True,
                },
            }
        else:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _parse_and_validate(self, response_json, schema, patient_id):
        try:
            content = response_json["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            _normalize_result(parsed)  # evidence_level 变体归一化（json_object 无 enum 强制）
            jsonschema.validate(parsed, schema)
            # 退化检测（2026-07-03 codex 审查）：finish=length 几乎总是模型
            # 重复生成撞 max_tokens 上限（正常输出 finish=stop）。即便 JSON
            # 恰好合法，length + 高重复度也应判失败，不放过退化输出。
            choice = response_json["choices"][0]
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                comp_tok = response_json.get("usage", {}).get("completion_tokens")
                rep = _repetition_score(content)
                if rep >= 0.5:
                    raise DegenerationError(
                        patient_id, finish_reason, comp_tok, rep, content[:200]
                    )
            return parsed
        except (KeyError, IndexError, TypeError) as e:
            raise SchemaError(f"malformed response: {e}", patient_id) from e
        except json.JSONDecodeError as e:
            # 记录 LLM 原始返回的前 200 字符用于调试
            try:
                preview = response_json["choices"][0]["message"]["content"][:200]
            except Exception:
                preview = "<no content>"
            # 2026-07-02：真实 E2E 验收里 10/10 患者都因 max_tokens 太小
            # （旧值 8192）在生成中途被截断成非法 JSON，报错信息只显示
            # "invalid JSON" 完全看不出是 token 预算问题，排查花了很久。
            # 显式检查 finish_reason == "length" 并把它写进错误信息。
            try:
                finish_reason = response_json["choices"][0].get("finish_reason")
            except Exception:
                finish_reason = None
            truncation_note = (
                " [TRUNCATED: finish_reason=length，很可能是 max_tokens 不够，"
                "不是模型输出真的非法]" if finish_reason == "length" else ""
            )
            raise SchemaError(
                f"invalid JSON: {e} (preview: {preview!r}){truncation_note}", patient_id
            ) from e
        except jsonschema.ValidationError as e:
            raise SchemaError(f"schema violation: {e.message}", patient_id) from e

    async def _post_with_retry(
        self, messages, schema, schema_name, patient_id,
        *,
        max_tokens_override: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        structured_mode_override: Optional[str] = None,
    ):
        payload = self._build_payload(
            messages, schema, schema_name,
            max_tokens_override=max_tokens_override,
            frequency_penalty=frequency_penalty,
            structured_mode_override=structured_mode_override,
        )
        url = f"{self.profile.base_url.rstrip('/')}/chat/completions"
        last_error: Optional[Exception] = None
        for attempt in range(4):
            try:
                resp = await self._http.post(
                    url, json=payload, headers=self._auth_headers,
                    timeout=self.profile.timeout_s,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}",
                        request=resp.request, response=resp,
                    )
                    if attempt < 3:
                        await asyncio.sleep(1.0 * (2 ** attempt))
                        continue
                    break
                if 400 <= resp.status_code < 500:
                    raise LLMFailure(
                        patient_id,
                        httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}",
                            request=resp.request, response=resp,
                        ),
                        stage="transport",
                    )
                return self._parse_and_validate(resp.json(), schema, patient_id)
            except httpx.RequestError as e:
                last_error = e
                if attempt < 3:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                break
        raise LLMFailure(patient_id, last_error or Exception("unknown"), stage="transport")

    async def complete_structured(
        self,
        messages: list[dict],
        schema: dict,
        *,
        schema_name: str = "patient_recommendation",
        patient_id: Optional[str] = None,
        max_tokens_override: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        structured_mode_override: Optional[str] = None,
    ) -> dict:
        async with self._sem:
            kw = dict(
                max_tokens_override=max_tokens_override,
                frequency_penalty=frequency_penalty,
                structured_mode_override=structured_mode_override,
            )
            try:
                return await self._post_with_retry(
                    messages, schema, schema_name, patient_id, **kw,
                )
            except SchemaError:
                try:
                    return await self._post_with_retry(
                        messages, schema, schema_name, patient_id, **kw,
                    )
                except SchemaError as e2:
                    raise LLMFailure(patient_id, e2, stage="schema") from e2

    async def complete_structured_with_feedback(
        self,
        messages: list[dict],
        schema: dict,
        *,
        schema_name: str = "patient_recommendation",
        feedback_check: Callable[[dict], float],
        threshold: float = 0.5,
        feedback_template: Optional[str] = None,
        patient_id: Optional[str] = None,
        max_tokens_override: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        structured_mode_override: Optional[str] = None,
    ) -> tuple[dict, float, str]:
        kw = dict(
            max_tokens_override=max_tokens_override,
            frequency_penalty=frequency_penalty,
            structured_mode_override=structured_mode_override,
        )
        result = await self.complete_structured(
            messages, schema, schema_name=schema_name, patient_id=patient_id, **kw,
        )
        score = feedback_check(result)
        if score >= threshold:
            return result, score, "ok"
        tmpl = feedback_template if feedback_template is not None else _DEFAULT_FEEDBACK_TEMPLATE
        feedback_msg = tmpl.format(prev=score, threshold=threshold)
        augmented = list(messages) + [
            {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
            {"role": "user", "content": feedback_msg},
        ]
        result2 = await self.complete_structured(
            augmented, schema, schema_name=schema_name, patient_id=patient_id, **kw,
        )
        score2 = feedback_check(result2)
        status = "ok" if score2 >= threshold else "partial"
        return result2, score2, status
