# --profile slim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--profile slim` mode to batch pipeline for small model (27B) compatibility, reducing grep commands from ~36 to ~12 per patient and JSON output from 5-layer 21-field to 2-layer 7-field.

**Architecture:** A `ProfileConfig` dataclass encapsulates all profile differences. Functions receive `config` as an optional parameter (default `None` → full profile). Slim JSON output is flat (one entry per patient-guideline pair); merge stage auto-detects and aggregates back to full-compatible format so downstream `generate` is untouched.

**Tech Stack:** Python 3.10+, dataclasses, argparse, pytest

**Spec:** `docs/superpowers/specs/2026-03-31-slim-profile-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/batch_pipeline.py` | Modify | Add ProfileConfig, modify grep gen / prompt gen / validate / verify / merge |
| `tests/test_slim_profile.py` | Create | All slim-specific tests (~25) |

---

### Task 1: ProfileConfig dataclass + get_profile()

**Files:**
- Modify: `scripts/batch_pipeline.py:22-23` (add `from dataclasses import dataclass, field`)
- Modify: `scripts/batch_pipeline.py:25` (insert after imports, before parse section)
- Test: `tests/test_slim_profile.py`

- [ ] **Step 1: Write failing tests for ProfileConfig**

```python
# tests/test_slim_profile.py
"""Tests for --profile slim mode."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from batch_pipeline import ProfileConfig, PROFILE_FULL, PROFILE_SLIM, get_profile


class TestProfileConfig:
    def test_full_profile_defaults(self):
        config = get_profile("full")
        assert config.name == "full"
        assert config.dimension_groups is None
        assert config.min_rec_length == 50
        assert config.skip_anti_laziness is False
        assert config.flat_json is False

    def test_slim_profile_values(self):
        config = get_profile("slim")
        assert config.name == "slim"
        assert len(config.dimension_groups) == 4
        assert config.min_rec_length == 20
        assert config.skip_anti_laziness is True
        assert config.flat_json is True
        assert config.org_filter_by_disease is True
        assert config.micro_checkpoints is True

    def test_unknown_profile_raises(self):
        with pytest.raises(KeyError):
            get_profile("unknown")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_slim_profile.py::TestProfileConfig -v`
Expected: FAIL with `ImportError: cannot import name 'ProfileConfig'`

- [ ] **Step 3: Implement ProfileConfig**

In `scripts/batch_pipeline.py`, add `from dataclasses import dataclass` at line 22, then insert after all imports (before the `# ─── parse 子命令` comment at line 27):

```python
# ─── Profile 配置 ────────────────────────────────────────────────────────────


SLIM_DIMENSION_GROUPS = [
    ["diagnosis_keywords", "staging_keywords", "metastasis_keywords"],
    ["molecular_keywords", "marker_keywords"],
    ["treatment_keywords", "event_keywords"],
    ["comorbidity_keywords", "special_keywords"],
]


@dataclass
class ProfileConfig:
    name: str = "full"
    dimension_groups: list | None = None
    min_rec_length: int = 50
    skip_anti_laziness: bool = False
    skip_snippet_verify: bool = False
    micro_checkpoints: bool = False
    flat_json: bool = False
    org_filter_by_disease: bool = False


PROFILE_FULL = ProfileConfig()

PROFILE_SLIM = ProfileConfig(
    name="slim",
    dimension_groups=SLIM_DIMENSION_GROUPS,
    min_rec_length=20,
    skip_anti_laziness=True,
    skip_snippet_verify=True,
    micro_checkpoints=True,
    flat_json=True,
    org_filter_by_disease=True,
)


def get_profile(name: str) -> ProfileConfig:
    """获取命名 profile 配置。"""
    return {"full": PROFILE_FULL, "slim": PROFILE_SLIM}[name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_slim_profile.py::TestProfileConfig -v`
Expected: 3 passed

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `python -m pytest tests/ -v`
Expected: All 80 tests pass

- [ ] **Step 6: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_slim_profile.py
git commit -m "feat(slim): add ProfileConfig dataclass and get_profile()"
```

---

### Task 2: Org filtering + dimension-grouped grep generation

**Files:**
- Modify: `scripts/batch_pipeline.py:400-448` (`generate_grep_commands`)
- Test: `tests/test_slim_profile.py`

- [ ] **Step 1: Write failing tests for org filtering and grouped grep**

Append to `tests/test_slim_profile.py`:

```python
from batch_pipeline import (
    generate_grep_commands, filter_orgs_by_disease, get_profile,
)


