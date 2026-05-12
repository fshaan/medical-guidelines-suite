"""Tests for batch_pipeline._extract_org_from_hit_path.

codex P1 修复回归：原 split("/", 1)[0] 在 qmd://NCCN/file.md 上返回 "qmd:"，
导致 cmd_orchestrate 把所有 qmd:// 命中误判为 off-topic 全部过滤掉。
"""

from __future__ import annotations

import pytest

from scripts.batch_pipeline import _extract_org_from_hit_path


def test_extract_org_from_qmd_url():
    """qmd:// URL → 取 host 段为 org 名。"""
    assert _extract_org_from_hit_path("qmd://NCCN/file.md") == "NCCN"
    assert _extract_org_from_hit_path("qmd://ESMO/guideline-2026.md") == "ESMO"


def test_extract_org_from_qmd_url_lowercase():
    """qmd:// URL with lowercase org（chunks.json sidecar 风格）。"""
    assert _extract_org_from_hit_path("qmd://nccn/file.md") == "nccn"


def test_extract_org_from_qmd_url_with_nested_path():
    """qmd:// URL 内嵌多级路径 → 仍取第一段。"""
    assert _extract_org_from_hit_path("qmd://CSCO/2026/colorectal.md") == "CSCO"


def test_extract_org_from_qmd_url_host_only():
    """qmd:// URL 仅有 host 没有 path → host 作为 org（边缘场景）。"""
    assert _extract_org_from_hit_path("qmd://NCCN") == "NCCN"


def test_extract_org_from_bare_path():
    """裸路径 ORG/file.md → 向后兼容。"""
    assert _extract_org_from_hit_path("NCCN/file.md") == "NCCN"


def test_extract_org_from_empty_path():
    """空字符串 → 空 org。"""
    assert _extract_org_from_hit_path("") == ""


def test_extract_org_from_path_without_slash():
    """单 token 无分隔符 → 空 org（视为不可解析）。"""
    assert _extract_org_from_hit_path("standalone") == ""


def test_qmd_url_not_misread_as_qmd_colon():
    """codex P1 回归：原 split bug 在 qmd:// 上会返回 'qmd:'，必须不再出现。"""
    org = _extract_org_from_hit_path("qmd://NCCN/file.md")
    assert org != "qmd:", "qmd:// URL 被错误解析为 'qmd:' 前缀"
    assert org == "NCCN"
