# Batch Pipeline 三项 Bug 修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs in the batch pipeline: cross-cancer keyword contamination, missing patient metadata in merge output, and duplicate guideline results from small model repetition loops.

**Architecture:** All fixes are in `scripts/batch_pipeline.py` — modify `_extract_from_structured()` for Bug #1, add metadata back-fill in `cmd_merge()` for Bug #2, add deduplication in `_extract_patient_list()` for Bug #3. Tests go into existing test files. TDD: write failing tests first, then implement.

**Tech Stack:** Python 3, pytest, no new dependencies

---

### Task 1: Bug #1 — Fix cross-cancer keyword contamination

**Files:**
- Modify: `scripts/batch_pipeline.py:643-648` (`_extract_from_structured`)
- Test: `tests/test_features.py` (append new tests)

- [ ] **Step 1: Write failing tests for cross-cancer contamination**

Append to `tests/test_features.py`:

```python
def test_lung_patient_no_gastric_keywords():
    """肺癌患者不应出现 gastric 关键词"""
    p = {
        "patient_id": "L001", "patient_name": "肺癌患者",
        "primary_site": "右肺上叶",
        "pathology": "腺癌",
        **{k: None for k in [
            "gender", "age", "siewert_type", "patient_type", "prior_treatment",
            "biopsy_molecular", "gross_molecular", "abnormal_markers", "marker_change",
            "staging_prefix", "t_stage", "t4b_invasion", "n_stage", "m_stage",
            "m_sites", "staging_notes", "symptom_change", "response",
            "tumor_emergency", "comorbidities", "clinical_narrative",
        ]},
    }
    from scripts.batch_pipeline import extract_patient_features
    features = extract_patient_features(p)
    all_kw = " ".join(features["diagnosis_keywords"]).lower()
    assert "gastric" not in all_kw
    assert "胃癌" not in all_kw
    # 应包含肺相关关键词
    assert any(k in all_kw for k in ["lung", "pulmonary", "肺"])


def test_gastric_patient_has_correct_keywords():
    """胃癌患者应映射出正确的中英文关键词"""
    p = {
        "patient_id": "G001", "patient_name": "胃癌患者",
        "primary_site": "胃体",
        "pathology": "腺癌",
        **{k: None for k in [
            "gender", "age", "siewert_type", "patient_type", "prior_treatment",
            "biopsy_molecular", "gross_molecular", "abnormal_markers", "marker_change",
            "staging_prefix", "t_stage", "t4b_invasion", "n_stage", "m_stage",
            "m_sites", "staging_notes", "symptom_change", "response",
            "tumor_emergency", "comorbidities", "clinical_narrative",
        ]},
    }
    from scripts.batch_pipeline import extract_patient_features
    features = extract_patient_features(p)
    all_kw = " ".join(features["diagnosis_keywords"]).lower()
    assert "gastric" in all_kw
    assert "stomach" in all_kw


def test_unmapped_cancer_retains_primary_site():
    """未在映射表中的癌种只保留 primary_site 原值"""
    p = {
        "patient_id": "U001", "patient_name": "罕见癌种",
        "primary_site": "腮腺",
        "pathology": "腺样囊性癌",
        **{k: None for k in [
            "gender", "age", "siewert_type", "patient_type", "prior_treatment",
            "biopsy_molecular", "gross_molecular", "abnormal_markers", "marker_change",
            "staging_prefix", "t_stage", "t4b_invasion", "n_stage", "m_stage",
            "m_sites", "staging_notes", "symptom_change", "response",
            "tumor_emergency", "comorbidities", "clinical_narrative",
        ]},
    }
    from scripts.batch_pipeline import extract_patient_features
    features = extract_patient_features(p)
    assert "腮腺" in features["diagnosis_keywords"]
    all_kw = " ".join(features["diagnosis_keywords"]).lower()
    assert "gastric" not in all_kw


def test_special_pathology_retained_as_keyword():
    """特殊病理描述作为完整关键词保留"""
    p = {
        "patient_id": "S001", "patient_name": "特殊病理",
        "primary_site": "Gastric Signet Ring Cell Carcinoma",
        **{k: None for k in [
            "gender", "age", "pathology", "siewert_type", "patient_type",
            "prior_treatment", "biopsy_molecular", "gross_molecular",
            "abnormal_markers", "marker_change", "staging_prefix", "t_stage",
            "t4b_invasion", "n_stage", "m_stage", "m_sites", "staging_notes",
            "symptom_change", "response", "tumor_emergency", "comorbidities",
            "clinical_narrative",
        ]},
    }
    from scripts.batch_pipeline import extract_patient_features
    features = extract_patient_features(p)
    assert "Gastric Signet Ring Cell Carcinoma" in features["diagnosis_keywords"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_features.py::test_lung_patient_no_gastric_keywords tests/test_features.py::test_gastric_patient_has_correct_keywords tests/test_features.py::test_unmapped_cancer_retains_primary_site tests/test_features.py::test_special_pathology_retained_as_keyword -v`