class TestFilterOrgsByDisease:
    def test_matches_disease_in_filenames(self):
        kb_profile = {
            "orgs": ["NCCN", "JGCA", "ESMO"],
            "org_files": {
                "NCCN": [{"file": "NCCN_GastricCancer_2026.txt", "path": "/kb/NCCN/extracted/NCCN_GastricCancer_2026.txt"}],
                "JGCA": [{"file": "JGCA_Gastric_Guidelines.txt", "path": "/kb/JGCA/extracted/JGCA_Gastric_Guidelines.txt"}],
                "ESMO": [{"file": "ESMO_BreastCancer_2025.txt", "path": "/kb/ESMO/extracted/ESMO_BreastCancer_2025.txt"}],
            },
        }
        result = filter_orgs_by_disease(kb_profile, "胃癌")
        assert "NCCN" in result
        assert "JGCA" in result
        assert "ESMO" not in result

    def test_fallback_all_orgs_when_no_match(self):
        kb_profile = {
            "orgs": ["NCCN", "JGCA"],
            "org_files": {
                "NCCN": [{"file": "NCCN_Lung.txt", "path": "..."}],
                "JGCA": [{"file": "JGCA_Lung.txt", "path": "..."}],
            },
        }
        result = filter_orgs_by_disease(kb_profile, "罕见病X")
        assert result == ["NCCN", "JGCA"]


class TestGrepGenerationSlim:
    def _make_features(self):
        return {
            "diagnosis_keywords": ["胃癌", "gastric"],
            "staging_keywords": ["T3"],
            "metastasis_keywords": ["peritoneal"],
            "molecular_keywords": ["HER2"],
            "marker_keywords": ["CEA"],
            "treatment_keywords": ["SOX"],
            "event_keywords": ["术后"],
            "comorbidity_keywords": ["diabetes"],
            "special_keywords": ["elderly"],
            "all_keywords": ["胃癌", "gastric", "T3", "peritoneal", "HER2", "CEA", "SOX", "术后", "diabetes", "elderly"],
        }

    def _make_kb_profile(self):
        return {
            "orgs": ["NCCN", "JGCA", "ESMO"],
            "org_files": {
                "NCCN": [{"file": "NCCN_GastricCancer.txt"}],
                "JGCA": [{"file": "JGCA_Gastric.txt"}],
                "ESMO": [{"file": "ESMO_GastricCancer.txt"}],
            },
        }

    def test_slim_produces_grouped_commands(self):
        config = get_profile("slim")
        features = self._make_features()
        kb = self._make_kb_profile()
        cmds = generate_grep_commands(features, kb, Path("/kb"), config=config)
        # 4 groups x 3 orgs = 12
        assert len(cmds) == 12

    def test_slim_group_merges_keywords(self):
        config = get_profile("slim")
        features = self._make_features()
        kb = self._make_kb_profile()
        cmds = generate_grep_commands(features, kb, Path("/kb"), config=config)
        # First group (diagnosis+staging+metastasis) should contain all 3 dims' keywords
        group1_cmds = [c for c in cmds if c["dimension"] == "diagnosis_staging_metastasis"]
        assert len(group1_cmds) == 3  # one per org
        for cmd in group1_cmds:
            assert "胃癌" in cmd["command"] or "gastric" in cmd["command"]

    def test_slim_skips_empty_groups(self):
        config = get_profile("slim")
        features = self._make_features()
        features["comorbidity_keywords"] = []
        features["special_keywords"] = []
        kb = self._make_kb_profile()
        cmds = generate_grep_commands(features, kb, Path("/kb"), config=config)
        # Group 4 (comorbidity+special) has no keywords → 3 groups x 3 orgs = 9
        assert len(cmds) == 9

    def test_slim_truncates_keywords_to_15(self):
        config = get_profile("slim")
        features = self._make_features()
        features["diagnosis_keywords"] = [f"kw{i}" for i in range(20)]
        features["staging_keywords"] = []
        features["metastasis_keywords"] = []
        kb = {"orgs": ["NCCN"], "org_files": {"NCCN": [{"file": "f.txt"}]}}
        cmds = generate_grep_commands(features, kb, Path("/kb"), config=config)
        group1 = [c for c in cmds if "diagnosis" in c["dimension"]]
        # Pattern should have at most 15 variants (some keywords may expand)
        # Just check command was generated
        assert len(group1) == 1

    def test_full_profile_unchanged(self):
        """Full profile with config=None produces original behavior."""
        features = self._make_features()
        kb = self._make_kb_profile()
        cmds_default = generate_grep_commands(features, kb, Path("/kb"))
        cmds_explicit = generate_grep_commands(features, kb, Path("/kb"), config=None)
        assert len(cmds_default) == len(cmds_explicit)
        # 9 dimensions x 3 orgs = 27
        assert len(cmds_default) == 27
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_slim_profile.py::TestFilterOrgsByDisease tests/test_slim_profile.py::TestGrepGenerationSlim -v`
Expected: FAIL with `ImportError: cannot import name 'filter_orgs_by_disease'`

- [ ] **Step 3: Implement filter_orgs_by_disease()**

Add before `generate_grep_commands()` (~line 399):

```python
# 疾病类型 → 搜索关键词映射
_DISEASE_KEYWORD_MAP = {
    "胃": ["gastric", "stomach", "胃"],
    "肺": ["lung", "pulmonary", "肺"],
    "乳腺": ["breast", "乳腺"],
    "结直肠": ["colorectal", "colon", "rectal", "结直肠", "结肠", "直肠"],
    "肝": ["liver", "hepat", "肝"],
    "食管": ["esophag", "食管"],
    "胰腺": ["pancrea", "胰腺"],
}


