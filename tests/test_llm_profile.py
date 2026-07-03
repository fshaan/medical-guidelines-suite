"""Tests for LLMProfile.from_env + _load_profiles_yaml: env/yaml/default priority."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.llm_client import (
    LLMProfile,
    _DEFAULT_YAML_PATH,
    _load_profiles_yaml,
)


_ALL_LLM_ENV = [
    "LLM_PROFILE", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY_ENV",
    "LLM_TIMEOUT", "LLM_STRUCTURED_MODE", "LLM_CONCURRENCY", "LLM_API_KEY",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ALL_LLM_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def yaml_with_two_profiles(tmp_path):
    p = tmp_path / "llm_profiles.yaml"
    p.write_text(
        "profiles:\n"
        "  qwen3-vllm-lan:\n"
        "    base_url: http://yaml-host:8000/v1\n"
        "    model: yaml-model\n"
        "    api_key_env: YAML_KEY\n"
        "    timeout_s: 120\n"
        "    structured_mode: json_schema\n"
        "    concurrency: 3\n"
        "  partial:\n"
        "    base_url: http://partial/v1\n"
        "    model: partial-model\n",
        encoding="utf-8",
    )
    return p


def test_llm_profile_from_env_all_set(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROFILE", "custom")
    monkeypatch.setenv("LLM_BASE_URL", "http://env-host/v1")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_API_KEY_ENV", "MY_KEY")
    monkeypatch.setenv("LLM_TIMEOUT", "300")
    monkeypatch.setenv("LLM_STRUCTURED_MODE", "json_object")
    monkeypatch.setenv("LLM_CONCURRENCY", "20")
    p = LLMProfile.from_env(yaml_path=tmp_path / "nope.yaml")
    assert p.name == "custom"
    assert p.base_url == "http://env-host/v1"
    assert p.model == "env-model"
    assert p.api_key_env == "MY_KEY"
    assert p.timeout_s == 300
    assert p.structured_mode == "json_object"
    assert p.concurrency == 20


def test_llm_profile_env_overrides_yaml(monkeypatch, yaml_with_two_profiles):
    monkeypatch.setenv("LLM_PROFILE", "qwen3-vllm-lan")
    monkeypatch.setenv("LLM_BASE_URL", "http://env-wins/v1")
    p = LLMProfile.from_env(yaml_path=yaml_with_two_profiles)
    assert p.base_url == "http://env-wins/v1"
    assert p.model == "yaml-model"


def test_llm_profile_from_env_partial_falls_back_to_yaml(monkeypatch, yaml_with_two_profiles):
    monkeypatch.setenv("LLM_PROFILE", "qwen3-vllm-lan")
    p = LLMProfile.from_env(yaml_path=yaml_with_two_profiles)
    assert p.name == "qwen3-vllm-lan"
    assert p.base_url == "http://yaml-host:8000/v1"
    assert p.model == "yaml-model"
    assert p.api_key_env == "YAML_KEY"
    assert p.timeout_s == 120
    assert p.structured_mode == "json_schema"
    assert p.concurrency == 3


def test_llm_profile_yaml_falls_back_to_default(monkeypatch, yaml_with_two_profiles):
    monkeypatch.setenv("LLM_PROFILE", "partial")
    p = LLMProfile.from_env(yaml_path=yaml_with_two_profiles)
    assert p.timeout_s == 180
    assert p.concurrency == 5
    assert p.api_key_env == "LLM_API_KEY"
    assert p.structured_mode == "json_schema"
    assert p.base_url == "http://partial/v1"


def test_load_profiles_yaml_missing_returns_empty(tmp_path):
    assert _load_profiles_yaml(tmp_path / "nope.yaml") == {}


def test_load_profiles_yaml_malformed_returns_empty(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("profiles:\n  - this is not a dict\n: bad: indent", encoding="utf-8")
    result = _load_profiles_yaml(p)
    assert result == {}


def test_load_profiles_yaml_real_file():
    if not _DEFAULT_YAML_PATH.exists():
        pytest.skip("config/llm_profiles.yaml not present in working tree")
    d = _load_profiles_yaml(_DEFAULT_YAML_PATH)
    assert set(d.keys()) == {"qwen3-vllm-lan", "deepseek-cloud"}
    assert d["qwen3-vllm-lan"]["model"] == "qwen3.6-35b"
    assert d["deepseek-cloud"]["api_key_env"] == "DEEPSEEK_API_KEY"


def test_llm_timeout_invalid_raises(monkeypatch, yaml_with_two_profiles):
    monkeypatch.setenv("LLM_PROFILE", "qwen3-vllm-lan")
    monkeypatch.setenv("LLM_TIMEOUT", "abc")
    with pytest.raises(ValueError):
        LLMProfile.from_env(yaml_path=yaml_with_two_profiles)


def test_llm_concurrency_invalid_raises(monkeypatch, yaml_with_two_profiles):
    monkeypatch.setenv("LLM_PROFILE", "qwen3-vllm-lan")
    monkeypatch.setenv("LLM_CONCURRENCY", "xyz")
    with pytest.raises(ValueError):
        LLMProfile.from_env(yaml_path=yaml_with_two_profiles)


def test_llm_concurrency_zero_rejected_by_post_init():
    with pytest.raises(ValueError, match="concurrency must be >= 1"):
        LLMProfile(name="t", base_url="http://x/v1", model="m", concurrency=0)


def test_llm_timeout_zero_rejected_by_post_init():
    with pytest.raises(ValueError, match="timeout_s must be >= 1"):
        LLMProfile(name="t", base_url="http://x/v1", model="m", timeout_s=0)


def test_llm_profile_required_field_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROFILE", "nonexistent")
    empty = tmp_path / "empty.yaml"
    empty.write_text("profiles: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="base_url is required"):
        LLMProfile.from_env(yaml_path=empty)


def test_api_key_not_in_profile_repr(monkeypatch, yaml_with_two_profiles):
    monkeypatch.setenv("LLM_PROFILE", "qwen3-vllm-lan")
    monkeypatch.setenv("LLM_API_KEY", "sk-leak-test")
    p = LLMProfile.from_env(yaml_path=yaml_with_two_profiles)
    assert "sk-leak-test" not in repr(p)
    assert p.api_key_env == "YAML_KEY"
