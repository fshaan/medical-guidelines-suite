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
