import pytest
from scripts.batch_pipeline import _verify_batch_results


def test_citation_coverage_warning_when_below_threshold():
    """V3: Warning when citation coverage < 50%."""
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
                        ],
                        "consensus": "consensus",
                        "differences": "differences",
                    }
                ],
                "citation_coverage": 0.25,
            }
        ]
    }

    errors, warnings = _verify_batch_results(prompt_text, batch_data)
    cov_warnings = [w for w in warnings if "coverage" in w.lower()]
    assert len(cov_warnings) >= 1


def test_citation_coverage_pass_when_above_threshold():
    """V3: No warning when citation coverage >= 50%."""
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
                                "recommendation": "Recommend chemo " * 10,
                                "source_file": "NCCN/extracted/x.md",
                                "retrieval_sources": [
                                    {"chunk_id": "R001-01", "score": 0.85, "snippet": "..."},
                                    {"chunk_id": "R001-02", "score": 0.72, "snippet": "..."},
                                ],
                            }
                        ],
                        "consensus": "consensus",
                        "differences": "differences",
                    }
                ],
                "citation_coverage": 0.75,
            }
        ]
    }

    errors, warnings = _verify_batch_results(prompt_text, batch_data)
    cov_warnings = [w for w in warnings if "coverage" in w.lower()]
    assert len(cov_warnings) == 0
    assert len(errors) == 0


def test_v4_contradiction_with_no_retrieval_sources():
    """V4: Warning when no retrieval sources but recommendation exists."""
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
                        ],
                        "consensus": "consensus",
                        "differences": "differences",
                    }
                ],
                "citation_coverage": 0.0,
            }
        ]
    }

    errors, warnings = _verify_batch_results(prompt_text, batch_data)
    contradiction = [w for w in warnings if "no retrieval" in w.lower()]
    assert len(contradiction) >= 1


def test_v4_no_warning_when_sources_present():
    """V4: No contradiction warning when retrieval sources are present."""
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
                                "source_file": "NCCN/extracted/x.md",
                                "retrieval_sources": [
                                    {"chunk_id": "R001-01", "score": 0.85, "snippet": "..."},
                                ],
                            }
                        ],
                        "consensus": "consensus",
                        "differences": "differences",
                    }
                ],
                "citation_coverage": 0.75,
            }
        ]
    }

    errors, warnings = _verify_batch_results(prompt_text, batch_data)
    contradiction = [w for w in warnings if "no retrieval" in w.lower()]
    assert len(contradiction) == 0


def test_empty_recommendation_error():
    """Empty recommendation produces an error."""
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
                                "recommendation": "",
                                "source_file": "NCCN/extracted/x.md",
                                "retrieval_sources": [],
                            }
                        ],
                        "consensus": "consensus",
                        "differences": "differences",
                    }
                ],
                "citation_coverage": 0.0,
            }
        ]
    }

    errors, warnings = _verify_batch_results(prompt_text, batch_data)
    assert any("empty recommendation" in e for e in errors)


def test_missing_source_file_warning():
    """Missing source_file produces a warning."""
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
                                "recommendation": "Some recommendation text here",
                                "source_file": "",
                                "retrieval_sources": [
                                    {"chunk_id": "R001-01", "score": 0.85, "snippet": "..."},
                                ],
                            }
                        ],
                        "consensus": "consensus",
                        "differences": "differences",
                    }
                ],
                "citation_coverage": 0.75,
            }
        ]
    }

    errors, warnings = _verify_batch_results(prompt_text, batch_data)
    assert any("missing source file" in w for w in warnings)


def test_missing_consensus_warning():
    """Missing consensus analysis produces a warning."""
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
                                "recommendation": "Rec",
                                "source_file": "x.md",
                                "retrieval_sources": [{"chunk_id": "R001-01", "score": 0.8, "snippet": "..."}],
                            }
                        ],
                        "consensus": "",
                        "differences": "",
                    }
                ],
                "citation_coverage": 0.75,
            }
        ]
    }

    errors, warnings = _verify_batch_results(prompt_text, batch_data)
    assert any("consensus" in w for w in warnings)
    assert any("difference" in w for w in warnings)
