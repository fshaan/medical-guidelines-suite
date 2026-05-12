"""Async LLM client with strict JSON Schema output and multi-path retry.

Provides AsyncLLMClient for OpenAI-compatible structured output via
response_format={"type":"json_schema","strict":true}. Includes three
independent retry paths (transport / schema / feedback) and the
PATIENT_RECOMMENDATION_SCHEMA with 23-item evidence_level enum.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Callable

import httpx
import jsonschema
import yaml
from pathlib import Path


class LLMFailure(Exception):
    """LLM call failed after all retries exhausted."""

    def __init__(self, patient_id: str | None, last_error: Exception, stage: str):
        self.patient_id = patient_id
        self.last_error = last_error
        self.stage = stage
        super().__init__(
            f"LLM call failed at stage={stage} for patient={patient_id}: {last_error}"
        )


class SchemaError(Exception):
    """Internal control-flow exception for schema validation failures."""

    def __init__(self, message: str, patient_id: str | None = None):
        super().__init__(message)
        self.patient_id = patient_id


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
                "required": [
                    "guideline", "guideline_version", "recommendation",
                    "evidence_level", "source_file", "retrieval_sources",
                ],
            },
        },
        "consensus": {"type": "array", "items": {"type": "string"}},
        "differences": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["guideline_results", "consensus", "differences"],
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
    max_tokens: int = 4096
    temperature: float = 0.1
    timeout_s: int = 180
    structured_mode: str = "json_schema"
    concurrency: int = 5

    @classmethod
    def from_env(
        cls,
        name: str | None = None,
        *,
        yaml_path: "str | os.PathLike | None" = None,
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
        )


_DEFAULT_YAML_PATH = Path("config/llm_profiles.yaml")

_ENV_PROFILE = "LLM_PROFILE"
_ENV_BASE_URL = "LLM_BASE_URL"
_ENV_MODEL = "LLM_MODEL"
_ENV_API_KEY_ENV = "LLM_API_KEY_ENV"
_ENV_TIMEOUT = "LLM_TIMEOUT"
_ENV_STRUCTURED_MODE = "LLM_STRUCTURED_MODE"
_ENV_CONCURRENCY = "LLM_CONCURRENCY"

_DEFAULT_PROFILE_NAME = "qwen3-vllm-lan"


def _load_profiles_yaml(
    path: "str | os.PathLike | None" = None,
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
        semaphore: asyncio.Semaphore | None = None,
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

    def _build_payload(self, messages, schema, schema_name):
        payload = {
            "model": self.profile.model,
            "messages": messages,
            "temperature": self.profile.temperature,
            "max_tokens": self.profile.max_tokens,
        }
        if self.profile.structured_mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
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
            jsonschema.validate(parsed, schema)
            return parsed
        except (KeyError, IndexError, TypeError) as e:
            raise SchemaError(f"malformed response: {e}", patient_id) from e
        except json.JSONDecodeError as e:
            raise SchemaError(f"invalid JSON: {e}", patient_id) from e
        except jsonschema.ValidationError as e:
            raise SchemaError(f"schema violation: {e.message}", patient_id) from e

    async def _post_with_retry(self, messages, schema, schema_name, patient_id):
        payload = self._build_payload(messages, schema, schema_name)
        url = f"{self.profile.base_url.rstrip('/')}/chat/completions"
        last_error: Exception | None = None
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
                resp.raise_for_status()
                return self._parse_and_validate(resp.json(), schema, patient_id)
            except httpx.TimeoutException as e:
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
        patient_id: str | None = None,
    ) -> dict:
        async with self._sem:
            try:
                return await self._post_with_retry(
                    messages, schema, schema_name, patient_id,
                )
            except SchemaError:
                try:
                    return await self._post_with_retry(
                        messages, schema, schema_name, patient_id,
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
        feedback_template: str | None = None,
        patient_id: str | None = None,
    ) -> tuple[dict, float, str]:
        result = await self.complete_structured(
            messages, schema, schema_name=schema_name, patient_id=patient_id,
        )
        score = feedback_check(result)
        if score >= threshold:
            return result, score, "ok"
        tmpl = feedback_template if feedback_template is not None else _DEFAULT_FEEDBACK_TEMPLATE
        feedback_msg = tmpl.format(prev=score, threshold=threshold)
        augmented = list(messages) + [{"role": "user", "content": feedback_msg}]
        result2 = await self.complete_structured(
            augmented, schema, schema_name=schema_name, patient_id=patient_id,
        )
        score2 = feedback_check(result2)
        status = "ok" if score2 >= threshold else "partial"
        return result2, score2, status