Expected: `test_lung_patient_no_gastric_keywords` and `test_unmapped_cancer_retains_primary_site` FAIL (because hardcoded "gastric" is injected for all patients).

- [ ] **Step 3: Fix `_extract_from_structured()` to use dynamic mapping**

In `scripts/batch_pipeline.py`, replace lines 645-646:

```python
    if p.get("primary_site"):
        features["diagnosis_keywords"].extend([p["primary_site"], "gastric", "胃癌"])
```

With:

```python
    if p.get("primary_site"):
        features["diagnosis_keywords"].append(p["primary_site"])
        features["diagnosis_keywords"].extend(_extract_disease_keywords(p["primary_site"]))
```

- [ ] **Step 4: Run all tests to verify fix works and nothing breaks**

Run: `python3 -m pytest tests/ -v`

Expected: All tests PASS (107 existing + 4 new = 111).

- [ ] **Step 5: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_features.py
git commit -m "fix: replace hardcoded gastric keywords with dynamic disease mapping

_extract_from_structured() was injecting 'gastric'/'胃癌' for all
patients regardless of cancer type. Now uses _extract_disease_keywords()
to map primary_site to correct bilingual keywords."
```

---

### Task 2: Bug #3 — Add guideline_results deduplication

**Files:**
- Modify: `scripts/batch_pipeline.py:1202-1238` (add function before `_extract_patient_list`, call it inside)
- Test: `tests/test_extract_patient_list.py` (append new tests)

> Note: Task 2 (Bug #3) is implemented before Task 3 (Bug #2) because `_extract_patient_list` is called inside `cmd_merge`. The dedup must be in place before we add metadata back-fill logic to `cmd_merge`.

- [ ] **Step 1: Write failing tests for deduplication**

Append to `tests/test_extract_patient_list.py`:

```python
def test_dedup_removes_repeated_guideline_results():
    """重复的 guideline_results 只保留第一条"""
    rec = {"guideline": "NCCN", "recommendation": "推荐曲妥珠单抗联合化疗"}
    data = {"batch_id": "batch_001", "results": [{
        "patient_id": "P001",
        "guideline_results": [
            {**rec, "source_lines": "10-20"},
            {**rec, "source_lines": "10-20"},
            {**rec, "source_lines": "30-40"},  # same rec, different lines
            {"guideline": "ESMO", "recommendation": "推荐曲妥珠单抗联合化疗"},  # different guideline
            {**rec, "source_lines": "10-20"},
            {**rec, "source_lines": "10-20"},
        ],
        "consensus": ["共识A", "共识A", "共识B", "共识A"],
        "differences": ["差异X", "差异X"],
    }]}
    patients = _extract_patient_list(data)
    p = patients[0]
    cq = p["clinical_questions"][0]
    # NCCN rec kept once + ESMO rec kept once = 2
    assert len(cq["guideline_results"]) == 2
    assert cq["guideline_results"][0]["guideline"] == "NCCN"
    assert cq["guideline_results"][1]["guideline"] == "ESMO"
    # consensus deduped preserving order
    assert cq["consensus"] == ["共识A", "共识B"]
    # differences deduped
    assert cq["differences"] == ["差异X"]


