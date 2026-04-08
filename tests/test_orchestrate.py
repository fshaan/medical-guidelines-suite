import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


@patch("scripts.retriever.QMDService")
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

    from scripts.batch_pipeline import cmd_orchestrate

    cmd_orchestrate(args)

    mock_svc.query.assert_called()
    assert output_dir.exists()
    prompt_files = list(output_dir.glob("*.md"))
    assert len(prompt_files) >= 1


@patch("scripts.retriever.QMDService")
@patch("scripts.batch_pipeline.scan_knowledge_base")
@patch("scripts.batch_pipeline.resolve_kb_root")
def test_orchestrate_creates_plan(
    mock_resolve, mock_scan, mock_qmd_cls, tmp_path
):
    """orchestrate should create orchestration_plan.json."""
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
        {"content": "Test result", "path": "NCCN/extracted/x.md", "score": 0.8, "context": "ctx"}
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
            }]
        }),
        encoding="utf-8",
    )

    output_dir = tmp_path / "batches"
    args = MagicMock()
    args.patients = str(patients_path)
    args.output_dir = str(output_dir)
    args.batch_size = 5
    args.max_prompt_tokens = 80000
    args.kb_root = str(tmp_path)

    from scripts.batch_pipeline import cmd_orchestrate

    cmd_orchestrate(args)

    plan_file = output_dir / "orchestration_plan.json"
    assert plan_file.exists()
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    assert plan["total_patients"] == 1
    assert len(plan["batches"]) >= 1
    assert "total_queries" in plan
    assert "total_retrieval_results" in plan


@patch("scripts.retriever.QMDService")
@patch("scripts.batch_pipeline.scan_knowledge_base")
@patch("scripts.batch_pipeline.resolve_kb_root")
def test_orchestrate_deduplicates_results(
    mock_resolve, mock_scan, mock_qmd_cls, tmp_path
):
    """orchestrate should deduplicate retrieval results by path+content."""
    mock_resolve.return_value = tmp_path
    mock_scan.return_value = {
        "orgs": ["NCCN"],
        "org_files": {
            "NCCN": [{"file": "NCCN_Gastric.md", "lines": 100}]
        },
        "root_index_content": "# KB Root",
    }

    dup_result = {
        "content": "Same content appears twice in results.",
        "path": "NCCN/extracted/NCCN_Gastric.md",
        "score": 0.85,
        "context": "ctx",
    }
    mock_svc = MagicMock()
    mock_svc.query.return_value = [dup_result, dup_result]
    mock_qmd_cls.return_value.__enter__ = MagicMock(return_value=mock_svc)
    mock_qmd_cls.return_value.__exit__ = MagicMock(return_value=False)

    patients_path = tmp_path / "patients.json"
    patients_path.write_text(
        json.dumps({
            "patients": [{
                "patient_id": "P001",
                "patient_name": "Test",
                "disease_type": "gastric cancer",
                "staging_keywords": ["T3"],
            }]
        }),
        encoding="utf-8",
    )

    output_dir = tmp_path / "batches"
    args = MagicMock()
    args.patients = str(patients_path)
    args.output_dir = str(output_dir)
    args.batch_size = 5
    args.max_prompt_tokens = 80000
    args.kb_root = str(tmp_path)

    from scripts.batch_pipeline import cmd_orchestrate

    cmd_orchestrate(args)

    prompt_files = list(output_dir.glob("*.md"))
    assert len(prompt_files) >= 1
    content = prompt_files[0].read_text(encoding="utf-8")
    assert "Same content appears twice" in content


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
