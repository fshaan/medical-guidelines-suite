# QMD 混合检索引擎集成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace grep-based keyword retrieval with QMD hybrid search (BM25 + vector + LLM reranking) and refactor document extraction from pdftotext/python-docx to Docling, producing Markdown instead of plain text.

**Architecture:** Three-layer implementation — Layer 1 rewrites extraction (Docling to `*.md`), Layer 2 adds QMD retrieval service (`retriever.py` + `index` subcommand), Layer 3 adapts the batch pipeline (orchestrate pre-retrieval, prompt rewrite, anti-laziness V3 refactor, verify-batch). All in one PR on `feature/qmd-retrieval` branch.

**Tech Stack:** Python 3.12+, Docling (IBM), QMD (`@tobilu/qmd` npm), Qwen3-Embedding, pytest

**Spec:** `docs/superpowers/specs/2026-04-08-qmd-retrieval-design.md`

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `scripts/retriever.py` | QMDService context manager + query/search API |
| `tests/test_extract.py` | Docling extraction tests |
| `tests/test_retriever.py` | QMDService lifecycle + query tests |
| `tests/test_index.py` | index subcommand tests |
| `tests/test_qmd_integration.py` | Integration tests (@slow, QMD_AVAILABLE gate) |

### Modified files
| File | Changes |
|------|---------|
| `scripts/extract_all.py` | Rewrite: Docling to `*.md` |
| `scripts/batch_pipeline.py` | scan_knowledge_base glob, delete grep functions, rewrite orchestrate/prompt/verify, new index subcommand, anti-laziness V3 |
| `tests/test_orchestrate.py` | Adapt to pre-retrieval + new prompt |
| `tests/test_prompt.py` | Adapt to new prompt structure |
| `tests/test_verify_batch.py` | Adapt to citation coverage |
| `tests/test_validate_enhance.py` | Adapt JSON field names |
| `tests/test_slim_profile.py` | Adapt to removed grep references |
| `SKILL.md` | Query flow: grep to QMD MCP |
| `CLAUDE.md` | Dependencies, architecture, commands |
| `README.md` | Sync |
| `skill.json` | requires.bins + requires.pip |

### Deleted files
| File | Reason |
|------|--------|
| `scripts/extract_pdf.py` | Replaced by Docling in extract_all.py |
| `scripts/extract_docx.py` | Replaced by Docling in extract_all.py |
| `tests/test_grep.py` | grep code deleted |

---

## Layer 1: Extraction Refactor

### Task 1: Rewrite extract_all.py with Docling

**Files:**
- Rewrite: `scripts/extract_all.py`
- Test: `tests/test_extract.py`

- [ ] **Step 1: Write failing test for single file extraction**

Create `tests/test_extract.py`:

```python
"""Tests for Docling-based document extraction."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_kb(tmp_path):
    """Create a minimal KB directory structure."""
    org_dir = tmp_path / "NCCN"
    org_dir.mkdir()
    (org_dir / "NCCN_Gastric_2026.pdf").write_bytes(b"%PDF-1.4 dummy")
    return tmp_path


def _mock_converter():
    """Build a mock DocumentConverter that returns fixed markdown."""
    mock_doc = MagicMock()
    mock_doc.document.export_to_markdown.return_value = (
        "# Gastric Cancer\n\n## Treatment\n\nChemotherapy recommended.\n"
    )
    converter = MagicMock()
    converter.convert.return_value = mock_doc
    return converter


@patch("scripts.extract_all.DocumentConverter")
def test_extract_file_creates_md(mock_cls, tmp_kb):
    mock_cls.return_value = _mock_converter()

    from scripts.extract_all import extract_file

    org_dir = tmp_kb / "NCCN"
    extracted_dir = org_dir / "extracted"
    extracted_dir.mkdir()

    result = extract_file(org_dir / "NCCN_Gastric_2026.pdf", extracted_dir)

    assert result.name == "NCCN_Gastric_2026.md"
    assert result.exists()
    content = result.read_text(encoding="utf-8")
    assert "# Gastric Cancer" in content
    assert "Chemotherapy" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_extract.py::test_extract_file_creates_md -v`
Expected: FAIL (module `scripts.extract_all` has no `extract_file` matching new signature)

- [ ] **Step 3: Implement extract_all.py**

Rewrite `scripts/extract_all.py` (replace entire file):

```python
#!/usr/bin/env python3
"""
批量提取所有指南文档（PDF/DOCX -> Markdown）
使用 Docling 统一提取，输出到 extracted/*.md
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

from docling.document_converter import DocumentConverter


def extract_file(source_path: Path, output_dir: Path) -> Path:
    """将单个 PDF/DOCX 转换为 Markdown。

    Args:
        source_path: 源文件路径（PDF 或 DOCX）
        output_dir: 输出目录（通常是 extracted/）

    Returns:
        输出的 .md 文件路径
    """
    converter = DocumentConverter()
    result = converter.convert(source_path)
    md_content = result.document.export_to_markdown()
    out_path = output_dir / f"{source_path.stem}.md"
    out_path.write_text(md_content, encoding="utf-8")
    return out_path


def extract_all(kb_root: Path, force: bool = False) -> dict:
    """遍历 KB 下所有 org 目录，提取 PDF/DOCX -> extracted/*.md。

    Args:
        kb_root: 知识库根目录
        force: 是否强制重新提取

    Returns:
        {"success": int, "skipped": int, "failed": int}
    """
    stats = {"success": 0, "skipped": 0, "failed": 0}

    for org_dir in sorted(kb_root.iterdir()):
        if not org_dir.is_dir() or org_dir.name.startswith("."):
            continue
        if org_dir.name in ("scripts", "extracted"):
            continue

        extracted_dir = org_dir / "extracted"
        extracted_dir.mkdir(exist_ok=True)

        sources = itertools.chain(
            org_dir.glob("*.[pP][dD][fF]"),
            org_dir.glob("*.[dD][oO][cC][xX]"),
        )
        for src in sorted(sources):
            out = extracted_dir / f"{src.stem}.md"
            if out.exists() and not force:
                print(f"  skip {src.name}: already exists")
                stats["skipped"] += 1
                continue
            try:
                extract_file(src, extracted_dir)
                print(f"  done {src.name}")
                stats["success"] += 1
            except Exception as e:
                print(f"  FAIL {src.name}: {e}", file=sys.stderr)
                stats["failed"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="批量提取指南文档（PDF/DOCX -> Markdown）"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新提取（覆盖已有文件）",
    )
    parser.add_argument(
        "--guidelines-dir",
        type=Path,
        default=Path("guidelines"),
        help="指南根目录（默认为 guidelines/）",
    )
    args = parser.parse_args()

    guidelines_dir = args.guidelines_dir.resolve()
    if not guidelines_dir.exists():
        print(f"指南目录不存在: {guidelines_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"知识库: {guidelines_dir}")
    stats = extract_all(guidelines_dir, force=args.force)
    print(
        f"\n统计: 成功 {stats['success']}, "
        f"跳过 {stats['skipped']}, 失败 {stats['failed']}"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_extract.py::test_extract_file_creates_md -v`