def test_dedup_no_duplicates_unchanged():
    """无重复时 guideline_results 不变"""
    data = {"batch_id": "batch_001", "results": [{
        "patient_id": "P001",
        "guideline_results": [
            {"guideline": "NCCN", "recommendation": "rec1"},
            {"guideline": "ESMO", "recommendation": "rec2"},
        ],
        "consensus": ["共识"],
        "differences": [],
    }]}
    patients = _extract_patient_list(data)
    cq = patients[0]["clinical_questions"][0]
    assert len(cq["guideline_results"]) == 2


def test_dedup_different_guidelines_same_rec_kept():
    """不同指南的相同推荐文本不被误去重"""
    data = {"batch_id": "batch_001", "results": [{
        "patient_id": "P001",
        "guideline_results": [
            {"guideline": "NCCN", "recommendation": "一线推荐曲妥珠单抗"},
            {"guideline": "ESMO", "recommendation": "一线推荐曲妥珠单抗"},
            {"guideline": "CSCO", "recommendation": "一线推荐曲妥珠单抗"},
        ],
    }]}
    patients = _extract_patient_list(data)
    cq = patients[0]["clinical_questions"][0]
    assert len(cq["guideline_results"]) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_extract_patient_list.py::test_dedup_removes_repeated_guideline_results tests/test_extract_patient_list.py::test_dedup_no_duplicates_unchanged tests/test_extract_patient_list.py::test_dedup_different_guidelines_same_rec_kept -v`

Expected: `test_dedup_removes_repeated_guideline_results` FAILS (6 items remain instead of 2).

- [ ] **Step 3: Implement `_deduplicate_guideline_results()` and wire into `_extract_patient_list()`**

In `scripts/batch_pipeline.py`, add the following function **before** `_extract_patient_list()` (before line 1202):

```python
def _deduplicate_guideline_results(patient: dict) -> dict:
    """去除小模型重复输出的 guideline_results / consensus / differences。"""
    pid = patient.get("patient_id", "?")
    total_removed = 0

    for cq in patient.get("clinical_questions", []):
        grs = cq.get("guideline_results", [])
        if grs:
            seen = set()
            unique = []
            for gr in grs:
                key = (gr.get("guideline", ""), gr.get("recommendation", ""))
                if key not in seen:
                    seen.add(key)
                    unique.append(gr)
            removed = len(grs) - len(unique)
            if removed:
                total_removed += removed
                cq["guideline_results"] = unique

        if cq.get("consensus"):
            cq["consensus"] = list(dict.fromkeys(cq["consensus"]))
        if cq.get("differences"):
            cq["differences"] = list(dict.fromkeys(cq["differences"]))

    if total_removed:
        print(f"  ⚠ 患者 {pid}: 去除 {total_removed} 条重复推荐", file=sys.stderr)
    return patient
```

Then modify `_extract_patient_list()` to call it before each `return`. Change the two return points:

Replace (around line 1229):
```python
        return patients
```
With:
```python
        return [_deduplicate_guideline_results(p) for p in patients]
```

Replace (around line 1238):
```python
    return patients
```
With:
```python
    return [_deduplicate_guideline_results(p) for p in patients]
```

- [ ] **Step 4: Run all tests to verify**

Run: `python3 -m pytest tests/ -v`

Expected: All tests PASS (111 existing + 3 new = 114).

- [ ] **Step 5: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_extract_patient_list.py
git commit -m "fix: deduplicate repeated guideline_results from small model loops

Small models (Qwen 27B, glm-5.1) often enter repetition loops when
processing single patients, producing dozens of identical recommendations.
_deduplicate_guideline_results() removes duplicates keyed by
(guideline, recommendation) tuple, preserving first occurrence order."
```

---

### Task 3: Bug #2 — Add patient metadata back-fill in merge

**Files:**
- Modify: `scripts/batch_pipeline.py:1244-1288` (`cmd_merge`), `scripts/batch_pipeline.py:2326-2329` (CLI arg)
- Test: `tests/test_merge.py` (append new tests)

- [ ] **Step 1: Write failing tests for metadata back-fill**

