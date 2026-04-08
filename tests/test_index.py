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
