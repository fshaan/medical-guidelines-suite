"""Tests for VLM image classification and description."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.extraction.vlm_describer import (
    classify_image,
    describe_image,
    inject_descriptions,
    compute_image_hash,
    load_cache,
    save_cache,
    check_vlm_health,
)


class TestImageHash:
    def test_deterministic(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff test image data")
        h1 = compute_image_hash(img)
        h2 = compute_image_hash(img)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex digest


class TestCache:
    def test_save_and_load(self, tmp_path):
        cache_path = tmp_path / "image_descriptions.json"
        data = {"abc123": {"classification": "流程图", "description": "test"}}
        save_cache(data, cache_path)
        loaded = load_cache(cache_path)
        assert loaded == data

    def test_load_missing_returns_empty(self, tmp_path):
        assert load_cache(tmp_path / "nope.json") == {}


class TestClassifyImage:
    @patch("scripts.extraction.vlm_describer._call_vlm")
    def test_classifies_flowchart(self, mock_call):
        mock_call.return_value = "流程图"
        result = classify_image(Path("test.jpg"), "gemma-4-31b")
        assert result == "流程图"

    @patch("scripts.extraction.vlm_describer._call_vlm")
    def test_classifies_decorative(self, mock_call):
        mock_call.return_value = "装饰性图片"
        result = classify_image(Path("logo.jpg"), "gemma-4-31b")
        assert result == "装饰性图片"

    @patch("scripts.extraction.vlm_describer._call_vlm")
    def test_fallback_to_default(self, mock_call):
        mock_call.return_value = "some unknown response"
        result = classify_image(Path("test.jpg"), "gemma-4-31b")
        assert result == "医学示意图"  # default fallback


class TestDescribeImage:
    @patch("scripts.extraction.vlm_describer._call_vlm")
    def test_returns_description(self, mock_call):
        mock_call.return_value = "NCCN GAST-1 流程图描述..."
        result = describe_image(Path("test.jpg"), "流程图", "gemma-4-31b")
        assert "NCCN" in result

    @patch("scripts.extraction.vlm_describer._call_vlm")
    def test_skips_decorative(self, mock_call):
        result = describe_image(Path("test.jpg"), "装饰性图片", "gemma-4-31b")
        assert result is None
        mock_call.assert_not_called()


class TestInjectDescriptions:
    def test_injects_after_image_ref(self):
        md = "Some text\n\n![](images/abc.jpg)\n\nMore text"
        descriptions = {"images/abc.jpg": "这是一个流程图描述"}
        result = inject_descriptions(md, descriptions)
        assert "> **[图片描述]**" in result
        assert "流程图描述" in result
        assert "More text" in result

    def test_no_description_unchanged(self):
        md = "Some text\n\n![](images/abc.jpg)\n\nMore text"
        result = inject_descriptions(md, {})
        assert result == md

    def test_multiple_images(self):
        md = "![](images/a.jpg)\n\nText\n\n![](images/b.jpg)"
        descriptions = {
            "images/a.jpg": "描述A",
            "images/b.jpg": "描述B",
        }
        result = inject_descriptions(md, descriptions)
        assert "描述A" in result
        assert "描述B" in result

    def test_multiline_description(self):
        md = "![](images/a.jpg)\n\nText"
        descriptions = {"images/a.jpg": "第一行\n第二行\n第三行"}
        result = inject_descriptions(md, descriptions)
        assert "> 第一行" in result
        assert "> 第二行" in result
        assert "> 第三行" in result


class TestVlmHealth:
    @patch("scripts.extraction.vlm_describer.requests")
    def test_healthy(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"id": "gemma-4-31b"}]
        }
        mock_req.get.return_value = mock_resp
        assert check_vlm_health("gemma-4-31b") is True

    @patch("scripts.extraction.vlm_describer.requests")
    def test_model_not_loaded(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"id": "other-model"}]
        }
        mock_req.get.return_value = mock_resp
        assert check_vlm_health("gemma-4-31b") is False

    @patch("scripts.extraction.vlm_describer.requests")
    def test_connection_refused(self, mock_req):
        mock_req.get.side_effect = ConnectionError("refused")
        assert check_vlm_health("gemma-4-31b") is False