Expected: PASS

- [ ] **Step 5: Write test for incremental skip**

Add to `tests/test_extract.py`:

```python
@patch("scripts.extract_all.DocumentConverter")
def test_extract_all_skips_existing(mock_cls, tmp_kb):
    mock_cls.return_value = _mock_converter()

    from scripts.extract_all import extract_all

    stats1 = extract_all(tmp_kb, force=False)
    assert stats1["success"] == 1

    stats2 = extract_all(tmp_kb, force=False)
    assert stats2["skipped"] == 1
    assert stats2["success"] == 0


@patch("scripts.extract_all.DocumentConverter")
def test_extract_all_force_reextracts(mock_cls, tmp_kb):
    mock_cls.return_value = _mock_converter()

    from scripts.extract_all import extract_all

    extract_all(tmp_kb, force=False)
    stats = extract_all(tmp_kb, force=True)
    assert stats["success"] == 1
    assert stats["skipped"] == 0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_extract.py -v`
Expected: All 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/extract_all.py tests/test_extract.py
git commit -m "feat: rewrite extract_all with Docling (PDF/DOCX -> Markdown)"
```

---

### Task 2: Delete old extraction scripts

**Files:**
- Delete: `scripts/extract_pdf.py`
- Delete: `scripts/extract_docx.py`

- [ ] **Step 1: Verify no imports of old scripts**

Run: `grep -r "extract_pdf\|extract_docx" scripts/ tests/ --include="*.py" -l`
Expected: No results (or only references in files we are about to delete/update)

- [ ] **Step 2: Delete scripts**

```bash
git rm scripts/extract_pdf.py scripts/extract_docx.py
```

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor: remove extract_pdf.py and extract_docx.py (replaced by Docling)"
```

---

### Task 3: Update scan_knowledge_base glob pattern

**Files:**
- Modify: `scripts/batch_pipeline.py:336` (glob `*.txt` to `*.md`)
- Modify: `tests/test_scan_kb.py` (adapt fixtures)

- [ ] **Step 1: Write failing test**

Add to `tests/test_scan_kb.py`:

```python
def test_scan_kb_finds_md_files(tmp_path):
    """scan_knowledge_base should find *.md files in extracted/ dirs."""
    org = tmp_path / "NCCN" / "extracted"
    org.mkdir(parents=True)
    (org / "NCCN_Gastric.md").write_text("# Gastric Cancer\n", encoding="utf-8")

    from scripts.batch_pipeline import scan_knowledge_base

    result = scan_knowledge_base(tmp_path)
    assert "NCCN" in result["orgs"]
    files = result["org_files"]["NCCN"]
    assert len(files) == 1
    assert files[0]["file"] == "NCCN_Gastric.md"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scan_kb.py::test_scan_kb_finds_md_files -v`
Expected: FAIL — scan_knowledge_base looks for `*.txt`, finds nothing

- [ ] **Step 3: Update scan_knowledge_base**

In `scripts/batch_pipeline.py`, change line 336:

```python
# Old:
txt_files = sorted(extracted_dir.glob("*.txt"))
# New:
txt_files = sorted(extracted_dir.glob("*.md"))
```

Also update the warning message on line 338:

```python
# Old:
print(f"  warning {org}/extracted/ no .txt files, skip", file=sys.stderr)
# New:
print(f"  warning {org}/extracted/ no .md files, skip", file=sys.stderr)
```

- [ ] **Step 4: Run new test to verify it passes**

Run: `python -m pytest tests/test_scan_kb.py::test_scan_kb_finds_md_files -v`
Expected: PASS

- [ ] **Step 5: Update existing test fixtures to use .md**

In `tests/test_scan_kb.py`, change all occurrences of `.txt` file creation to `.md`. Apply the same pattern to all test fixtures across files that create fake KB structures: `tests/conftest.py`, `tests/test_orchestrate.py`, `tests/test_verify_batch.py`, `tests/test_slim_profile.py`.

Search for all occurrences:

```bash
grep -rn '\.txt.*write_text\|\.txt.*touch\|extracted.*\.txt' tests/ --include="*.py"
```

Update each occurrence from `.txt` to `.md`.

- [ ] **Step 6: Run full test suite to check for breakage**

Run: `python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: No failures related to `.txt` to `.md` migration. Some tests may fail for other reasons (grep functions not yet removed) — that is expected and will be fixed in Layer 3.

- [ ] **Step 7: Commit**

```bash
git add scripts/batch_pipeline.py tests/
git commit -m "refactor: scan_knowledge_base glob *.txt to *.md for Docling output"
```

---

## Layer 2: Retrieval Layer

### Task 4: QMDService context manager

**Files:**
- Create: `scripts/retriever.py`
- Test: `tests/test_retriever.py`

- [ ] **Step 1: Write failing test for QMDService lifecycle**

Create `tests/test_retriever.py`:

```python
"""Tests for QMDService context manager and query API."""

import json
from unittest.mock import MagicMock, patch

import pytest


@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.requests.post")
def test_qmd_service_lifecycle(mock_post, mock_popen):
    """QMDService starts qmd process on enter, kills on exit."""
    proc = MagicMock()
    proc.poll.return_value = None  # process is running
    proc.pid = 12345
    mock_popen.return_value = proc

    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"result": "ok"})

    from scripts.retriever import QMDService

    with QMDService(port=9999) as svc:
        assert svc.process is not None
        assert svc.port == 9999

    proc.terminate.assert_called_once()