Append to `tests/test_merge.py`:

```python
def test_merge_backfills_metadata_from_patients(tmp_path):
    """merge --patients 回注缺失的患者元数据"""
    # patients.json with full metadata
    patients_data = {
        "patient_count": 1,
        "patients": [{
            "patient_id": "P001",
            "patient_name": "张三",
            "primary_site": "胃体",
            "disease_type": "胃腺癌",
            "diagnosis_summary": "cT4aN1M0 HER2+ 局部晚期",
        }],
    }
    patients_file = tmp_path / "patients.json"
    patients_file.write_text(json.dumps(patients_data, ensure_ascii=False), encoding="utf-8")

    # batch result WITHOUT metadata (typical LLM output)
    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [{
            "patient_id": "P001",
            "clinical_questions": [{
                "guideline_results": [
                    {"guideline": "NCCN", "recommendation": "推荐"},
                ],
                "consensus": [],
                "differences": [],
            }],
        }],
    })

    output = tmp_path / "merged.json"

    class Args(FakeArgs):
        def __init__(self, input_dir, output, patients=None):
            super().__init__(input_dir, output)
            self.patients = patients

    cmd_merge(Args(tmp_path, output, str(patients_file)))
    result = json.loads(output.read_text(encoding="utf-8"))
    p = result["results"][0]
    assert p["patient_name"] == "张三"
    assert p["primary_site"] == "胃体"
    assert p["disease_type"] == "胃腺癌"
    assert p["diagnosis_summary"] == "cT4aN1M0 HER2+ 局部晚期"


def test_merge_no_patients_flag_unchanged(tmp_path):
    """merge 不带 --patients 时行为不变"""
    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [{
            "patient_id": "P001",
            "clinical_questions": [{
                "guideline_results": [{"guideline": "NCCN", "recommendation": "推荐"}],
                "consensus": [],
                "differences": [],
            }],
        }],
    })
    output = tmp_path / "merged.json"

    class Args(FakeArgs):
        def __init__(self, input_dir, output):
            super().__init__(input_dir, output)
            self.patients = None

    cmd_merge(Args(tmp_path, output))
    result = json.loads(output.read_text(encoding="utf-8"))
    p = result["results"][0]
    # No backfill — fields stay absent
    assert "patient_name" not in p
    assert "primary_site" not in p


def test_merge_llm_fields_not_overwritten(tmp_path):
    """LLM 已输出的字段不被 patients.json 覆盖"""
    patients_data = {
        "patient_count": 1,
        "patients": [{
            "patient_id": "P001",
            "patient_name": "张三",
            "primary_site": "胃体",
            "disease_type": "胃腺癌",
            "diagnosis_summary": "原始摘要",
        }],
    }
    patients_file = tmp_path / "patients.json"
    patients_file.write_text(json.dumps(patients_data, ensure_ascii=False), encoding="utf-8")

    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [{
            "patient_id": "P001",
            "patient_name": "LLM输出的名字",
            "diagnosis_summary": "LLM生成的摘要",
            "clinical_questions": [{
                "guideline_results": [{"guideline": "NCCN", "recommendation": "推荐"}],
                "consensus": [],
                "differences": [],
            }],
        }],
    })

    output = tmp_path / "merged.json"

    class Args(FakeArgs):
        def __init__(self, input_dir, output, patients=None):
            super().__init__(input_dir, output)
            self.patients = patients

    cmd_merge(Args(tmp_path, output, str(patients_file)))
    result = json.loads(output.read_text(encoding="utf-8"))
    p = result["results"][0]
    assert p["patient_name"] == "LLM输出的名字"  # NOT overwritten
    assert p["diagnosis_summary"] == "LLM生成的摘要"  # NOT overwritten
    assert p["primary_site"] == "胃体"  # backfilled (was missing)
    assert p["disease_type"] == "胃腺癌"  # backfilled (was missing)


def test_merge_warns_on_missing_patient_id(tmp_path, capsys):
    """batch 结果中 patient_id 不在 patients.json 中 → 警告但不中断"""
    patients_data = {
        "patient_count": 1,
        "patients": [{"patient_id": "P001", "patient_name": "张三"}],
    }
    patients_file = tmp_path / "patients.json"
    patients_file.write_text(json.dumps(patients_data, ensure_ascii=False), encoding="utf-8")

    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [{
            "patient_id": "P999",  # not in patients.json
            "clinical_questions": [{
                "guideline_results": [{"guideline": "NCCN", "recommendation": "推荐"}],
                "consensus": [],
                "differences": [],
            }],
        }],
    })
    output = tmp_path / "merged.json"

    class Args(FakeArgs):
        def __init__(self, input_dir, output, patients=None):
            super().__init__(input_dir, output)
            self.patients = patients

    cmd_merge(Args(tmp_path, output, str(patients_file)))
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["patient_count"] == 1  # merge still succeeds
    captured = capsys.readouterr()
    assert "P999" in captured.err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_merge.py::test_merge_backfills_metadata_from_patients tests/test_merge.py::test_merge_no_patients_flag_unchanged tests/test_merge.py::test_merge_llm_fields_not_overwritten tests/test_merge.py::test_merge_warns_on_missing_patient_id -v`

