"""Tests for the index subcommand (QMD collection + context setup)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def kb_with_md(tmp_path):
    """KB with extracted .md files and data_structure.md."""
    nccn = tmp_path / "NCCN"
    nccn.mkdir()
    (nccn / "extracted").mkdir()
    (nccn / "extracted" / "NCCN_Gastric_2026.V2_EN.md").write_text(
        "# Gastric Cancer\n", encoding="utf-8"
    )
    (nccn / "data_structure.md").write_text(
        "# NCCN Gastric Cancer Guidelines\n", encoding="utf-8",
    )

    (tmp_path / "data_structure.md").write_text("# KB Root\n", encoding="utf-8")

    csco = tmp_path / "CSCO"
    csco.mkdir()
    (csco / "extracted").mkdir()
    (csco / "extracted" / "CSCO_Gastric_2026.md").write_text(
        "# Gastric\n", encoding="utf-8"
    )
    (csco / "data_structure.md").write_text(
        "# CSCO Gastric Guidelines\n", encoding="utf-8",
    )
    return tmp_path


@patch("scripts.batch_pipeline.subprocess.run")
def test_cmd_index_creates_collections_and_contexts(mock_run, kb_with_md):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    from scripts.batch_pipeline import cmd_index

    args = MagicMock()
    args.kb_root = str(kb_with_md)
    args.force = False

    cmd_index(args)

    call_args_list = [c.args[0] for c in mock_run.call_args_list]
    collection_calls = [
        c for c in call_args_list if "collection" in c and "add" in c
    ]
    assert len(collection_calls) == 2

    embed_calls = [c for c in call_args_list if "embed" in c]
    assert len(embed_calls) >= 1

    context_calls = [
        c for c in call_args_list if "context" in c and "add" in c
    ]
    assert len(context_calls) >= 2


@patch("scripts.batch_pipeline.subprocess.run")
def test_cmd_index_invokes_build_sidecar_and_writes_metadata(mock_run, kb_with_md):
    """KBM-01 + KBM-02 entrypoint integration: cmd_index 必须真正触发
    build_sidecar，把 chunks.json / org_disease_coverage.json / synonym_map.yaml
    三件套写入 <kb_root>/.metadata/。"""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    from scripts.batch_pipeline import cmd_index

    args = MagicMock()
    args.kb_root = str(kb_with_md)
    args.force = False

    cmd_index(args)

    meta_dir = kb_with_md / ".metadata"
    assert (meta_dir / "chunks.json").exists(), \
        "build_sidecar 未生成 chunks.json — cmd_index 没调用 build_sidecar"
    assert (meta_dir / "org_disease_coverage.json").exists(), \
        "build_sidecar 未生成 org_disease_coverage.json"
    assert (meta_dir / "synonym_map.yaml").exists(), \
        "seed_synonym_map 未生成 synonym_map.yaml"


@patch("scripts.batch_pipeline.kb_metadata.build_sidecar")
@patch("scripts.batch_pipeline.subprocess.run")
def test_cmd_index_continues_when_build_sidecar_fails(
    mock_run, mock_build_sidecar, kb_with_md, capsys
):
    """KBM-01/02 reliability: build_sidecar 失败时 cmd_index 必须 warn-only，
    不能 abort（QMD 索引已经完成）。与 Metal 134 容忍精神对齐。"""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    mock_build_sidecar.side_effect = RuntimeError("simulated sidecar failure")

    from scripts.batch_pipeline import cmd_index

    args = MagicMock()
    args.kb_root = str(kb_with_md)
    args.force = False

    # 不应该 raise（warn-only 策略）
    cmd_index(args)

    # build_sidecar 必须被调用过（证明 cmd_index 走到了 sidecar 路径）
    assert mock_build_sidecar.called, \
        "cmd_index 没尝试调用 build_sidecar，无法验证 warn-only 行为"

    # stderr 出现 sidecar / metadata 警告
    captured = capsys.readouterr()
    stderr_lower = captured.err.lower()
    assert "sidecar" in stderr_lower or "metadata" in stderr_lower, \
        f"未在 stderr 看到 sidecar/metadata 警告; stderr={captured.err!r}"
