import json
import pytest
from pathlib import Path

def test_orchestrate_creates_plan(mock_kb, patients_json, tmp_path):
    from scripts.batch_pipeline import cmd_orchestrate
    import argparse

    output_dir = tmp_path / "batches"
    args = argparse.Namespace(
        patients=str(patients_json),
        kb_root=str(mock_kb),
        output_dir=str(output_dir),
        batch_size=5,
        max_prompt_tokens=80000,
    )
    cmd_orchestrate(args)

    plan_file = output_dir / "orchestration_plan.json"
    assert plan_file.exists()
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    assert plan["version"] == "2.2"
    assert plan["total_patients"] == 12
    assert len(plan["batches"]) >= 1
    assert plan["stats"]["orgs_covered"] == ["CSCO", "ESMO", "NCCN"]

def test_orchestrate_creates_prompt_files(mock_kb, patients_json, tmp_path):
    from scripts.batch_pipeline import cmd_orchestrate
    import argparse

    output_dir = tmp_path / "batches"
    args = argparse.Namespace(
        patients=str(patients_json),
        kb_root=str(mock_kb),
        output_dir=str(output_dir),
        batch_size=5,
        max_prompt_tokens=80000,
    )
    cmd_orchestrate(args)

    prompt_files = sorted(output_dir.glob("batch_*_prompt.md"))
    assert len(prompt_files) >= 2

    for pf in prompt_files:
        content = pf.read_text(encoding="utf-8")
        assert "<CONTEXT_RESET>" in content

def test_orchestrate_checkpoint_detection(mock_kb, patients_json, tmp_path):
    from scripts.batch_pipeline import cmd_orchestrate
    import argparse

    output_dir = tmp_path / "batches"
    output_dir.mkdir(parents=True)
    (output_dir / "rag_batch_001.json").write_text(
        json.dumps({"results": [{"patient_id": "P001"}]}), encoding="utf-8"
    )

    args = argparse.Namespace(
        patients=str(patients_json),
        kb_root=str(mock_kb),
        output_dir=str(output_dir),
        batch_size=5,
        max_prompt_tokens=80000,
    )
    cmd_orchestrate(args)

    plan = json.loads((output_dir / "orchestration_plan.json").read_text(encoding="utf-8"))
    assert plan["batches"][0]["status"] == "completed"

def test_orchestrate_existing_plan_resume(mock_kb, patients_json, tmp_path):
    from scripts.batch_pipeline import cmd_orchestrate
    import argparse

    output_dir = tmp_path / "batches"
    output_dir.mkdir(parents=True)
    old_plan = {"version": "2.2", "batches": [{"id": "batch_001", "status": "pending"}]}
    (output_dir / "orchestration_plan.json").write_text(json.dumps(old_plan), encoding="utf-8")

    args = argparse.Namespace(
        patients=str(patients_json),
        kb_root=str(mock_kb),
        output_dir=str(output_dir),
        batch_size=5,
        max_prompt_tokens=80000,
    )
    cmd_orchestrate(args)
    plan = json.loads((output_dir / "orchestration_plan.json").read_text(encoding="utf-8"))
    assert plan["total_patients"] == 12


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