Expected: FAIL — `FakeArgs` doesn't have `patients` attribute, `cmd_merge` doesn't read it.

- [ ] **Step 3: Add `--patients` CLI argument**

In `scripts/batch_pipeline.py`, after line 2329 (`p_merge.add_argument("--output", ...)`), add:

```python
    p_merge.add_argument("--patients", default=None,
                         help="patients.json 路径（可选，用于回注患者元数据）")
```

- [ ] **Step 4: Implement metadata back-fill in `cmd_merge()`**

In `scripts/batch_pipeline.py`, modify `cmd_merge()`. After line 1246 (`input_dir = Path(args.input_dir).resolve()`), add the patient lookup loading:

```python
    # 加载患者元数据 lookup（可选）
    patient_lookup = {}
    if getattr(args, "patients", None):
        patients_path = Path(args.patients).resolve()
        if patients_path.exists():
            pdata = json.loads(patients_path.read_text(encoding="utf-8"))
            for p in pdata.get("patients", []):
                pid = p.get("patient_id")
                if pid:
                    patient_lookup[pid] = p
            print(f"已加载患者元数据: {len(patient_lookup)} 位患者")
        else:
            print(f"  ⚠ patients.json 不存在: {patients_path}", file=sys.stderr)
```

Then, inside the `for bf in batch_files:` loop, after the `all_results.append(result)` line (after line 1274), add the metadata back-fill:

```python
            # 回注患者元数据（仅填充缺失字段）
            if patient_lookup:
                source = patient_lookup.get(pid)
                if source:
                    for field in ("patient_name", "primary_site", "disease_type",
                                  "diagnosis_summary"):
                        if not result.get(field):
                            result[field] = source.get(field, "")
                elif pid:
                    print(f"  ⚠ 患者 {pid} 未在 patients.json 中找到，跳过元数据回注",
                          file=sys.stderr)
```

- [ ] **Step 5: Run all tests to verify**

Run: `python3 -m pytest tests/ -v`

Expected: All tests PASS (114 existing + 4 new = 118).

- [ ] **Step 6: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_merge.py
git commit -m "fix: backfill patient metadata in merge via --patients flag

LLM output in rag_batch_*.json typically omits patient_name, primary_site,
disease_type, and diagnosis_summary. The new optional --patients flag
loads patients.json and injects missing fields without overwriting
LLM-provided values."
```

---

### Task 4: Update SKILL.md documentation

**Files:**
- Modify: `SKILL.md:1299` (merge command example)

- [ ] **Step 1: Update merge command in SKILL.md**

In `SKILL.md`, find the merge command in Step 4 (around line 1299):

```bash
python scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json
```

Replace with:

```bash
python scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json --patients Output/patients.json
```

- [ ] **Step 2: Run all tests one final time**

Run: `python3 -m pytest tests/ -v`

Expected: All 118 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add SKILL.md
git commit -m "docs: add --patients flag to merge command in SKILL.md"
```