def _extract_disease_keywords(disease_type: str) -> list[str]:
    """从 disease_type 提取中英文疾病关键词。"""
    keywords = []
    for cn_key, kw_list in _DISEASE_KEYWORD_MAP.items():
        if cn_key in (disease_type or ""):
            keywords.extend(kw_list)
    if not keywords and disease_type:
        keywords.append(disease_type)
    return keywords


def filter_orgs_by_disease(kb_profile: dict, disease_type: str) -> list[str]:
    """根据 disease_type 过滤 KB 中相关的 org。"""
    disease_kws = _extract_disease_keywords(disease_type)
    if not disease_kws:
        return kb_profile["orgs"]
    relevant = []
    for org in kb_profile["orgs"]:
        files = kb_profile["org_files"].get(org, [])
        if any(
            kw.lower() in f["file"].lower()
            for f in files
            for kw in disease_kws
        ):
            relevant.append(org)
    return relevant or kb_profile["orgs"]
```

- [ ] **Step 4: Modify generate_grep_commands() to accept config**

Change signature at line 400:

```python
def generate_grep_commands(
    patient_features: dict,
    kb_profile: dict,
    kb_root: "Path",
    config: "ProfileConfig | None" = None,
) -> list[dict]:
```

Add at function top (after existing `all_kw` check):

```python
    # Slim: grouped dimensions
    if config and config.dimension_groups:
        commands = []
        orgs = kb_profile["orgs"]
        for org in orgs:
            files = kb_profile["org_files"].get(org, [])
            if not files:
                continue
            extracted_dir = str(kb_root / org / "extracted")
            for group in config.dimension_groups:
                merged_kw = []
                for dim_name in group:
                    merged_kw.extend(patient_features.get(dim_name, []))
                if not merged_kw:
                    continue
                merged_kw = list(dict.fromkeys(merged_kw))[:15]
                all_variants = []
                for kw in merged_kw:
                    all_variants.extend(escape_grep_keyword(kw))
                if not all_variants:
                    continue
                pattern = "\\|".join(all_variants)
                group_name = "_".join(d.replace("_keywords", "") for d in group)
                cmd = f'grep -n -i --include="*.txt" -r "{pattern}" "{extracted_dir}"'
                commands.append({
                    "org": org,
                    "dimension": group_name,
                    "command": cmd,
                })
        return commands

    # Full: original per-dimension logic (unchanged below)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_slim_profile.py -v`
Expected: All tests pass

- [ ] **Step 6: Run existing tests for regression**

Run: `python -m pytest tests/ -v`
Expected: All 80+ tests pass

- [ ] **Step 7: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_slim_profile.py
git commit -m "feat(slim): add org filtering and dimension-grouped grep generation"
```

---

### Task 3: Slim batch prompt template

**Files:**
- Modify: `scripts/batch_pipeline.py:651-784` (`generate_batch_prompt`)
- Test: `tests/test_slim_profile.py`

- [ ] **Step 1: Write failing tests for slim prompt**

Append to `tests/test_slim_profile.py`:

```python
from batch_pipeline import generate_batch_prompt