@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.requests.post")
def test_qmd_service_query_returns_structured_results(mock_post, mock_popen):
    """query() returns list of dicts with content, path, score, context."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 12345
    mock_popen.return_value = proc

    health_resp = MagicMock(status_code=200, json=lambda: {"result": "ok"})
    query_resp = MagicMock(status_code=200, json=lambda: {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps([
                        {
                            "content": "Chemotherapy is recommended for stage IV.",
                            "path": "NCCN/extracted/NCCN_Gastric_2026.md",
                            "score": 0.85,
                            "context": "NCCN gastric guidelines 2026 V2",
                        }
                    ]),
                }
            ]
        }
    })
    mock_post.side_effect = [health_resp, query_resp]

    from scripts.retriever import QMDService

    with QMDService(port=9999) as svc:
        results = svc.query("gastric cancer stage IV treatment")

    assert len(results) == 1
    assert results[0]["content"] == "Chemotherapy is recommended for stage IV."
    assert results[0]["score"] == 0.85
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_retriever.py -v`
Expected: FAIL (no module `scripts.retriever`)

- [ ] **Step 3: Implement retriever.py**

Create `scripts/retriever.py`:

```python
"""QMD hybrid retrieval engine service wrapper.

Provides QMDService context manager that manages the QMD HTTP MCP Server
lifecycle. Exposes query() and search() methods.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time

import requests


class QMDStartupError(Exception):
    """QMD service failed to start."""


