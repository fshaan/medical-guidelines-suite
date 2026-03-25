import os
import pytest
from scripts.batch_pipeline import resolve_kb_root

def test_explicit_path(mock_kb):
    assert resolve_kb_root(str(mock_kb)) == mock_kb

def test_explicit_path_invalid(tmp_path):
    with pytest.raises(SystemExit):
        resolve_kb_root(str(tmp_path / "nonexistent"))

def test_env_var(mock_kb, monkeypatch):
    monkeypatch.setenv("MEDICAL_GUIDELINES_DIR", str(mock_kb))
    assert resolve_kb_root(None) == mock_kb

def test_local_guidelines(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEDICAL_GUIDELINES_DIR", raising=False)
    g = tmp_path / "guidelines"
    g.mkdir()
    (g / "data_structure.md").write_text("# test")
    assert resolve_kb_root(None) == g

def test_local_knowledge(monkeypatch, tmp_path):
    """./knowledge/ 作为备选路径"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEDICAL_GUIDELINES_DIR", raising=False)
    k = tmp_path / "knowledge"
    k.mkdir()
    (k / "data_structure.md").write_text("# test")
    assert resolve_kb_root(None) == k

def test_no_kb_found(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEDICAL_GUIDELINES_DIR", raising=False)
    with pytest.raises(SystemExit):
        resolve_kb_root(None)