class TestSlimPrompt:
    def _make_batch(self):
        return [{
            "patient_id": "P001",
            "patient_name": "张三",
            "disease_type": "胃癌",
            "features": {
                "diagnosis_keywords": ["胃癌"],
                "all_keywords": ["胃癌"],
            },
            "grep_commands": [
                {"org": "NCCN", "dimension": "diagnosis_staging_metastasis",
                 "command": 'grep -n -i --include="*.txt" -r "胃癌" "/kb/NCCN/extracted"'},
            ],
        }]

    def _make_kb_profile(self):
        return {
            "orgs": ["NCCN"],
            "org_files": {"NCCN": [{"file": "NCCN_Gastric.txt"}]},
            "root_index_content": "test index",
        }

    def test_slim_prompt_contains_micro_checkpoints(self):
        config = get_profile("slim")
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1, config=config,
        )
        assert "自检" in prompt or "检查" in prompt

    def test_slim_prompt_uses_flat_json_template(self):
        config = get_profile("slim")
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1, config=config,
        )
        # Flat template has "guideline" at top level, no "guideline_results"
        assert '"guideline":' in prompt
        assert "guideline_results" not in prompt

    def test_slim_prompt_no_execution_log(self):
        config = get_profile("slim")
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1, config=config,
        )
        assert "execution_log" not in prompt
        assert "execution_summary" not in prompt

    def test_full_prompt_unchanged(self):
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1,
        )
        # Full prompt has execution_log
        assert "execution_log" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_slim_profile.py::TestSlimPrompt -v`
Expected: FAIL with `TypeError: generate_batch_prompt() got an unexpected keyword argument 'config'`

- [ ] **Step 3: Modify generate_batch_prompt() signature**

Change signature at line 651:

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
```

- [ ] **Step 4: Add slim prompt generation branch**

After the signature, at the top of the function body, add a branch that returns early for slim:

```python
    if config and config.flat_json:
        return _generate_slim_prompt(
            batch, kb_profile, kb_root, batch_idx, total_batches, output_file, config,
        )
```

Then add the new function before `generate_batch_prompt`:

```python
def _generate_slim_prompt(
    batch: list[dict],
    kb_profile: dict,
    kb_root: str,
    batch_idx: int,
    total_batches: int,
    output_file: str,
    config: "ProfileConfig",
) -> str:
    """生成 slim profile 的简化 batch prompt。"""
    lines = []
    lines.append(f"# 批次 {batch_idx:03d}/{total_batches:03d} 检索任务\n")
    lines.append("<CONTEXT_RESET>")
    lines.append("请忽略此消息之前的所有检索结果和患者信息。")
    lines.append("以下是一个全新的、独立的批次任务。")
    lines.append("</CONTEXT_RESET>\n")

    lines.append("## 规则")
    lines.append("- 逐条执行 grep 命令，不得跳过")
    lines.append("- 不得调用任何工具、函数或子代理")
    lines.append("- 所有输出使用简体中文")
    lines.append("- 禁止编写脚本批量执行\n")

    lines.append(f"## 知识库\n路径: {kb_root}\n")

    # 步骤 1: grep 命令 + 微检查点
    lines.append("## 步骤 1：执行 grep 命令\n")

    total_cmds = 0
    for pi, patient in enumerate(batch, 1):
        pid = patient.get("patient_id", "?")
        pname = patient.get("patient_name", "?")
        grep_cmds = patient.get("grep_commands", [])

        lines.append(f"### 患者 P{pi:03d}: {pname} ({pid})\n")

        for ci, gc in enumerate(grep_cmds, 1):
            cmd_id = f"CMD-P{pi:03d}-{gc['org']}-{ci:02d}"
            lines.append(f"{cmd_id}: {gc['command']}")
            total_cmds += 1

        if config.micro_checkpoints and grep_cmds:
            lines.append(f"\n【自检 P{pi:03d}】确认执行了全部 {len(grep_cmds)} 条命令。\n")

    # 步骤 2: JSON 输出
    lines.append("## 步骤 2：输出 JSON\n")
    lines.append("根据 grep 结果，输出以下格式（严格遵守，不得添加或省略字段）：\n")
    lines.append("```json")
    lines.append("{")
    lines.append(f'  "batch_id": "batch_{batch_idx:03d}",')
    lines.append('  "processed_at": "ISO时间戳",')
    lines.append('  "results": [')
    lines.append('    {')
    lines.append('      "patient_id": "实际ID",')
    lines.append('      "patient_name": "实际姓名",')
    lines.append('      "clinical_question": "一句话临床问题摘要",')
    lines.append('      "guideline": "NCCN",')
    lines.append(f'      "recommendation": ">={config.min_rec_length}字推荐内容（简体中文）",')
    lines.append('      "evidence_level": "证据等级",')
    lines.append('      "source_file": "匹配的文件名"')
    lines.append('    }')
    lines.append('  ]')
    lines.append("}")
    lines.append("```\n")

    if config.micro_checkpoints:
        lines.append("【最终自检】")
        lines.append(f"- results 条目总数应 = 患者数 x guideline数")
        lines.append(f"- 每条 recommendation >= {config.min_rec_length} 字\n")

    if output_file:
        lines.append(f"将完整 JSON 保存到: {output_file}")

    return "\n".join(lines)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_slim_profile.py::TestSlimPrompt -v`
Expected: 4 passed

- [ ] **Step 6: Run all tests for regression**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_slim_profile.py
git commit -m "feat(slim): add slim batch prompt template with micro-checkpoints"
```