class QMDService:
    """Manage QMD HTTP MCP Server lifecycle.

    Usage:
        with QMDService(port=8181) as qmd:
            results = qmd.query("gastric cancer treatment")
    """

    def __init__(self, port: int | None = None):
        self.port = port or int(os.environ.get("QMD_PORT", "8181"))
        self.process: subprocess.Popen | None = None
        self.base_url = f"http://localhost:{self.port}/mcp"

    def __enter__(self) -> "QMDService":
        self._check_port_available()
        self.process = subprocess.Popen(
            ["qmd", "mcp", "--http", "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._wait_for_ready(timeout=30)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.process is None:
            return
        if self.process.poll() is not None:
            self.process = None
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        self.process = None

    def query(
        self, text: str, top_k: int = 10, min_score: float = 0.3
    ) -> list[dict]:
        """Hybrid query (BM25 + vector + LLM reranking).

        Returns:
            [{"content": str, "path": str, "score": float, "context": str}]
        """
        resp = requests.post(
            self.base_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "query",
                    "arguments": {
                        "query": text,
                        "limit": top_k,
                        "min_score": min_score,
                    },
                },
            },
            timeout=60,
        )
        resp.raise_for_status()
        return self._parse_mcp_response(resp.json())

    def search(self, text: str, top_k: int = 10) -> list[dict]:
        """Pure BM25 search (no LLM, faster).

        Returns:
            [{"content": str, "path": str, "score": float, "context": str}]
        """
        resp = requests.post(
            self.base_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {"query": text, "limit": top_k},
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        return self._parse_mcp_response(resp.json())

    def _check_port_available(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", self.port)) == 0:
                raise QMDStartupError(
                    f"Port {self.port} already in use. "
                    f"Kill the existing process or set QMD_PORT env var."
                )

    def _wait_for_ready(self, timeout: int = 30) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stderr = (
                    self.process.stderr.read().decode()
                    if self.process.stderr
                    else ""
                )
                raise QMDStartupError(
                    f"QMD exited during startup: {stderr[:200]}"
                )
            try:
                resp = requests.post(
                    self.base_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": "tools/list",
                    },
                    timeout=5,
                )
                if resp.status_code == 200:
                    return
            except requests.ConnectionError:
                pass
            time.sleep(0.5)
        raise QMDStartupError(f"QMD not ready after {timeout}s")

    @staticmethod
    def _parse_mcp_response(data: dict) -> list[dict]:
        """Parse MCP tools/call response into list of result dicts."""
        result = data.get("result", {})
        content_blocks = result.get("content", [])
        for block in content_blocks:
            if block.get("type") == "text":
                return json.loads(block["text"])
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_retriever.py -v`
Expected: All PASS

- [ ] **Step 5: Write test for port conflict detection**

Add to `tests/test_retriever.py`:

```python
@patch("scripts.retriever.socket.socket")
def test_qmd_service_detects_port_conflict(mock_socket_cls):
    """Raises QMDStartupError if port is already in use."""
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0
    mock_sock.__enter__ = lambda s: mock_sock
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_socket_cls.return_value = mock_sock

    from scripts.retriever import QMDService, QMDStartupError

    with pytest.raises(QMDStartupError, match="already in use"):
        with QMDService(port=9999):
            pass
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_retriever.py::test_qmd_service_detects_port_conflict -v`
Expected: PASS

- [ ] **Step 7: Write test for exit with already-crashed process**

Add to `tests/test_retriever.py`:

```python
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.requests.post")
def test_qmd_service_exit_handles_crashed_process(mock_post, mock_popen):
    """__exit__ handles gracefully if QMD already crashed."""
    proc = MagicMock()
    proc.poll.side_effect = [None, None, 1]  # running, then crashed
    proc.pid = 12345
    mock_popen.return_value = proc

    mock_post.return_value = MagicMock(
        status_code=200, json=lambda: {"result": "ok"}
    )

    from scripts.retriever import QMDService

    with QMDService(port=9999):
        pass

    proc.terminate.assert_not_called()
```

- [ ] **Step 8: Run all retriever tests**

Run: `python -m pytest tests/test_retriever.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add scripts/retriever.py tests/test_retriever.py
git commit -m "feat: add QMDService context manager with lifecycle management"
```

---

### Task 5: index subcommand

**Files:**
- Modify: `scripts/batch_pipeline.py` (add `cmd_index` + argparse entry)
- Test: `tests/test_index.py`

- [ ] **Step 1: Write failing test for cmd_index**

Create `tests/test_index.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_index.py -v`
Expected: FAIL (no `cmd_index` in batch_pipeline)

- [ ] **Step 3: Implement cmd_index**

Add to `scripts/batch_pipeline.py` (after `cmd_orchestrate`, around line 1150):

```python
def cmd_index(args):
    """index subcommand -- build QMD index and inject Context metadata."""
    import subprocess as sp

    kb_root = resolve_kb_root(getattr(args, "kb_root", None))
    print(f"知识库路径: {kb_root}")
    force = getattr(args, "force", False)

    orgs_found = []
    for org_dir in sorted(kb_root.iterdir()):
        if not org_dir.is_dir() or org_dir.name.startswith("."):
            continue
        extracted_dir = org_dir / "extracted"
        if not extracted_dir.exists():
            continue
        md_files = sorted(extracted_dir.glob("*.md"))
        if not md_files:
            continue
        orgs_found.append((org_dir.name, org_dir, md_files))

    if not orgs_found:
        print("No extracted/*.md files found", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(orgs_found)} organizations\n")

    for org_name, org_dir, md_files in orgs_found:
        extracted_dir = org_dir / "extracted"
        cmd = [
            "qmd", "collection", "add",
            str(extracted_dir),
            "--name", org_name,
            "--mask", "**/*.md",
        ]
        if force:
            cmd.append("--force")
        print(f"  collection: {org_name}")
        sp.run(cmd, check=True)

    print("\nGenerating embeddings (Qwen3-Embedding)...")
    embed_env = {**os.environ, "QMD_EMBED_MODEL": "Qwen3-Embedding"}
    embed_cmd = ["qmd", "embed"]
    if force:
        embed_cmd.append("-f")
    sp.run(embed_cmd, check=True, env=embed_env)

    print("\nInjecting contexts...")
    for org_name, org_dir, md_files in orgs_found:
        ds_path = org_dir / "data_structure.md"
        if ds_path.exists():
            first_line = ds_path.read_text(encoding="utf-8").split("\n")[0]
            org_desc = first_line.lstrip("# ").strip() or org_name
        else:
            org_desc = org_name

        sp.run(
            ["qmd", "context", "add", f"qmd://{org_name}", org_desc],
            check=True,
        )
        print(f"  context: {org_name} = {org_desc}")

        for md_file in md_files:
            file_desc = f"{org_name} {md_file.stem.replace('_', ' ')}"
            sp.run(
                ["qmd", "context", "add",
                 f"qmd://{org_name}/{md_file.stem}", file_desc],
                check=True,
            )

    print(f"\nIndex complete: {len(orgs_found)} organizations")
```

- [ ] **Step 4: Register index subcommand in argparse**

Add to the argparse section at the bottom of `scripts/batch_pipeline.py` (before `verify-batch`):

```python
    # index
    p_index = sub.add_parser("index", help="Build QMD index and inject Context metadata")
    p_index.add_argument("--kb-root", help="Knowledge base root directory")
    p_index.add_argument("--force", action="store_true", help="Force rebuild index")
```

And in the dispatch block:

```python
    elif args.command == "index":
        cmd_index(args)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_index.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_index.py
git commit -m "feat: add index subcommand for QMD collection + context setup"
```

---

### Task 6: build_queries helper

**Files:**
- Modify: `scripts/batch_pipeline.py` (add `build_queries` function)
- Modify: `tests/test_orchestrate.py` (add test)

- [ ] **Step 1: Write failing test**

Add to `tests/test_orchestrate.py`:

```python
def test_build_queries_generates_per_dimension_queries():
    from scripts.batch_pipeline import build_queries

    patient = {"disease_type": "gastric cancer"}
    features = {
        "staging_keywords": ["T3", "N2", "M0"],
        "molecular_keywords": ["HER2+", "PD-L1 CPS>=5"],
        "treatment_keywords": ["chemotherapy", "targeted therapy"],
        "all_keywords": ["T3", "N2", "M0", "HER2+", "chemotherapy"],
    }

    queries = build_queries(patient, features)

    assert len(queries) == 3
    assert "gastric cancer" in queries[0]
    assert "T3" in queries[0]
    assert "HER2+" in queries[1]
    assert "chemotherapy" in queries[2]


def test_build_queries_fallback_for_sparse_patient():
    from scripts.batch_pipeline import build_queries

    patient = {"disease_type": "gastric cancer"}
    features = {"all_keywords": ["gastric"]}

    queries = build_queries(patient, features)

    assert len(queries) == 1
    assert "gastric cancer" in queries[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrate.py::test_build_queries_generates_per_dimension_queries -v`
Expected: FAIL (no `build_queries` function)

- [ ] **Step 3: Implement build_queries**

Add to `scripts/batch_pipeline.py` (after `filter_orgs_by_disease`, around line 490):

```python
def build_queries(patient: dict, features: dict) -> list[str]:
    """Build QMD queries from patient features. One query per clinical dimension.

    Args:
        patient: Patient data dict (with disease_type etc.)
        features: Output of extract_patient_features()

    Returns:
        List of natural language query strings (1-N)
    """
    queries = []
    disease = patient.get("disease_type", "")

    staging = features.get("staging_keywords", [])
    if staging:
        queries.append(
            f"{disease} {' '.join(staging)} diagnosis staging treatment"
        )

    molecular = features.get("molecular_keywords", [])
    if molecular:
        queries.append(
            f"{disease} {' '.join(molecular)} targeted therapy immunotherapy"
        )

    treatment = features.get("treatment_keywords", [])
    if treatment:
        queries.append(
            f"{disease} {' '.join(treatment)} recommended regimen evidence level"
        )

    return queries or [f"{disease} treatment recommendation"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrate.py::test_build_queries_generates_per_dimension_queries tests/test_orchestrate.py::test_build_queries_fallback_for_sparse_patient -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_orchestrate.py
git commit -m "feat: add build_queries helper for QMD natural language queries"
```

---

## Layer 3: Pipeline Adaptation

### Task 7: Delete grep code

**Files:**
- Modify: `scripts/batch_pipeline.py` (delete `escape_grep_keyword`, `generate_grep_commands`, `_GREP_SPECIAL`)
- Delete: `tests/test_grep.py`

- [ ] **Step 1: Delete grep functions from batch_pipeline.py**

Remove from `scripts/batch_pipeline.py`:
- Line 433: `_GREP_SPECIAL = re.compile(...)` (1 line)
- Lines 436-452: `escape_grep_keyword()` function
- Lines 491-570 (approx): `generate_grep_commands()` function (the full function including both slim and full profile branches)

- [ ] **Step 2: Delete test_grep.py**

```bash
git rm tests/test_grep.py
```

- [ ] **Step 3: Verify no remaining references**

Run: `grep -rn "escape_grep_keyword\|generate_grep_commands\|_GREP_SPECIAL" scripts/ tests/ --include="*.py"`

Fix any remaining references. References in orchestrate/prompt will be fixed in subsequent tasks.

- [ ] **Step 4: Commit**

```bash
git add scripts/batch_pipeline.py
git rm tests/test_grep.py
git commit -m "refactor: delete escape_grep_keyword, generate_grep_commands, and test_grep.py"
```

---

### Task 8: Rewrite cmd_orchestrate with QMD pre-retrieval

**Files:**
- Modify: `scripts/batch_pipeline.py:1050-1130` (`cmd_orchestrate`)
- Modify: `tests/test_orchestrate.py`

- [ ] **Step 1: Write failing test for new orchestrate flow**

Add to `tests/test_orchestrate.py`:

```python
import json
from unittest.mock import MagicMock, patch


@patch("scripts.batch_pipeline.QMDService")
@patch("scripts.batch_pipeline.scan_knowledge_base")
@patch("scripts.batch_pipeline.resolve_kb_root")
def test_orchestrate_uses_qmd_preretrieval(
    mock_resolve, mock_scan, mock_qmd_cls, tmp_path
):
    """orchestrate should use QMD pre-retrieval instead of grep."""
    mock_resolve.return_value = tmp_path
    mock_scan.return_value = {
        "orgs": ["NCCN"],
        "org_files": {
            "NCCN": [{"file": "NCCN_Gastric.md", "lines": 100}]
        },
        "root_index_content": "# KB Root",
    }

    mock_svc = MagicMock()
    mock_svc.query.return_value = [
        {
            "content": "Chemotherapy is recommended for stage IV.",
            "path": "NCCN/extracted/NCCN_Gastric.md",
            "score": 0.85,
            "context": "NCCN gastric guidelines",
        }
    ]
    mock_qmd_cls.return_value.__enter__ = MagicMock(return_value=mock_svc)
    mock_qmd_cls.return_value.__exit__ = MagicMock(return_value=False)

    patients_path = tmp_path / "patients.json"
    patients_path.write_text(
        json.dumps({
            "patients": [{
                "patient_id": "P001",
                "patient_name": "Test",
                "disease_type": "gastric cancer",
                "t_stage": "T3",
                "n_stage": "N2",
                "m_stage": "M0",
            }]
        }),
        encoding="utf-8",
    )

    output_dir = tmp_path / "batches"

    args = MagicMock()
    args.patients = str(patients_path)
    args.output_dir = str(output_dir)
    args.batch_size = 5
    args.max_prompt_tokens = 50000
    args.kb_root = str(tmp_path)
    args.profile = "full"

    from scripts.batch_pipeline import cmd_orchestrate

    cmd_orchestrate(args)

    mock_svc.query.assert_called()
    assert output_dir.exists()
    prompt_files = list(output_dir.glob("*.md"))
    assert len(prompt_files) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_orchestrate.py::test_orchestrate_uses_qmd_preretrieval -v`
Expected: FAIL (orchestrate still uses grep)

- [ ] **Step 3: Rewrite cmd_orchestrate**

Replace `cmd_orchestrate` in `scripts/batch_pipeline.py` (lines ~1050-1130):

```python
def cmd_orchestrate(args):
    """orchestrate subcommand -- batch processing with QMD pre-retrieval."""
    from scripts.retriever import QMDService

    kb_root = resolve_kb_root(getattr(args, "kb_root", None))
    print(f"Knowledge base: {kb_root}")

    kb_profile = scan_knowledge_base(kb_root)
    if not kb_profile["orgs"]:
        print("Knowledge base is empty", file=sys.stderr)
        sys.exit(1)

    config = get_profile(getattr(args, "profile", "full"))

    patients_path = Path(args.patients).resolve()
    if not patients_path.exists():
        print(f"Patients file not found: {patients_path}", file=sys.stderr)
        sys.exit(1)
    patients_data = json.loads(patients_path.read_text(encoding="utf-8"))
    patients = patients_data.get("patients", [])
    if not patients:
        print("Patient list is empty", file=sys.stderr)
        sys.exit(1)

    enriched_patients = []
    total_results = 0
    total_queries = 0

    with QMDService() as qmd:
        for p in patients:
            features = extract_patient_features(p)
            queries = build_queries(p, features)
            total_queries += len(queries)

            retrieval_results = []
            for q in queries:
                hits = qmd.query(q, top_k=10, min_score=0.3)
                retrieval_results.extend(hits)

            seen = set()
            unique_results = []
            for hit in retrieval_results:
                key = (hit.get("path", ""), hit.get("content", "")[:100])
                if key not in seen:
                    seen.add(key)
                    unique_results.append(hit)

            total_results += len(unique_results)
            enriched = {
                **p,
                "features": features,
                "retrieval_results": unique_results,
            }
            enriched_patients.append(enriched)

    print(
        f"Pre-retrieval done: {len(patients)} patients, "
        f"{total_queries} queries, {total_results} results\n"
    )

    batch_size = args.batch_size
    max_tokens = args.max_prompt_tokens
    batches = _split_patients(enriched_patients, batch_size)

    final_batches = []
    for batch in batches:
        prompt = generate_batch_prompt(
            batch, kb_profile, str(kb_root),
            len(final_batches) + 1, len(batches), config=config,
        )
        tokens = estimate_tokens(prompt)
        if tokens > max_tokens and len(batch) > 1:
            sub_batches = _auto_split_batch(
                batch, kb_profile, str(kb_root), max_tokens, config=config,
            )
            final_batches.extend(sub_batches)
        else:
            final_batches.append(batch)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, batch in enumerate(final_batches, 1):
        output_file = output_dir / f"rag_batch_{i:03d}.json"
        prompt = generate_batch_prompt(
            batch, kb_profile, str(kb_root),
            i, len(final_batches),
            output_file=str(output_file), config=config,
        )
        prompt_file = output_dir / f"batch_{i:03d}_prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        print(f"  batch {i:03d}: {len(batch)} patients -> {prompt_file.name}")

    plan = {
        "total_patients": len(patients),
        "total_batches": len(final_batches),
        "total_queries": total_queries,
        "total_retrieval_results": total_results,
        "kb_profile": {
            "orgs": kb_profile["orgs"],
            "kb_root": str(kb_root),
        },
        "batches": [
            {
                "batch_id": f"batch_{i:03d}",
                "patient_count": len(b),
                "prompt_file": f"batch_{i:03d}_prompt.md",
                "output_file": f"rag_batch_{i:03d}.json",
                "status": "pending",
            }
            for i, b in enumerate(final_batches, 1)
        ],
    }
    plan_path = output_dir / "orchestration_plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nOrchestration complete: {plan_path}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_orchestrate.py::test_orchestrate_uses_qmd_preretrieval -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_orchestrate.py
git commit -m "feat: rewrite cmd_orchestrate with QMD pre-retrieval"
```

---

### Task 9: Rewrite generate_batch_prompt

**Files:**
- Modify: `scripts/batch_pipeline.py:886+` (`generate_batch_prompt`)
- Modify: `tests/test_prompt.py`

- [ ] **Step 1: Write failing test for new prompt structure**

Add to `tests/test_prompt.py`:

```python
def test_prompt_contains_retrieval_results_not_grep():
    """New prompt embeds retrieval results, not grep commands."""
    from scripts.batch_pipeline import generate_batch_prompt

    batch = [
        {
            "patient_id": "P001",
            "patient_name": "Test Patient",
            "disease_type": "gastric cancer",
            "features": {
                "staging_keywords": ["T3"],
                "all_keywords": ["T3", "gastric"],
            },
            "retrieval_results": [
                {
                    "content": "Stage III: perioperative chemo recommended.",
                    "path": "NCCN/extracted/NCCN_Gastric_2026.md",
                    "score": 0.85,
                    "context": "NCCN gastric guidelines 2026 V2",
                },
                {
                    "content": "T3N2M0: D2 gastrectomy.",
                    "path": "CSCO/extracted/CSCO_Gastric_2026.md",
                    "score": 0.72,
                    "context": "CSCO gastric guidelines 2026",
                },
            ],
        }
    ]
    kb_profile = {
        "orgs": ["NCCN", "CSCO"],
        "root_index_content": "# KB Root",
    }

    prompt = generate_batch_prompt(batch, kb_profile, "/kb", 1, 1)

    assert "perioperative chemo" in prompt
    assert "D2 gastrectomy" in prompt
    assert "0.85" in prompt
    assert "grep" not in prompt.lower()
    assert "CMD-P" not in prompt
    assert "retrieval_sources" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_prompt.py::test_prompt_contains_retrieval_results_not_grep -v`
Expected: FAIL

- [ ] **Step 3: Rewrite generate_batch_prompt**

Replace `generate_batch_prompt` in `scripts/batch_pipeline.py` (line ~886). Also delete `_generate_slim_prompt` (line ~808-885):

```python
def generate_batch_prompt(
    batch: list[dict],
    kb_profile: dict,
    kb_root: str,
    batch_idx: int,
    total_batches: int,
    output_file: str = "",
    config: "ProfileConfig | None" = None,
) -> str:
    """Generate self-contained batch prompt (based on pre-retrieval results)."""
    lines = []

    lines.append(f"# Batch {batch_idx:03d}/{total_batches:03d} Analysis Task\n")
    lines.append("<CONTEXT_RESET>")
    lines.append("Ignore all retrieval results and patient data before this message.")
    lines.append("This is a fresh, independent batch task. Start from zero.")
    lines.append("Do not reference any other batch results.")
    lines.append("</CONTEXT_RESET>\n")

    lines.append("<MANDATORY_RULES>")
    lines.append("1. Carefully read each patient's pre-retrieved results and extract recommendations, evidence levels, and sources")
    lines.append("2. Each patient must have results for every guideline organization")
    lines.append('3. If pre-retrieval has no content for a guideline, record: "This guideline does not cover this clinical question"')
    lines.append("4. Output must be in Simplified Chinese")
    lines.append("5. Recommendations must be based on pre-retrieved results, not fabricated")
    lines.append("6. Must cite which pre-retrieved chunks were used in retrieval_sources")
    lines.append("7. Citation coverage requirement: cite at least 50% of pre-retrieved results")
    lines.append("</MANDATORY_RULES>\n")

    lines.append(f"## Knowledge Base\nPath: {kb_root}\n")
    lines.append("Root index:\n---")
    lines.append(kb_profile.get("root_index_content", ""))
    lines.append("---\n")

    lines.append(f"## Patient List (this batch: {len(batch)} patients)\n")

    for pi, patient in enumerate(batch, 1):
        pid = patient.get("patient_id", "?")
        pname = patient.get("patient_name", "?")
        features = patient.get("features", {})
        retrieval_results = patient.get("retrieval_results", [])

        lines.append(f"### Patient {pi}: {pname} ({pid})\n")

        lines.append("**Clinical info:**")
        for k, v in patient.items():
            if k in ("features", "retrieval_results", "grep_commands"):
                continue
            if v is None:
                continue
            lines.append(f"- {k}: {v}")

        confidence = features.get("confidence", "high")
        lines.append(f"\n**Feature extraction confidence**: {confidence}")

        lines.append("\n**Extracted keywords:**")
        for dim_key in sorted(features.keys()):
            if dim_key.endswith("_keywords") and dim_key != "all_keywords":
                kws = features[dim_key]
                if kws:
                    dim_name = dim_key.replace("_keywords", "")
                    lines.append(f"- {dim_name}: {', '.join(kws)}")

        if retrieval_results:
            lines.append(
                f"\n#### Pre-retrieved Results ({len(retrieval_results)} chunks)\n"
            )
            by_org: dict[str, list] = {}
            for ri, hit in enumerate(retrieval_results, 1):
                path = hit.get("path", "")
                org = path.split("/")[0] if "/" in path else "unknown"
                by_org.setdefault(org, []).append((ri, hit))

            for org, hits in by_org.items():
                lines.append(f"**{org}:**\n")
                for ri, hit in hits:
                    score = hit.get("score", 0)
                    context = hit.get("context", "")
                    content = hit.get("content", "")
                    path = hit.get("path", "")
                    chunk_id = f"R{pi:03d}-{ri:02d}"
                    lines.append(f"[{chunk_id}] (score={score:.2f}) {context}")
                    lines.append(f"  file: {path}")
                    lines.append(f"  content: {content}")
                    lines.append("")

            min_citations = max(1, len(retrieval_results) // 2)
            lines.append(f"#### Citation Requirement [Patient {pi}: {pname}]")
            lines.append(
                f"Cite chunk IDs (e.g. R{pi:03d}-01) in retrieval_sources. "
                f"Coverage >= 50% (at least {min_citations} chunks)."
            )
        else:
            lines.append("\n#### Pre-retrieved Results\n")
            lines.append("No pre-retrieved results. Provide general guidance based on clinical info.\n")

        lines.append("")

    lines.append("## Output Requirements\n")
    lines.append(f"- File path: {output_file}")
    lines.append("- Format: JSON (strict template below)")
    lines.append("- Output language: Simplified Chinese")
    lines.append('- Top-level key must be `"results"` (not `"patients"`)')
    lines.append("")
    lines.append("JSON template:")

    template = {
        "batch_id": f"batch_{batch_idx:03d}",
        "processed_at": "2026-04-08T10:00:00",
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "Patient Name",
                "clinical_question": "Clinical question summary",
                "guideline_results": [
                    {
                        "guideline": "NCCN",
                        "version": "2026.V2",
                        "recommendation": "Recommendation (Chinese, >=50 chars)",
                        "evidence_level": "Category 1",
                        "source_file": "NCCN/extracted/NCCN_Gastric_2026.md",
                        "retrieval_sources": [
                            {
                                "chunk_id": "R001-01",
                                "score": 0.85,
                                "snippet": "First 40 chars of cited chunk...",
                            }
                        ],
                    }
                ],
                "citation_coverage": 0.75,
                "consensus": "Cross-guideline consensus analysis",
                "differences": "Cross-guideline difference analysis",
            }
        ],
    }

    lines.append("```json")
    lines.append(json.dumps(template, ensure_ascii=False, indent=2))
    lines.append("```\n")

    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_prompt.py::test_prompt_contains_retrieval_results_not_grep -v`
Expected: PASS

- [ ] **Step 5: Update remaining test_prompt.py tests**

Update existing tests to use `retrieval_results` instead of `grep_commands`.

- [ ] **Step 6: Run full prompt tests**

Run: `python -m pytest tests/test_prompt.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_prompt.py
git commit -m "feat: rewrite generate_batch_prompt for pre-retrieval results"
```

---

### Task 10: Anti-laziness V3 refactor + V1/V2 delete

**Files:**
- Modify: `scripts/batch_pipeline.py:1624-1732` (`_verify_batch_results`)
- Modify: `tests/test_verify_batch.py`

- [ ] **Step 1: Write failing test for citation coverage check**

Add to `tests/test_verify_batch.py`:

```python
def test_citation_coverage_warning_when_below_threshold():
    """V3: Warning when citation coverage < 50%."""
    from scripts.batch_pipeline import _verify_batch_results

    prompt_text = "# Batch 001/001\n[R001-01] [R001-02] [R001-03] [R001-04]"
    batch_data = {
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "Test",
                "clinical_questions": [
                    {
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "recommendation": "Recommend chemo " * 10,
                                "retrieval_sources": [
                                    {"chunk_id": "R001-01", "score": 0.85,
                                     "snippet": "..."},
                                ],
                            }
                        ]
                    }
                ],
                "citation_coverage": 0.25,
            }
        ]
    }

    errors, warnings = _verify_batch_results(prompt_text, batch_data)
    cov_warnings = [w for w in warnings if "coverage" in w.lower() or "覆盖" in w]
    assert len(cov_warnings) >= 1


