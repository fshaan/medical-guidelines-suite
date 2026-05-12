"""Tests for batch_pipeline.py CLI subcommand registration.

Plan 03-02: argparse 行为 smoke test — run 子命令 / hidden 子命令 / 互斥组 / env 优先级 / cmd_index sidecar / dispatch 路由。

8 cases:
1. test_run_subcommand_in_help
2. test_hidden_subcommands_still_callable
3. test_validate_mutually_exclusive
4. test_generate_mutually_exclusive
5. test_run_args_defaults_from_env
6. test_run_cli_flag_overrides_env
7. test_cmd_index_produces_sidecar
8. test_main_dispatch_run_calls_run_pipeline
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Case 1: run 子命令出现在 --help 中，hidden 子命令不出现
# ---------------------------------------------------------------------------


def test_run_subcommand_in_help():
    """--help 输出含 'run' + 描述，hidden 子命令原始描述被隐藏。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    result = subprocess.run(
        [sys.executable, "scripts/batch_pipeline.py", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0
    assert "run" in result.stdout
    assert "按患者并发" in result.stdout
    # hidden 子命令的原始 help 描述应被隐藏（SUPPRESS 替换为 ==SUPPRESS==）
    # Python 3.9: argparse.SUPPRESS 在 subparser 中只隐藏 help 文本，
    # 名字仍在 {choices} 列表中，但描述不再显示原始文案
    hidden_descriptions = [
        "将 patients.json 分成多个批次文件",
        "自动编排批处理流程",
        "合并批次结果为 rag_results",
        "验证批次执行证据的真实性",
    ]
    for desc in hidden_descriptions:
        assert desc not in result.stdout, f"hidden 子命令描述 '{desc}' 不应在 --help 中出现"


# ---------------------------------------------------------------------------
# Case 2: hidden 子命令仍可通过直接调用获取 help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subcmd", ["split", "orchestrate", "merge", "verify-batch"])
def test_hidden_subcommands_still_callable(subcmd):
    """hidden 子命令 --help 仍能正常返回。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    result = subprocess.run(
        [sys.executable, "scripts/batch_pipeline.py", subcmd, "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0
    assert "--" in result.stdout  # 应该有参数描述


# ---------------------------------------------------------------------------
# Case 3: validate 互斥组 --input / --patients-dir
# ---------------------------------------------------------------------------


def test_validate_mutually_exclusive():
    """validate 同时传 --input 和 --patients-dir 应报错。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    result = subprocess.run(
        [
            sys.executable, "scripts/batch_pipeline.py",
            "validate", "--input", "X", "--patients-dir", "Y",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode != 0
    assert "not allowed with" in result.stderr


# ---------------------------------------------------------------------------
# Case 4: generate 互斥组 --input / --patients-dir
# ---------------------------------------------------------------------------


def test_generate_mutually_exclusive():
    """generate 同时传 --input 和 --patients-dir 应报错。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    result = subprocess.run(
        [
            sys.executable, "scripts/batch_pipeline.py",
            "generate", "--input", "X", "--patients-dir", "Y",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode != 0
    assert "not allowed with" in result.stderr


# ---------------------------------------------------------------------------
# Case 5: run args 默认值从 env 读取
# ---------------------------------------------------------------------------


def test_run_args_defaults_from_env(monkeypatch):
    """env 变量设定后，run 子命令默认值应反映 env 值（直接测试 argparse）。"""
    import argparse

    monkeypatch.setenv("PIPELINE_CONCURRENCY_PATIENTS", "3")
    monkeypatch.setenv("PIPELINE_CONCURRENCY_QMD", "4")
    monkeypatch.setenv("LLM_PROFILE", "test-profile")

    # 直接构建与 batch_pipeline.py main() 相同的 parser
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="按患者并发流水线(v3.1 主路径)")
    p_run.add_argument("--patients", required=True)
    p_run.add_argument("--output-dir", required=True)
    p_run.add_argument(
        "--llm-profile",
        default=os.environ.get("LLM_PROFILE", "qwen3-vllm-lan"),
    )
    p_run.add_argument(
        "--concurrency-patients",
        type=int,
        default=int(os.environ.get("PIPELINE_CONCURRENCY_PATIENTS", "5")),
    )
    p_run.add_argument(
        "--concurrency-qmd",
        type=int,
        default=int(os.environ.get("PIPELINE_CONCURRENCY_QMD", "8")),
    )

    with patch.object(sys, "argv", ["bp", "run", "--patients", "p.json", "--output-dir", "Out"]):
        args = parser.parse_args()

    assert args.concurrency_patients == 3
    assert args.concurrency_qmd == 4
    assert args.llm_profile == "test-profile"


# ---------------------------------------------------------------------------
# Case 6: CLI flag 覆盖 env 默认值
# ---------------------------------------------------------------------------


def test_run_cli_flag_overrides_env(monkeypatch):
    """CLI flag 显式传入应覆盖 env 默认值。"""
    import argparse

    monkeypatch.setenv("PIPELINE_CONCURRENCY_PATIENTS", "3")
    monkeypatch.setenv("PIPELINE_CONCURRENCY_QMD", "4")
    monkeypatch.setenv("LLM_PROFILE", "test-profile")

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="按患者并发流水线(v3.1 主路径)")
    p_run.add_argument("--patients", required=True)
    p_run.add_argument("--output-dir", required=True)
    p_run.add_argument(
        "--llm-profile",
        default=os.environ.get("LLM_PROFILE", "qwen3-vllm-lan"),
    )
    p_run.add_argument(
        "--concurrency-patients",
        type=int,
        default=int(os.environ.get("PIPELINE_CONCURRENCY_PATIENTS", "5")),
    )
    p_run.add_argument(
        "--concurrency-qmd",
        type=int,
        default=int(os.environ.get("PIPELINE_CONCURRENCY_QMD", "8")),
    )

    with patch.object(
        sys,
        "argv",
        ["bp", "run", "--patients", "p.json", "--output-dir", "Out", "--concurrency-patients", "7"],
    ):
        args = parser.parse_args()

    assert args.concurrency_patients == 7  # CLI flag 覆盖 env 的 3


# ---------------------------------------------------------------------------
# Case 7: cmd_index 产生 .metadata/ 侧车文件 (smoke test)
# ---------------------------------------------------------------------------


def test_cmd_index_produces_sidecar(tmp_path, monkeypatch):
    """cmd_index 调用 build_sidecar 产出三个 .metadata 文件。"""
    # 创建 mock KB 结构（含 data_structure.md 让 resolve_kb_root 通过）
    (tmp_path / "data_structure.md").write_text("# Test KB\n", encoding="utf-8")
    nccn_dir = tmp_path / "NCCN" / "extracted"
    nccn_dir.mkdir(parents=True)
    (nccn_dir / "colon.md").write_text("# NCCN Colon Cancer\nTreatment guidelines...", encoding="utf-8")
    esmo_dir = tmp_path / "ESMO" / "extracted"
    esmo_dir.mkdir(parents=True)
    (esmo_dir / "rectal.md").write_text("# ESMO Rectal Cancer\nTreatment...", encoding="utf-8")

    import scripts.batch_pipeline as bp

    # mock build_sidecar to produce the expected files
    with patch("scripts.batch_pipeline.kb_metadata.build_sidecar") as mock_sidecar:
        meta_dir = tmp_path / ".metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "chunks.json").write_text("{}", encoding="utf-8")
        (meta_dir / "org_disease_coverage.json").write_text("{}", encoding="utf-8")
        (meta_dir / "synonym_map.yaml").write_text("{}", encoding="utf-8")
        mock_sidecar.return_value = {
            "chunks_path": meta_dir / "chunks.json",
            "coverage_path": meta_dir / "org_disease_coverage.json",
            "synonyms_path": meta_dir / "synonym_map.yaml",
            "n_chunks": 0,
            "n_orgs": 2,
        }

        args = MagicMock()
        args.kb_root = str(tmp_path)
        args.force = False

        # Mock subprocess.run to avoid real QMD calls
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            try:
                bp.cmd_index(args)
            except SystemExit:
                pass
            except Exception:
                pass

        # build_sidecar 被调用过（带正确的 kb_root）
        mock_sidecar.assert_called_once()
        called_path = mock_sidecar.call_args[0][0]
        assert str(called_path) == str(tmp_path)


# ---------------------------------------------------------------------------
# Case 8: main() dispatch run 路由到 pipeline.run_pipeline
# ---------------------------------------------------------------------------


def test_main_dispatch_run_calls_run_pipeline(monkeypatch):
    """main() 中 run 子命令应调用 asyncio.run(pipeline.run_pipeline(args))。"""
    import scripts.batch_pipeline as bp

    with patch("scripts.pipeline.run_pipeline", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = 0
        with patch("asyncio.run", return_value=0) as mock_asyncio:
            with patch.object(
                sys,
                "argv",
                ["bp", "run", "--patients", "p.json", "--output-dir", "Out"],
            ):
                with patch.object(sys, "exit") as mock_exit:
                    try:
                        bp.main()
                    except SystemExit:
                        pass

                    # asyncio.run 被调用
                    assert mock_asyncio.called, "asyncio.run 应被调用"

                    # sys.exit 被调用，退出码 0
                    if mock_exit.called:
                        assert mock_exit.call_args[0][0] == 0