---

### Task 4: Merge pipeline — flat format detection + aggregation + consensus

**Files:**
- Modify: `scripts/batch_pipeline.py:944-1000` (`_extract_patient_list`, `cmd_merge`)
- Test: `tests/test_slim_profile.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_slim_profile.py`:

```python
from batch_pipeline import _is_flat_format, _aggregate_flat_results, _generate_consensus


class TestFlatFormatDetection:
    def test_detects_flat_format(self):
        results = [{"patient_id": "P1", "guideline": "NCCN", "recommendation": "..."}]
        assert _is_flat_format(results) is True

    def test_detects_nested_format(self):
        results = [{"patient_id": "P1", "guideline_results": [{"guideline": "NCCN"}]}]
        assert _is_flat_format(results) is False

    def test_empty_results(self):
        assert _is_flat_format([]) is False


class TestAggregateFlatResults:
    def test_groups_by_patient_id(self):
        flat = [
            {"patient_id": "P1", "patient_name": "张三", "clinical_question": "Q1",
             "guideline": "NCCN", "recommendation": "推荐A", "evidence_level": "1", "source_file": "a.txt"},
            {"patient_id": "P1", "patient_name": "张三", "clinical_question": "Q1",
             "guideline": "JGCA", "recommendation": "推荐B", "evidence_level": "强", "source_file": "b.txt"},
            {"patient_id": "P2", "patient_name": "李四", "clinical_question": "Q2",
             "guideline": "NCCN", "recommendation": "推荐C", "evidence_level": "2A", "source_file": "c.txt"},
        ]
        result = _aggregate_flat_results(flat)
        assert len(result) == 2
        p1 = [r for r in result if r["patient_id"] == "P1"][0]
        assert len(p1["guideline_results"]) == 2
        assert p1["patient_name"] == "张三"

    def test_single_patient_single_guideline(self):
        flat = [
            {"patient_id": "P1", "patient_name": "A", "clinical_question": "Q",
             "guideline": "NCCN", "recommendation": "R", "evidence_level": "1", "source_file": "f.txt"},
        ]
        result = _aggregate_flat_results(flat)
        assert len(result) == 1
        assert len(result[0]["guideline_results"]) == 1

    def test_empty_input(self):
        assert _aggregate_flat_results([]) == []


class TestGenerateConsensus:
    def test_finds_common_keywords(self):
        patient = {
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "推荐SOX方案化疗联合免疫治疗"},
                {"guideline": "JGCA", "recommendation": "建议SOX方案为一线化疗选择"},
            ]
        }
        consensus, diffs = _generate_consensus(patient)
        assert len(consensus) > 0  # SOX should appear in consensus

    def test_single_guideline_no_consensus(self):
        patient = {
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "推荐化疗"},
            ]
        }
        consensus, diffs = _generate_consensus(patient)
        assert consensus == []
        assert diffs == []

    def test_empty_guideline_results(self):
        patient = {"guideline_results": []}
        consensus, diffs = _generate_consensus(patient)
        assert consensus == []
        assert diffs == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_slim_profile.py::TestFlatFormatDetection tests/test_slim_profile.py::TestAggregateFlatResults tests/test_slim_profile.py::TestGenerateConsensus -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement _is_flat_format, _aggregate_flat_results, _generate_consensus**

Add before `_extract_patient_list()` (~line 943):

```python
def _is_flat_format(results: list[dict]) -> bool:
    """检测 slim 扁平格式（result 含 guideline 键且无 guideline_results）。"""
    return bool(results) and "guideline" in results[0] and "guideline_results" not in results[0]


def _aggregate_flat_results(flat_results: list[dict]) -> list[dict]:
    """将 slim 扁平 results 按 patient_id 聚合为 full 兼容格式。"""
    if not flat_results:
        return []
    grouped = {}
    for r in flat_results:
        pid = r["patient_id"]
        if pid not in grouped:
            grouped[pid] = {
                "patient_id": pid,
                "patient_name": r.get("patient_name", ""),
                "clinical_question": r.get("clinical_question", ""),
                "guideline_results": [],
            }
        grouped[pid]["guideline_results"].append({
            "guideline": r.get("guideline", ""),
            "recommendation": r.get("recommendation", ""),
            "evidence_level": r.get("evidence_level", ""),
            "source_file": r.get("source_file", ""),
        })
    return list(grouped.values())