def test_v4_contradiction_with_no_retrieval_sources():
    """V4: Warning when no retrieval sources but recommendation exists."""
    from scripts.batch_pipeline import _verify_batch_results

    prompt_text = "# Batch 001/001"
    batch_data = {
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "Test",
                "clinical_questions": [
                    {
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "recommendation": "Detailed recommendation " * 10,
                                "retrieval_sources": [],
                            }
                        ]
                    }
                ],
                "citation_coverage": 0.0,
            }
        ]
    }

    errors, warnings = _verify_batch_results(prompt_text, batch_data)
    contradiction = [w for w in warnings if "no retrieval" in w.lower() or "无检索" in w]
    assert len(contradiction) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_verify_batch.py::test_citation_coverage_warning_when_below_threshold -v`
Expected: FAIL

- [ ] **Step 3: Rewrite _verify_batch_results**

Replace `_verify_batch_results` in `scripts/batch_pipeline.py` (lines ~1624-1732):

```python
def _verify_batch_results(
    prompt_text: str,
    batch_data: dict,
    kb_root: str = "",
    config: "ProfileConfig | None" = None,
) -> tuple:
    """Verify batch results quality.

    V3: Citation coverage (>= 50% of pre-retrieved chunks cited)
    V4: Contradiction detection (no retrieval sources but has recommendation)

    Returns: (errors: list[str], warnings: list[str])
    """
    errors = []
    warnings = []

    for result in _extract_patient_list(batch_data):
        pid = result.get("patient_id", "?")

        # V3: Citation coverage
        citation_coverage = result.get("citation_coverage", None)
        if citation_coverage is not None and citation_coverage < 0.5:
            if not (config and config.skip_anti_laziness):
                warnings.append(
                    f"[{pid}] Low citation coverage "
                    f"({citation_coverage:.0%}, require >= 50%)"
                )

        for q in result.get("clinical_questions", []):
            for gr in q.get("guideline_results", []):
                org = gr.get("guideline", "")
                rec = gr.get("recommendation", "")
                sources = gr.get("retrieval_sources", [])

                # V4: No retrieval sources but has recommendation
                if not sources and len(rec) > 50:
                    warnings.append(
                        f"[{pid}] {org} no retrieval sources cited "
                        f"but has recommendation ({len(rec)} chars)"
                    )

                if not rec:
                    errors.append(f"[{pid}] {org} empty recommendation")

                src_file = gr.get("source_file", "")
                if not src_file:
                    warnings.append(f"[{pid}] {org} missing source file")

            if not q.get("consensus"):
                warnings.append(f"[{pid}] missing consensus analysis")
            if not q.get("differences"):
                warnings.append(f"[{pid}] missing difference analysis")

    return errors, warnings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_verify_batch.py::test_citation_coverage_warning_when_below_threshold tests/test_verify_batch.py::test_v4_contradiction_with_no_retrieval_sources -v`
Expected: All PASS

- [ ] **Step 5: Update existing verify-batch tests**

Remove all CMD-ID / execution_log references from test fixtures. Use `retrieval_sources` and `citation_coverage` instead.

- [ ] **Step 6: Run full verify-batch tests**

Run: `python -m pytest tests/test_verify_batch.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_verify_batch.py
git commit -m "feat: anti-laziness V3 -> citation coverage, delete V1/V2 CMD-ID checks"
```

---

### Task 11: Update validate and remaining tests

**Files:**
- Modify: `tests/test_validate_enhance.py` (adapt JSON fields)
- Modify: `tests/test_slim_profile.py` (remove grep references)
- Modify: `scripts/batch_pipeline.py` (validate function field checks)

- [ ] **Step 1: Update validate function**

In `scripts/batch_pipeline.py`, find the validate function (cmd_validate). Update field checks:
- Remove references to `execution_log` and `execution_summary`
- Add checks for `retrieval_sources` and `citation_coverage`
- Keep cross-patient consistency checks (length anomaly, cross-batch similarity, depth decay)

- [ ] **Step 2: Update test_validate_enhance.py fixtures**

Replace `execution_log` and `execution_summary` with `retrieval_sources` and `citation_coverage` in all test fixtures.

- [ ] **Step 3: Update test_slim_profile.py**

Remove references to `grep_commands`, `generate_grep_commands`, or `_generate_slim_prompt`. Update test fixtures to use `retrieval_results`.

- [ ] **Step 4: Run affected tests**

Run: `python -m pytest tests/test_validate_enhance.py tests/test_slim_profile.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short 2>&1 | tail -40`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/batch_pipeline.py tests/
git commit -m "refactor: adapt validate + slim tests for QMD retrieval model"
```