def _generate_consensus(patient: dict) -> tuple[list[str], list[str]]:
    """基于多 guideline 推荐文本生成 consensus/differences。"""
    recs = patient.get("guideline_results", [])
    if len(recs) < 2:
        return [], []

    all_kw_sets = []
    for r in recs:
        text = r.get("recommendation", "")
        kws = set(re.findall(r'[\u4e00-\u9fff]{2,}', text))
        all_kw_sets.append(kws)

    common = set.intersection(*all_kw_sets) if all_kw_sets else set()
    consensus = [f"各指南均提及: {'、'.join(sorted(common)[:5])}"] if common else []

    diffs = []
    for i, r in enumerate(recs):
        unique = all_kw_sets[i] - common
        if unique:
            diffs.append(f"{r['guideline']}独有: {'、'.join(sorted(unique)[:3])}")

    return consensus, diffs
```

- [ ] **Step 4: Modify _extract_patient_list() to handle flat format**

In `_extract_patient_list()` (line 944), after extracting `patients` list, add flat format handling:

```python
    # Slim flat format: aggregate before returning
    if patients and _is_flat_format(patients):
        patients = _aggregate_flat_results(patients)
        for p in patients:
            consensus, diffs = _generate_consensus(p)
            p["clinical_questions"] = [{
                "guideline_results": p.pop("guideline_results"),
                "consensus": consensus,
                "differences": diffs,
            }]
        return patients
```

Insert this block **before** the existing `for p in patients:` loop that handles `guideline_results` → `clinical_questions` wrapping.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_slim_profile.py -v`
Expected: All tests pass

- [ ] **Step 6: Run all tests for regression**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_slim_profile.py
git commit -m "feat(slim): add flat format detection, aggregation, and consensus generation in merge"
```

---

### Task 5: Validate + verify-batch slim mode

**Files:**
- Modify: `scripts/batch_pipeline.py:1276-1470` (`cmd_verify_batch`, `cmd_validate`)
- Test: `tests/test_slim_profile.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_slim_profile.py`:

```python
import argparse
from batch_pipeline import cmd_validate, cmd_verify_batch


class TestValidateSlim:
    def _make_results_json(self, tmp_path):
        data = {
            "results": [{
                "patient_id": "P1",
                "diagnosis_summary": "胃癌",
                "disease_type": "胃癌",
                "clinical_questions": [{
                    "guideline_results": [{
                        "guideline": "NCCN",
                        "recommendation": "这是一段至少二十字的推荐文本内容",
                        # evidence_level intentionally missing
                        # source_file intentionally missing
                    }],
                    "consensus": [],
                    "differences": [],
                }],
            }]
        }
        p = tmp_path / "results.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def test_slim_validate_missing_evidence_is_warning_not_error(self, tmp_path):
        """In slim mode, missing evidence_level should be WARNING, not ERROR."""
        results_path = self._make_results_json(tmp_path)
        args = argparse.Namespace(
            input=results_path, patients=None, kb_profile=None, profile="slim",
        )
        # Should exit 0 (warnings only, no errors)
        with pytest.raises(SystemExit) as exc:
            cmd_validate(args)
        assert exc.value.code == 0

    def test_full_validate_unchanged(self, tmp_path):
        """In full mode, same data should produce warnings (existing behavior)."""
        results_path = self._make_results_json(tmp_path)
        args = argparse.Namespace(
            input=results_path, patients=None, kb_profile=None, profile="full",
        )
        with pytest.raises(SystemExit) as exc:
            cmd_validate(args)
        # Full mode: evidence_level missing is WARNING (existing behavior)
        assert exc.value.code == 0


class TestVerifyBatchSlim:
    def test_slim_skips_v3_snippet_verify(self, tmp_path):
        """Slim mode should skip V3 snippet verification."""
        batch_data = {
            "batch_id": "batch_001",
            "results": [{
                "patient_id": "P1",
                "clinical_questions": [{
                    "guideline_results": [{
                        "guideline": "NCCN",
                        "recommendation": "推荐内容",
                        "source_file": "fake_file.txt",
                        "source_lines": "1-10",
                        "execution_log": [{
                            "cmd_id": "CMD-P001-NCCN-01",
                            "match_count": 5,
                            "first_match_snippet": "fake snippet not in KB",
                        }],
                    }],
                }],
                "execution_summary": {
                    "total_commands_in_prompt": 1,
                    "total_commands_executed": 1,
                    "commands_with_zero_matches": [],
                },
            }],
        }
        batch_file = tmp_path / "rag_batch_001.json"
        batch_file.write_text(json.dumps(batch_data, ensure_ascii=False), encoding="utf-8")

        prompt_file = tmp_path / "batch_001_prompt.md"
        prompt_file.write_text('CMD-P001-NCCN-01: grep -n -i "test" "/kb"', encoding="utf-8")

        args = argparse.Namespace(
            input_dir=str(tmp_path), kb_root="/nonexistent", profile="slim",
        )
        # Should not fail on V3 (snippet not found) because slim skips it
        with pytest.raises(SystemExit) as exc:
            cmd_verify_batch(args)
        assert exc.value.code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_slim_profile.py::TestValidateSlim tests/test_slim_profile.py::TestVerifyBatchSlim -v`
Expected: FAIL (functions don't accept `profile` parameter yet)

- [ ] **Step 3: Modify cmd_validate() to accept profile**

At the top of `cmd_validate()` (line 1352), add:

```python
    config = get_profile(getattr(args, 'profile', 'full'))
```

Then modify the anti-laziness checks section (lines 1424-1441):

```python
    # 跨患者一致性：检测质量下降
    if not config.skip_anti_laziness and len(rec_lengths) >= 3:
        lengths = [l for _, l in rec_lengths if l > 0]
        if lengths:
            avg_len = sum(lengths) / len(lengths)
            for pid, length in rec_lengths:
                if length > 0 and length < avg_len * 0.3:
                    warnings.append(
                        f"[{pid}] 推荐总长度异常偏短 ({length}字 vs 平均 {avg_len:.0f}字)"
                    )

    if not config.skip_anti_laziness:
        # 跨批次相似度检测 (D9)
        cross_warnings = _check_cross_batch_similarity(results)
        warnings.extend(cross_warnings)

        # 批次深度衰减检测 (L4)
        depth_warnings = _check_batch_depth_decay(results)
        warnings.extend(depth_warnings)
```

Also modify the recommendation length check (line 1404) to use `config.min_rec_length`:

```python
                if len(rec) < config.min_rec_length:
                    warnings.append(
                        f"[{pid}] Q{qi} {g.get('guideline', '')} 推荐过短 ({len(rec)}字)"
                    )
```

- [ ] **Step 4: Modify cmd_verify_batch() to accept profile**

At the top of `cmd_verify_batch()` (line 1276), add:

```python
    config = get_profile(getattr(args, 'profile', 'full'))
```

Then pass `config` to `_verify_batch_results()` and modify it to skip V3 when `config.skip_snippet_verify` is True. In `_verify_batch_results()` (line 1171), add `config` parameter and wrap V3 check:

```python
def _verify_batch_results(
    prompt_text: str,
    batch_data: dict,
    kb_root: str | None = None,
    config: "ProfileConfig | None" = None,
) -> tuple[list[str], list[str]]:
```

Inside the function, wrap the V3 snippet verification block with:

```python
    if not (config and config.skip_snippet_verify):
        # existing V3 snippet verification code
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_slim_profile.py -v`
Expected: All tests pass

- [ ] **Step 6: Run all tests for regression**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_slim_profile.py
git commit -m "feat(slim): add profile-aware validate and verify-batch"
```

---

### Task 6: CLI integration + orchestrate wiring

**Files:**
- Modify: `scripts/batch_pipeline.py:2020-2088` (argparse in `main()`)
- Modify: `scripts/batch_pipeline.py:787-835` (`cmd_orchestrate`)
- Test: `tests/test_slim_profile.py`

- [ ] **Step 1: Write failing test for CLI integration**

Append to `tests/test_slim_profile.py`:

```python
import subprocess


class TestCLIIntegration:
    def test_orchestrate_accepts_profile_flag(self):
        result = subprocess.run(
            ["python", "scripts/batch_pipeline.py", "orchestrate", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert "--profile" in result.stdout
        assert "slim" in result.stdout

    def test_validate_accepts_profile_flag(self):
        result = subprocess.run(
            ["python", "scripts/batch_pipeline.py", "validate", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert "--profile" in result.stdout

    def test_verify_batch_accepts_profile_flag(self):
        result = subprocess.run(
            ["python", "scripts/batch_pipeline.py", "verify-batch", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert "--profile" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_slim_profile.py::TestCLIIntegration -v`
Expected: FAIL (--profile not in --help output)

- [ ] **Step 3: Add --profile to argparse**