---

### Task 12: Update SKILL.md, CLAUDE.md, README.md, skill.json

**Files:**
- Modify: `SKILL.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `skill.json`

- [ ] **Step 1: Update SKILL.md Part 2 (Query Phase)**

Replace grep-based search instructions with QMD MCP:
- Remove `grep -n` command patterns
- Remove `data_structure.md` navigation for search
- Change `extracted/*.txt` to `extracted/*.md`
- Remove Agent hierarchical navigation pattern
- Add QMD query workflow using MCP tools

- [ ] **Step 2: Update SKILL.md Part 3 (Batch Phase)**

- `orchestrate` now does QMD pre-retrieval (not grep command generation)
- Add `index` subcommand documentation
- Update `verify-batch` description (citation coverage, not CMD-ID)

- [ ] **Step 3: Update CLAUDE.md**

- **Architecture**: mention QMD hybrid retrieval, Docling extraction
- **Dependencies table**: add `docling` (pip), `qmd` (npm/Node.js); remove `pdftotext` (poppler)
- **Common Commands**: add `index` subcommand, update `extract_all.py` description
- **Environment variables**: add `QMD_EMBED_MODEL`, `QMD_PORT`

- [ ] **Step 4: Update skill.json**

Update requires section:

```json
{
  "requires": {
    "bins": ["qmd", "python3"],
    "optional_bins": [],
    "pip": ["docling", "openpyxl"],
    "npm": ["@tobilu/qmd"]
  }
}
```

- [ ] **Step 5: Update README.md**

Sync with CLAUDE.md changes.

- [ ] **Step 6: Search for stale references**

Run: `grep -rn "pdftotext\|extract_pdf\|extract_docx\|extracted.*\.txt\|grep.*-n.*include" SKILL.md CLAUDE.md README.md references/ templates/`

Fix any remaining stale references.

- [ ] **Step 7: Commit**

```bash
git add SKILL.md CLAUDE.md README.md skill.json references/ templates/
git commit -m "docs: update SKILL.md, CLAUDE.md, README for QMD + Docling migration"
```

---

### Task 13: Integration test (optional, @slow gate)

**Files:**
- Create: `tests/test_qmd_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_qmd_integration.py`:

```python
"""Integration tests for QMD -- require real QMD installation.

Run with: QMD_AVAILABLE=1 python -m pytest tests/test_qmd_integration.py -v -m slow
"""

import json
import os

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("QMD_AVAILABLE"),
        reason="QMD not available (set QMD_AVAILABLE=1 to run)",
    ),
]


def test_qmd_service_real_lifecycle():
    """Start real QMD, query, stop."""
    from scripts.retriever import QMDService

    with QMDService(port=18181) as qmd:
        results = qmd.search("test query")
        assert isinstance(results, list)


def test_qmd_service_query_returns_results(tmp_path):
    """Index a test file, query it, verify results."""
    import subprocess

    test_dir = tmp_path / "test_org" / "extracted"
    test_dir.mkdir(parents=True)
    (test_dir / "test.md").write_text(
        "# Test Guide\n\n## Treatment\n\nChemotherapy is the standard.\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["qmd", "collection", "add", str(test_dir),
         "--name", "test_org", "--mask", "**/*.md"],
        check=True,
    )
    subprocess.run(["qmd", "embed"], check=True)

    from scripts.retriever import QMDService

    with QMDService(port=18182) as qmd:
        results = qmd.query("chemotherapy treatment")
        assert len(results) >= 1
        assert any("chemotherapy" in r["content"].lower() for r in results)

    subprocess.run(["qmd", "collection", "remove", "test_org"], check=False)
```

- [ ] **Step 2: Verify test skips without QMD**

Run: `python -m pytest tests/test_qmd_integration.py -v`
Expected: SKIPPED (QMD not available)

- [ ] **Step 3: Commit**

```bash
git add tests/test_qmd_integration.py
git commit -m "test: add QMD integration tests (gated by QMD_AVAILABLE)"
```

---

### Task 14: Final verification

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: All tests PASS (integration tests skipped)

- [ ] **Step 2: Search for orphaned references**

```bash
grep -rn "grep_commands\|CMD-P\|execution_log\|escape_grep\|generate_grep\|pdftotext\|extract_pdf\.py\|extract_docx\.py\|extracted.*\.txt" scripts/ tests/ SKILL.md CLAUDE.md README.md templates/ references/ --include="*.py" --include="*.md"
```

Fix any remaining orphans.

- [ ] **Step 3: Run final test suite**

```bash
python -m pytest tests/ -v
```

Expected: All PASS, 0 failures

- [ ] **Step 4: Final commit (if any fixes)**

```bash
git add -A
git commit -m "fix: clean up orphaned grep/txt references"
```