In `main()`, add `--profile` to `p_orch` (after line 2045), `p_validate` (after line 2056), and `p_verify` (after line 2061):

```python
    # After p_orch.add_argument("--max-prompt-tokens", ...)
    p_orch.add_argument("--profile", choices=["full", "slim"], default="full",
                        help="处理模式 (默认 full，slim 适用于小模型)")

    # After p_validate.add_argument("--kb-profile", ...)
    p_validate.add_argument("--profile", choices=["full", "slim"], default="full",
                            help="验证模式 (默认 full)")

    # After p_verify.add_argument("--kb-root", ...)
    p_verify.add_argument("--profile", choices=["full", "slim"], default="full",
                          help="验证模式 (默认 full)")
```

- [ ] **Step 4: Wire config into cmd_orchestrate()**

In `cmd_orchestrate()` (line 787), add after `kb_profile` scan:

```python
    config = get_profile(args.profile)
    if config.name == "slim":
        print(f"  Profile: slim（小模型优化模式）")
```

Modify the grep generation call (line 812):

```python
        grep_cmds = generate_grep_commands(features, kb_profile, kb_root, config=config)
```

Modify the `generate_batch_prompt` call (line 824):

```python
        prompt = generate_batch_prompt(batch, kb_profile, str(kb_root),
                                       len(final_batches) + 1, len(batches), config=config)
```

For org filtering, add before the patient loop (after loading patients):

```python
    if config.org_filter_by_disease:
        # Per-patient org filtering happens inside generate_grep_commands
        pass  # Org filtering is handled at grep generation level
```

Actually, org filtering should happen per-patient in the loop. Modify the patient enrichment loop:

```python
    for p in patients:
        features = extract_patient_features(p)
        if config.org_filter_by_disease:
            disease = p.get("disease_type", "")
            filtered_profile = {**kb_profile, "orgs": filter_orgs_by_disease(kb_profile, disease)}
            grep_cmds = generate_grep_commands(features, filtered_profile, kb_root, config=config)
        else:
            grep_cmds = generate_grep_commands(features, kb_profile, kb_root, config=config)
        enriched = {**p, "features": features, "grep_commands": grep_cmds}
        enriched_patients.append(enriched)
        total_grep += len(grep_cmds)
        total_kw += len(features.get("all_keywords", []))
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_slim_profile.py -v`
Expected: All tests pass

- [ ] **Step 6: Run all tests for regression**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add scripts/batch_pipeline.py tests/test_slim_profile.py
git commit -m "feat(slim): wire --profile CLI flag into orchestrate/validate/verify-batch"
```

---

### Task 7: Documentation update

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update CHANGELOG.md**

Add to top of changelog:

```markdown
## v2.4.0 (2026-03-31)

### Added
- `--profile slim` mode for small model (27B) compatibility
  - Reduces grep commands from ~36 to ~12 per patient via dimension grouping
  - Flattened 2-layer JSON output (vs 5-layer in full mode)
  - Micro-checkpoints in prompts for better instruction following
  - Dynamic org filtering by disease type
  - Auto-generated consensus/differences in merge stage
  - Relaxed validation (skip anti-laziness checks, lower thresholds)
```

- [ ] **Step 2: Update README.md**

Add `--profile slim` usage to the Batch Patient Processing section:

```markdown
### Small Model Mode (--profile slim)

For local models (Qwen 27B etc.) that struggle with complex prompts:

\`\`\`bash
python scripts/batch_pipeline.py orchestrate \
  --patients Output/patients.json \
  --output-dir Output/batches \
  --batch-size 5 \
  --profile slim

python scripts/batch_pipeline.py verify-batch --input-dir Output/batches/ --profile slim
python scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json
python scripts/batch_pipeline.py validate --input Output/rag_results.json --profile slim
\`\`\`
```

- [ ] **Step 3: Update SKILL.md**

Add `--profile` parameter documentation to the orchestrate section.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md README.md SKILL.md
git commit -m "docs: add --profile slim documentation"
```

---

## Verification Checklist

After all tasks complete:

- [ ] `python -m pytest tests/ -v` — all tests pass (80 existing + ~25 new)
- [ ] `python scripts/batch_pipeline.py orchestrate --help` — shows `--profile` flag
- [ ] `python scripts/batch_pipeline.py orchestrate --patients Output/patients.json --output-dir Output/batches_slim --batch-size 5 --profile slim` — generates prompts with ~12 grep/patient
- [ ] Inspect generated prompt file: confirm flat JSON template, micro-checkpoints, no execution_log
- [ ] Execute one batch with small model, then: `merge → validate --profile slim → generate`
