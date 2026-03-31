---
name: medical-guidelines-suite
version: 2.4.0
description: |
  Medical Clinical Guidelines Knowledge Suite - Build, Query & Batch Process.

  Three-phase workflow:
  1. BUILD (medical-guidelines-build): Convert PDF/DOCX → extracted/*.txt + generate indices
  2. QUERY (medical-guidelines-rag): Search indices → Generate clinical recommendations
  3. BATCH (medical-guidelines-batch): Excel patient list → RAG retrieval → xlsx/docx/pptx reports

  TRIGGER when: "treatment options", "guideline recommendations", "add new guidelines",
  "build knowledge base", "批量推荐", "batch recommendations", "患者列表"

  INPUT: Clinical questions, guideline files, OR patient Excel files
  OUTPUT: ALWAYS in Simplified Chinese (简体中文)
homepage: https://github.com/fshaan/medical-guidelines-suite
clawdis:
  emoji: "🏥"
  category: "medical"
  requires:
    bins: [grep, python3]
    optional_bins: [pdftotext]
    pip: [openpyxl, python-docx, python-pptx]
  triggers:
    - 指南推荐
    - 治疗方案
    - 临床决策
    - 知识库构建
    - 批量推荐
    - 患者列表
    - guideline recommendations
    - treatment options
    - clinical decision support
    - build knowledge base
    - batch recommendations
    - batch patient analysis
---

# Medical Guidelines Knowledge Suite

## Overview

This suite contains three complementary skills:

| Skill | Phase | Purpose |
|-------|-------|---------|
| medical-guidelines-build | Build | Convert files → Build indices |
| medical-guidelines-rag | Query | Search indices → Generate answers |
| medical-guidelines-batch | Batch | Patient list → RAG retrieval → Reports |

---

# Part 1: Knowledge Base Builder (BUILD Phase)

## Overview

This skill **builds** the knowledge base that the retrieval skill **queries**.

```
┌─────────────────────────────────┐     ┌───────────────────────────────┐
│  medical-guidelines-build       │     │  medical-guidelines-rag       │
│  (This Phase)                   │     │  (Retrieval Phase)           │
│                                  │     │                                │
│  PDF/DOCX → extracted/*.txt      │     │  extracted/*.txt → Search      │
│  + data_structure.md generation  │     │  + data_structure.md → Navigate│
│                                  │     │                                │
│  BUILD PHASE                     │────▶│  QUERY PHASE                   │
└─────────────────────────────────┘     └───────────────────────────────┘
```

---

## 1. Knowledge Base Location Configuration

<HALT_CONSTRAINT>
**Knowledge base location follows same priority as retrieval phase:**
</HARD_CONSTRAINT>

### Location Priority

| Priority | Method | Example |
|----------|--------|---------|
| 1 | User explicit in query | "将这个PDF添加到 `/data/guidelines/`" |
| 2 | Environment variable | `export MEDICAL_GUIDELINES_DIR=/data/guidelines` |
| 3 | Project config | `.claude/settings.json` → `medicalGuidelinesDir` |
| 4 | Convention | `./guidelines/` |
| 5 | Convention | `./knowledge/` |

### Path Variable

Throughout this skill, `$KB_ROOT` refers to the located knowledge base root directory.

---

## 2. Skill Triggers

Invoke this skill when user wants to:

- Add new guidelines to knowledge base
- Process PDF/DOCX guideline files
- Rebuild knowledge base indices
- Update knowledge base structure
- Convert guidelines to searchable format

**Example Triggers**:
- "把这个NCCN指南添加到知识库"
- "处理这个DOCX文件"
- "重新构建知识库索引"
- "添加肺癌指南到 /data/lung-cancer/"

---

## 3. Knowledge Base Structure Requirements

The skill maintains this structure:

```
$KB_ROOT/
├── data_structure.md              # Root index (MUST exist)
├── <Organization>/                # e.g., NCCN, ESMO, CSCO
│   ├── data_structure.md          # Organization index (MUST exist)
│   ├── *.pdf                      # Original PDF files
│   ├── *.docx                     # Original DOCX files
│   └── extracted/                 # Extracted text directory
│       ├── <filename>.txt         # Pre-extracted text (PRIMARY)
│       └── <filename>_tables.txt  # Extracted tables (optional)
└── scripts/                       # Extraction scripts (optional)
    ├── extract_pdf.py
    ├── extract_docx.py
    └── extract_all.py
```

---

## 3.5. Multi-Version Guideline Handling

### Directory Structure Convention

Multiple versions of the same guideline may coexist. Use the following naming convention:

```
<Organization>_<Disease>_<Year>.V<Number>_<Language>.<ext>
```

**Examples**:
- `NCCN_GastricCancer_2026.V1_EN.pdf` - Gastric Cancer V1, 2026
- `NCCN_GastricCancer_2026.V2_EN.pdf` - Gastric Cancer V2, 2026 (Latest)
- `NCCN_GastricCancer_2026.V1_EN.txt` - Extracted text for V1
- `NCCN_GastricCancer_2026.V2_EN.txt` - Extracted text for V2 (Latest)

### Version Selection Priority

When searching/retrieving from guidelines with multiple versions:

| Priority | Rule | Example |
|----------|-------|----------|
| 1 | User explicitly specifies version | "NCCN 胃癌 **2025.V4** 推荐" → Use V4 |
| 2 | File marked as **默认** in data_structure.md | Status column = "默认" |
| 3 | Highest version number | V2 > V1, 2026 > 2025 |

### data_structure.md Version Indexing

In organization-level `data_structure.md`, mark the default version:

```markdown
## 文件清单

| 文件 | 版本 | 状态 | 行数 |
|------|------|------|------|
| NCCN_GastricCancer_2026.V2_EN.txt | 2.2026 | **默认** | 6497 |
| NCCN_GastricCancer_2026.V1_EN.txt | 1.2026 | 历史 | 6501 |
```

### Output Requirements

**MANDATORY**: Always include version number in output table:

```markdown
| 指南 | 版本 | 推荐意见 | 证据等级(原始) | 来源 |
|------|------|---------|--------------|------|
| NCCN | 2.2026 | 曲妥珠单抗+化疗 | Category 1 | GAST-2 |
```

### Version Comparison

When user asks about version differences or when significant changes exist between versions:

```markdown
### 版本差异说明

**NCCN 胃癌指南 2026.V1 → 2026.V2 主要变化**：

| 内容 | V1 (2026.V1) | V2 (2026.V2) |
|------|----------------|----------------|
| HER2+一线治疗 | T+XP/FP | T+XP/FP+FOLFOX (**新增**) |
| Claudin 18.2 | 未提及 | **新增** zolbetuximab 推荐 |
```

**Rules for version comparison**:
- Only provide comparison when user explicitly asks or when recommendation differs significantly
- Use structured table format for clarity
- Highlight additions/deletions clearly
- Note evidence level changes if any

### File Extraction for Multiple Versions

When extracting multiple versions, process all files and maintain version integrity:

```bash
# Extract all files (maintains version in filename)
python scripts/extract_all.py --force

# Verify extraction
ls -la extracted/  # Should show all version files
```

---

## 4. Workflow: Add New Guideline

### Phase 1: Setup

**Step 1.1: Confirm Knowledge Base Location**

```bash
# Check if KB exists
if [ -d "$KB_ROOT" ]; then
    echo "Found knowledge base at: $KB_ROOT"
else
    echo "Knowledge base not found. Creating at: $KB_ROOT"
    mkdir -p "$KB_ROOT"
fi
```

**Step 1.2: Identify Organization**

Ask user for organization name if not clear from file:
- From filename: `NCCN_Gastric_2026.pdf` → NCCN
- From user: "这是ESMO指南"

**Step 1.3: Create Directory Structure**

```bash
mkdir -p "$KB_ROOT/<Organization>/extracted"
```

### Phase 2: File Processing

**Step 2.1: Place Original File**

```bash
# Copy source file to organization directory
cp /path/to/source.pdf "$KB_ROOT/<Organization>/"
```

**Step 2.2: Extract Text**

For PDF files:
```bash
# Using pdftotext (recommended)
pdftotext -layout "$KB_ROOT/<Organization>/<file>.pdf" \
    "$KB_ROOT/<Organization>/extracted/<file>.txt"

# Alternative: Python script
python scripts/extract_pdf.py "$KB_ROOT/<Organization>/<file>.pdf"
```

For DOCX files:
```bash
# Using python-docx
python scripts/extract_docx.py "$KB_ROOT/<Organization>/<file>.docx"
```

**Step 2.3: Verify Extraction Quality**

```bash
# Check file size/lines
wc -l "$KB_ROOT/<Organization>/extracted/*.txt"

# Check Chinese encoding (if applicable)
file "$KB_ROOT/<Organization>/extracted/*.txt"
# Should show: UTF-8 Unicode text

# Preview content
head -100 "$KB_ROOT/<Organization>/extracted/<file>.txt"
```

### Phase 3: Index Generation

**Step 3.1: Read Extracted Content, Generate Organization Index**

Read the extracted text file and generate `data_structure.md`:

```markdown
# <Organization> <Disease> 指南

## 指南基本信息

- **机构**: <Full Name>
- **版本**: <Version>
- **发布日期**: <Date>
- **语言**: <Language>
- **总行数**: <N> 行
- **证据分级**: <Grading System>

## 文件清单

| 文件 | 类型 | 行数 | 说明 |
|------|------|------|------|
| extracted/<name>.txt | 纯文本 | N | 预提取文本（首选） |
| <name>.pdf | PDF | — | 原始文件 |

## 章节结构

### 诊断与分期
| 章节 | 行数范围 | 内容摘要 |
|------|---------|---------|
| 诊断流程 | 1-50 | ... |
| 分期标准 | 51-120 | ... |

### 治疗
| 章节 | 行数范围 | 内容摘要 |
|------|---------|---------|
| 围手术期治疗 | 200-350 | ... |
| 晚期一线治疗 | 351-500 | ... |

## 证据等级体系

<Describe the grading system used by this organization>

## 常用检索关键词

### 诊断相关
- diagnosis, 诊断
- staging, 分期
- biomarker, 生物标志物
- HER2, PD-L1, MSI-H

### 治疗相关
- surgery, 手术
- chemotherapy, 化疗
- targeted therapy, 靶向治疗
- immunotherapy, 免疫治疗

---

*最后更新: <DATE>*
```

**Step 3.2: Update Root Index**

Read existing `$KB_ROOT/data_structure.md` and add new organization entry.

If root index doesn't exist, create it:

```markdown
# 临床指南知识库总览

本目录包含 X 个组织的 Y 份临床指南文件。

## 指南目录

### <Organization1>/
- **机构**: <Full Name>
- **版本**: <Version>
- **语言**: <Language>
- **证据分级**: <Grading System>
- **特点**: <Key Features>
- **适用**: <Use Cases>

### <Organization2>/
...

---

## 证据等级跨指南对照表

| 指南 | 最高推荐 | 高推荐 | 中等推荐 | 低推荐 |
|------|---------|--------|---------|--------|
| <Org1> | ... | ... | ... | ... |
| <Org2> | ... | ... | ... | ... |

---

## 常见临床问题 → 指南映射

| 临床问题类别 | 首选指南 | 补充参考 |
|-------------|---------|---------|
| ... | ... | ... |

---

*最后更新: <DATE>*
```

### Phase 4: Verification

**Step 4.1: Verify File Structure**

```bash
# Check required files exist
ls -la "$KB_ROOT/data_structure.md"
ls -la "$KB_ROOT/<Organization>/data_structure.md"
ls -la "$KB_ROOT/<Organization>/extracted/*.txt"
```

**Step 4.2: Test Searchability**

```bash
# Quick keyword test
grep -n "treatment" "$KB_ROOT/<Organization>/extracted/*.txt" | head -5
grep -n "推荐" "$KB_ROOT/<Organization>/extracted/*.txt" | head -5
```

---

## 5. Batch Processing

### Process Multiple Files

```bash
# Extract all PDFs in a directory
for pdf in "$KB_ROOT"/*/*.pdf; do
    python scripts/extract_pdf.py "$pdf"
done

# Extract all DOCX files
for docx in "$KB_ROOT"/*/*.docx; do
    python scripts/extract_docx.py "$docx"
done

# Or use the batch script
python scripts/extract_all.py --force
```

### Rebuild All Indices

When significant content changes:
1. Re-read all extracted text
2. Regenerate all `data_structure.md` files
3. Update root index

---

## 6. Quality Checklist

Before marking knowledge base as ready:

- [ ] Root `$KB_ROOT/data_structure.md` exists and is complete
- [ ] Each organization has `$KB_ROOT/<org>/data_structure.md`
- [ ] All source files have corresponding `extracted/*.txt`
- [ ] Chinese content uses UTF-8 encoding
- [ ] Table content is readable in extracted text
- [ ] Page number markers present (if PDF had them)
- [ ] Chapter line ranges are accurate
- [ ] Keywords cover both Chinese and English terms

---

## 7. Error Handling

### PDF Extraction Issues

| Problem | Solution |
|---------|----------|
| Chinese garbled | Verify poppler compiled with CJK support |
| Tables broken | Try pdfplumber instead of pdftotext |
| Missing content | Check if PDF is scanned (needs OCR) |
| Wrong encoding | Re-extract with `--force` |

### DOCX Extraction Issues

| Problem | Solution |
|---------|----------|
| Tables unreadable | Check python-docx table parsing |
| Missing headings | Verify style detection |
| Formatting lost | Expected - focus on content |

---

## 8. Output Format

After successful processing, report to user:

```markdown
## 知识库构建完成

### 处理结果

| 项目 | 状态 |
|------|------|
| 知识库位置 | `$KB_ROOT` |
| 新增组织 | <Organization> |
| 原始文件 | <filename>.pdf (N KB) |
| 提取文件 | extracted/<filename>.txt (M 行) |
| 索引文件 | data_structure.md ✓ |

### 目录结构

```
$KB_ROOT/
├── data_structure.md
└── <Organization>/
    ├── data_structure.md
    ├── <filename>.pdf
    └── extracted/
        └── <filename>.txt (M 行)
```

### 验证测试

```bash
# 搜索测试
grep -n "关键词" $KB_ROOT/<Organization>/extracted/*.txt
# 输出: 找到 N 处匹配
```

### 下一步

知识库已就绪，可使用检索技能进行查询。
```

---

## 9. File Processing References

- `references/pdf_extraction.md` - PDF extraction detailed methods
- `references/docx_extraction.md` - DOCX extraction detailed methods
- `references/index_generation.md` - data_structure.md templates

---

# Part 2: Knowledge Retrieval (QUERY Phase)

## CRITICAL RULE: OUTPUT LANGUAGE

<HALT_CONSTRAINT>
**ALL output to the user MUST be in Simplified Chinese (简体中文).**

This rule applies REGARDLESS of:
- The language of the source guidelines (English PDFs, Chinese DOCX, etc.)
- The language of the user's query
- The language of the extracted text files

**Examples**:
- Source: "Trastuzumab should be added to first-line chemotherapy" (English)
- Output: "曲妥珠单抗应联合一线化疗使用" (Chinese)

- Source: "Category 1 recommendation" (English)
- Output: "Category 1 推荐" (Chinese with original grade preserved)

**Rationale**: The end users are Chinese-speaking clinicians who need information in their native language for clinical decision-making.
</HARD_CONSTRAINT>

---

## 1. Knowledge Base Directory Configuration

<HALT_CONSTRAINT>
**Knowledge base location is USER-CONFIGURABLE.**

The skill does NOT assume a fixed directory. Instead, it dynamically locates the knowledge base using the following priority order:
</HARD_CONSTRAINT>

### Knowledge Base Location Priority

**Order of detection (highest priority first)**:

| Priority | Method | Description | Example |
|----------|--------|-------------|---------|
| 1 | **User Explicit** | User specifies path in query | "在 `/data/oncology/` 目录中搜索肺癌指南" |
| 2 | **Environment Variable** | `MEDICAL_GUIDELINES_DIR` set | `export MEDICAL_GUIDELINES_DIR=/data/guidelines` |
| 3 | **Project Config** | `.claude/settings.json` | `{"medicalGuidelinesDir": "/data/guidelines"}` |
| 4 | **Convention** | `guidelines/` in project root | `<project>/guidelines/` |
| 5 | **Convention** | `knowledge/` in project root | `<project>/knowledge/` |

### Configuration Examples

**Option 1: Environment Variable (Recommended for flexibility)**
```bash
# In ~/.zshrc or ~/.bashrc
export MEDICAL_GUIDELINES_DIR="/Users/username/data/medical-guidelines"

# Multiple knowledge bases (colon-separated, like PATH)
export MEDICAL_GUIDELINES_DIR="/data/gastric-cancer:/data/lung-cancer"
```

**Option 2: Project Configuration**
```json
// .claude/settings.json
{
  "medicalGuidelinesDir": "/Users/username/data/medical-guidelines"
}
```

**Option 3: Convention-Based (Simplest)**
```
<your-project>/
├── guidelines/          # or knowledge/
│   ├── data_structure.md
│   ├── NCCN/
│   ├── ESMO/
│   └── ...
└── (other files)
```

**Option 4: Specify in Query**
```
User: "在 /data/my-guidelines/ 目录中，搜索HER2阳性胃癌的治疗推荐"
```

### Required Knowledge Base Structure

Regardless of location, the knowledge base MUST have this structure:

```
<KNOWLEDGE_BASE_ROOT>/              # User-configurable location
├── data_structure.md               # Root index (MUST READ FIRST) - REQUIRED
├── <Organization1>/                # e.g., NCCN, ESMO, CSCO
│   ├── data_structure.md           # Organization index - REQUIRED
│   ├── *.pdf / *.docx              # Original files
│   └── extracted/*.txt             # Pre-extracted text (PRIMARY SOURCE)
├── <Organization2>/
│   ├── data_structure.md
│   └── extracted/*.txt
└── ...
```

**Required Files**:
- `<ROOT>/data_structure.md` — Root index file (MUST exist)
- `<ROOT>/<ORG>/data_structure.md` — Organization index file (MUST exist for each org)
- `<ROOT>/<ORG>/extracted/*.txt` — Pre-extracted text files (MUST exist)

### Dynamic Domain Detection

The knowledge base domain is determined by its contents. Before searching:
1. Locate knowledge base root using priority order above
2. Read `<ROOT>/data_structure.md` to understand available guidelines
3. Identify the medical specialty/disease area covered
4. Adapt clinical question understanding accordingly

---

## 2. CRITICAL PRINCIPLE: LEARN BEFORE PROCESSING

<HALT_CONSTRAINT>

### Mandatory Pre-Processing Checklist

Before processing ANY PDF or DOCX file, you MUST:

- [ ] Have read `references/pdf_reading.md` (when processing PDFs)
- [ ] Have read `references/docx_reading.md` (when processing DOCX)
- [ ] Have confirmed `extracted/` directory contains pre-extracted text
- [ ] Use `extracted/*.txt` as PRIMARY source, NOT original files

**Violation of this checklist is a protocol breach.**

### Forbidden Actions List

**NEVER**:
- Fabricate drug dosages, regimens, or evidence levels
- Guess guideline content instead of actually searching
- Use web search as a substitute for knowledge base retrieval
- Skip reading `data_structure.md` before searching
- Read entire files (use grep for positioning + Read for local context)
- Ignore evidence level differences across different grading systems
- Mix evidence grading systems (e.g., don't report "Category 1" as "I级推荐")

### Medical-Specific Rules

**MANDATORY**:
- All treatment recommendations MUST cite source guideline and original evidence level
- Drug dosages MUST be exact — NO rounding
- Regimen abbreviations MUST be explained
- When translating English content to Chinese, preserve original evidence level terminology

</HARD_CONSTRAINT>

---

## 3. Clinical Question Understanding Framework

### Decompose Clinical Questions

Break down user queries into dimensions appropriate for the domain:

| Dimension | Examples (Oncology) | Examples (Cardiology) |
|-----------|---------------------|----------------------|
| Disease Stage | Early/locally advanced/metastatic | Acute/chronic, NYHA class |
| Treatment Modality | Surgery/chemotherapy/targeted/radiation | Medical/PCI/surgical |
| Patient Characteristics | Biomarker status, PS score | Risk factors, comorbidities |

### Generate Bilingual Keywords

Generate both Chinese and English search terms:

```
Clinical Question: "HER2阳性晚期胃癌一线治疗"

Keywords:
- HER2 positive, HER2阳性
- advanced gastric cancer, 晚期胃癌
- first-line therapy, 一线治疗
- trastuzumab, 曲妥珠单抗
```

### Clinical Question Category → Guideline Mapping

The mapping between clinical question categories and primary guidelines depends on:
1. Guidelines available in the knowledge base (read `data_structure.md`)
2. Domain-specific expertise (e.g., surgical guidelines for surgery questions)
3. Regional considerations (e.g., CSCO for China drug accessibility)

**General Principle**:
- International guidelines (NCCN, ESMO): High-quality evidence, global perspective
- Regional guidelines (CSCO, CACA, Japanese, Korean): Local drug availability, regional practice patterns

---

## 4. Cross-Guideline Evidence Level Comparison

Different guidelines use different grading systems. Agent output MUST report the **ORIGINAL evidence level**:

### Common Evidence Grading Systems

| Guideline/Organization | Highest | High | Moderate | Low/Optional |
|------------------------|---------|------|----------|--------------|
| NCCN | Category 1 | Category 2A | Category 2B | Category 3 |
| ESMO | I,A | II,B | III,C | IV,D / V,E |
| CSCO (China) | I级推荐(1A) | I级推荐(1B/2A) | II级推荐 | III级推荐 |
| GRADE | Strong | — | Weak | — |
| Japanese | Strong | Weak | — | — |

### Detailed System Explanations

**NCCN Categories**:
- Category 1: Based on high-level evidence, uniform NCCN consensus
- Category 2A: Based on lower-level evidence, uniform NCCN consensus (default)
- Category 2B: Based on lower-level evidence, non-uniform NCCN consensus
- Category 3: Based on any level evidence, major disagreement exists

**ESMO Levels**:
- I/A: High-level evidence, strong recommendation
- II/B: Moderate evidence, recommended
- III/C: Low-level evidence, optional

**CSCO Recommendation Grades**:
- I级推荐 (Grade I): High evidence + high accessibility (insurance coverage)
- II级推荐 (Grade II): High evidence but lower accessibility
- III级推荐 (Grade III): Clinically useful but limited evidence

**IMPORTANT**: When reporting in Chinese output:
- Keep NCCN terms as "Category 1/2A/2B/3" (do NOT translate)
- Keep ESMO terms as "I,A" / "II,B" etc.
- Keep CSCO terms in original Chinese

---

## 5. Overall Search Workflow

### Step 1: Locate Knowledge Base + Understand Clinical Question

**First: Locate Knowledge Base Root**

Follow the priority order in Section 1:
1. Check user-specified path (in query)
2. Check `$MEDICAL_GUIDELINES_DIR` environment variable
3. Check `.claude/settings.json` → `medicalGuidelinesDir`
4. Check `./guidelines/` directory
5. Check `./knowledge/` directory
6. If not found → Ask user for location

**Then: Understand Domain + Question**

- Read `<ROOT>/data_structure.md` to understand available guidelines
- Identify the medical domain covered
- Decompose clinical question into relevant dimensions
- Generate bilingual keywords (3-8 terms)

**Set Variable for Subsequent Steps**:
```bash
# Store located path for reference
KB_ROOT="<located_path>"  # e.g., /path/to/guidelines or /path/to/knowledge
```

### Step 2: Read Root `data_structure.md`, Select Relevant Guidelines

- Read `$KB_ROOT/data_structure.md`
- Select 1-3 most relevant guidelines based on question category
- Consider guideline expertise areas (surgical vs. medical vs. supportive)

### Step 3: Read Target Guideline's `data_structure.md`, Locate Chapters

- Read `$KB_ROOT/<organization>/data_structure.md`
- Identify target chapters and approximate line ranges
- Confirm search keywords

### Step 4: Learn File Processing Methods (MANDATORY)

- Processing PDF: Read `references/pdf_reading.md`
- Processing DOCX: Read `references/docx_reading.md`
- Confirm using `extracted/*.txt`

### Step 5: Execute grep Search + Local Read

```bash
# Example: Search for targeted therapy
grep -n "HER2" $KB_ROOT/NCCN/extracted/*.txt
grep -n "trastuzumab" $KB_ROOT/ESMO/extracted/*.txt
```

- Use `Read` to retrieve content around matching lines (±20-50 lines)
- Extract specific recommendations, evidence levels, sources

### Step 6: Cross-Guideline Synthesis, Generate Comparison Table

Aggregate all search results and generate structured output.

---

## 6. General Search Principles

### Keyword Strategy

- Use 3-8 keywords per search
- Include Chinese and English synonyms
- Include medical abbreviations (HER2, PD-L1, MSI-H, etc.)
- Include drug/regimen names

### grep Basic Principles

```bash
# Basic search
grep -n "keyword" $KB_ROOT/*/extracted/*.txt

# Multiple keywords (OR)
grep -n -E "keyword1|keyword2" $KB_ROOT/NCCN/extracted/*.txt

# Case-insensitive
grep -n -i "keyword" $KB_ROOT/*/extracted/*.txt
```

### Multi-Round Iteration Mechanism

If first-round search results are unsatisfactory:

1. **Expand keywords**: Add synonyms, related terms
2. **Adjust scope**: Expand/shrink line range
3. **Switch guidelines**: Try other relevant guidelines
4. **Check chapters**: Re-read `data_structure.md`

**Maximum 5 iterations**. If still no results, clearly inform the user.

---

## 7. File Type Strategies

### Markdown/Text Files

For `extracted/*.txt`:

1. Use `grep` to locate keywords and line numbers
2. Use `Read` to retrieve ±20-50 lines around matches
3. Extract key information

### PDF Files

**MANDATORY prerequisite**: Read `references/pdf_reading.md`

Processing workflow:
1. Confirm `extracted/*.txt` exists
2. Search `extracted/*.txt` FIRST
3. Reference original PDF only when precise page numbers needed

### DOCX Files

**MANDATORY prerequisite**: Read `references/docx_reading.md`

Processing workflow:
1. Confirm `extracted/*.txt` exists
2. Search `extracted/*.txt`
3. Note table recommendation formats

---

## 8. Output Format Specification

### Standard Output Format (IN CHINESE)

```markdown
## [临床问题]

### 各指南推荐对比

| 指南 | 版本 | 推荐意见 | 证据等级(原始) | 来源章节/页码 |
|------|------|---------|--------------|-------------|
| NCCN | 2026.V1 | [具体推荐内容] | Category 1 | [章节名, p.XX] |
| ESMO | 2024 | [具体推荐内容] | I,A | [章节名] |
| CSCO | 2025 | [具体推荐内容] | I级推荐(1A) | [章节名] |
| ... | ... | ... | ... | ... |

### 指南间共识与差异

**共识点**:
- [关键共识1]
- [关键共识2]

**主要差异**:
- [差异1及原因]
- [差异2及原因]

### 信息来源

- NCCN: $KB_ROOT/NCCN/extracted/[filename].txt 第 XX-YY 行
- ESMO: $KB_ROOT/ESMO/extracted/[filename].txt 第 XX-YY 行
- CSCO: $KB_ROOT/CSCO/extracted/[filename].txt 第 XX-YY 行
```

---

## 9. Response Style and Error Handling

### Response Style

- **ALWAYS in Simplified Chinese**: All user-facing content must be in Chinese
- **Conclusion first, evidence second**: State conclusions before listing evidence
- **Cite sources**: All information must be traceable
- **Stay objective**: Present each guideline's perspective without bias

### When Information is Insufficient

Clearly state this — DO NOT FABRICATE:

```markdown
### 信息不足声明

当前知识库中未找到以下信息：
- [具体缺失内容]

建议：
- [补充参考来源]
- [进一步检索建议]
```

### Forbidden Actions

- NEVER use web search as substitute for knowledge base retrieval
- NEVER fabricate drug dosages or regimens
- NEVER omit evidence levels
- NEVER mix different guideline grading systems

---

## 10. Example Scenarios

### Scenario 1: Oncology - Biomarker-Targeted Therapy

**User Question**: "HER2阳性晚期胃癌一线治疗，各指南推荐什么方案？"

**Execution Flow**:

1. Read `guidelines/data_structure.md` → Identify gastric cancer guidelines
2. Select NCCN, ESMO, CSCO (primary guidelines for systemic therapy)
3. Read each guideline's `data_structure.md`
4. Search keywords: "HER2 positive", "trastuzumab", "first-line"
5. Aggregate and generate comparison table

**Expected Output (in Chinese)**:
- NCCN: 曲妥珠单抗联合化疗 (Category 1)
- ESMO: 曲妥珠单抗联合化疗 [I,A; ESCAT: I-A]
- CSCO: 曲妥珠单抗联合化疗 (I级推荐, 1A类)

### Scenario 2: Surgical Approach Selection

**User Question**: "可切除胃癌的淋巴结清扫范围推荐？"

**Search Keywords**:
- lymphadenectomy, 淋巴结清扫
- D1/D2, lymph node dissection
- gastrectomy, 胃切除术

### Scenario 3: Extending to New Domains

When adding guidelines from a new medical domain:

1. Place guideline files in `guidelines/<organization>/`
2. Run extraction scripts: `python scripts/extract_all.py --force`
3. Create `guidelines/<organization>/data_structure.md`
4. Update root `guidelines/data_structure.md` index
5. Skill will automatically adapt to new domain content

---

## 11. File Processing References

- `references/pdf_reading.md` - PDF processing guide for retrieval phase
- `references/docx_reading.md` - DOCX processing guide for retrieval phase
- `references/input_format.md` - Batch input Excel format specification
- `templates/data_structure_root.md` - Root index template
- `templates/data_structure_org.md` - Organization index template
- `templates/report_template.pptx` - PowerPoint report template with branded layouts

---

# Part 3: Batch Patient Processing (BATCH Phase)

## Overview

```
┌────────────────────────────┐     ┌─────────────────────────┐     ┌──────────────────────────────┐
│  medical-guidelines-build  │     │  medical-guidelines-rag  │     │  medical-guidelines-batch     │
│  (Build Phase)             │     │  (Query Phase)           │     │  (Batch Phase)               │
│                            │     │                          │     │                              │
│  PDF/DOCX → extracted/     │     │  Question → grep Search  │     │  Excel → Parse → RAG Loop    │
│  + data_structure.md       │────▶│  → Comparison Table      │────▶│  → xlsx + docx + pptx        │
│                            │     │                          │     │                              │
└────────────────────────────┘     └─────────────────────────┘     └──────────────────────────────┘
```

This phase takes a batch of patients from an Excel file, auto-infers clinical questions for each,
orchestrates RAG retrieval across ALL relevant guidelines, and generates 3 output deliverables.

---

## 1. Output Language

<HARD_CONSTRAINT>
ALL output MUST be in Simplified Chinese (简体中文), regardless of source guideline language.
Preserve original evidence level terminology: "Category 1" stays as-is, "I,A" stays as-is.
</HARD_CONSTRAINT>

---

## 2. Input Parsing

Two input formats are supported — user provides **one** Excel file.

```bash
python scripts/batch_pipeline.py parse --input <xlsx_path> --output Output/patients.json
```

The script auto-detects format based on column headers:

| Format | Detection | Use Case |
|--------|-----------|----------|
| Structured (26 cols) | Contains "患者ID号" + ≥10 cols | HIS/CRF exports |
| Narrative (3 cols) | Contains "病情总结" + ≤5 cols | Clinician summaries |

See `references/input_format.md` for complete column specifications.

---

## 3. Clinical Question Auto-Inference

For each patient, Claude analyzes their clinical profile and generates 1-3 targeted questions:

### Step 0: Disease Diagnosis (MANDATORY)

Disease type is determined by **tumor site + pathology type** together:

| Tumor Site | Pathology | Disease | Guideline Scope |
|-----------|-----------|---------|-----------------|
| 胃(U/M/L) | 腺癌/印戒细胞癌 | 胃癌 | NCCN胃癌, ESMO, CSCO胃癌, Japanese, Korean, CACA |
| EGJ | 腺癌 | EGJ腺癌 | NCCN食管+EGJ, NCCN胃癌, ESMO, CSCO胃癌, all Asian |
| EGJ | 鳞癌 | 食管癌 | NCCN食管癌+EGJ, CSCO食管癌 |
| 结肠 | 腺癌 | 结肠癌 | NCCN结肠癌, CSCO结直肠癌 |
| 直肠 | 腺癌 | 直肠癌 | NCCN直肠癌, CSCO结直肠癌 |

### Step 1: Clinical Scenario

| Patient State | Scenario | Question Focus |
|--------------|----------|----------------|
| 初治 + 可切除 | 围手术期 | Neoadjuvant/adjuvant regimens |
| 初治 + M1 | 一线治疗 | First-line systemic therapy |
| 术前治疗后 + PR/SD | 手术评估 | Surgery timing, adjuvant therapy |
| 术前治疗后 + PD | 二线治疗 | Second-line options |

### Step 2: Molecular Modifiers

- **HER2+** → anti-HER2 therapy
- **MSI-H/dMMR** → checkpoint inhibitors
- **pMMR/MSS** → limited immunotherapy benefit
- **PD-L1 CPS** → influences immunotherapy selection

### Question Template

```
"{肿瘤部位}{病理}，{分期描述}，{分子分型}，{既往治疗描述}，{当前治疗决策问题}"
```

---

## 4. RAG Retrieval Strategy

Search **all** guideline organizations relevant to each patient's disease type.

```
For each patient:
  1. Read $KB_ROOT/data_structure.md → available guidelines
  2. From disease diagnosis → identify ALL relevant orgs
  3. For each org:
     a. Read $KB_ROOT/<org>/data_structure.md → target chapters
     b. Generate bilingual keywords (3-8 terms)
     c. grep search → Read ±20-50 lines → extract recommendations
  4. Synthesize consensus + differences
  5. Report progress: "已完成 X/N 位患者检索"
```

---

## 5. Intermediate Data Format

Write results to `Output/rag_results.json`:

```json
{
  "generated_at": "2026-03-19",
  "patient_count": 5,
  "results": [{
    "patient_id": "T001587071",
    "patient_name": "杨永富",
    "primary_site": "胃中部（M）",
    "disease_type": "胃癌",
    "diagnosis_summary": "...",
    "clinical_questions": [{
      "question": "...",
      "guidelines_searched": ["NCCN", "ESMO", "CSCO"],
      "guideline_results": [{
        "guideline": "NCCN",
        "version": "2.2026",
        "recommendation": "...",
        "evidence_level": "Category 1",
        "source_file": "...",
        "source_lines": "2345-2380"
      }],
      "consensus": ["..."],
      "differences": ["..."]
    }]
  }]
}
```

---

## 6. Output Generation

```bash
python scripts/batch_pipeline.py generate --input Output/rag_results.json --format all
```

### Deliverable 1: 批量推荐汇总表.xlsx

Columns: 患者ID → 姓名 → 肿瘤部位 → 诊断摘要 → 临床问题 → **共识点** → **差异点** → {各指南推荐} → 备注

### Deliverable 2: 推荐意见书.docx (per patient)

Landscape orientation. Contains: patient info table, clinical questions, guideline comparison table, consensus/differences analysis, generation date, disclaimer.

Title format: `{姓名}（{ID}）临床指南推荐意见书`

### Deliverable 3: 批量推荐幻灯片.pptx

Uses `templates/report_template.pptx` with branded layouts:

| Slide Type | Template Layout | Content |
|-----------|----------------|---------|
| Cover | Layout 0 (Title Slide) | Title + date + patient count |
| Summary | Layout 1 (Custom) | Patient overview table (auto-paginated at 15 rows) |
| Patient Info | Layout 2 (Title+Content) | Info table + clinical questions |
| Comparison | Layout 3 (1_Title+Content) | Guideline recommendation table (4 cols) |
| Consensus | Layout 4 (Comparison) | Left: consensus / Right: differences |
| Disclaimer | Layout 0 (Title Slide) | Disclaimer text |

**Page formula**: 1(cover) + ceil(N/15)(summary) + 3×N(patients) + 1(disclaimer)

---

## 7. Execution Workflow

### Step 1: Environment Check

```bash
# Verify venv and dependencies
.venv/bin/python3 -c "import openpyxl, docx, pptx; print('OK')"
# Create output directories
mkdir -p Output/reports Output/batches
```

If dependencies are missing, install: `.venv/bin/pip install openpyxl python-docx python-pptx`

### Step 2: Parse Input

```bash
python scripts/batch_pipeline.py parse --input <user_xlsx> --output Output/patients.json
```

Read `Output/patients.json` to confirm patient count N.

<HARD_CONSTRAINT>

### Execution Mode Constraints

1. **禁止 subagent / 并行代理**: 所有批处理步骤在当前会话中顺序执行。
   禁止使用 Agent tool、Task tool 或任何并行机制。
   原因: subagent 无法继承知识库上下文，搜索质量无法保证一致性。

2. **禁止自行编码替代脚本**: 必须使用 scripts/batch_pipeline.py 的子命令。
   原因: 脚本已验证，自行编码引入格式差异和路径错误。

3. **所有输出路径以 orchestration_plan.json 为准**: 不自行决定文件存储位置。

4. **禁止跳过 grep 命令**: orchestrate 生成的命令必须全部执行。
   可以补充额外命令，但不得删减已有的。

</HARD_CONSTRAINT>

<HARD_CONSTRAINT>

### Full-Coverage Retrieval Rules

1. **所有 org 必须检索**: 不得因"已找到足够信息"提前停止。
2. **所有患者临床特征必须纳入检索**: 不仅合并症，转移部位、分子分型、
   分期、治疗史、肿瘤急症等所有非空字段都应体现在检索中。
3. **不得以"类似"跳过**: 即使两位患者病情相似，也独立执行全部命令。
4. **检索深度一致**: 第 1 位和最后 1 位患者的检索深度必须相同。

</HARD_CONSTRAINT>

### Step 3: Execute Retrieval via orchestrate

**所有批处理（无论患者数量）统一使用 orchestrate 驱动。**

```bash
python scripts/batch_pipeline.py orchestrate \
  --patients Output/patients.json --kb-root $KB_ROOT \
  --output-dir Output/batches --batch-size 5 \
  --profile {full,slim}
```

**Parameters**:
- `--profile full` (default): Full retrieval mode with ~36 grep commands per patient, 5-layer JSON output
- `--profile slim`: Small model mode (~12 commands per patient, flattened 2-layer JSON) for 27B-class models that struggle with complex prompts

读取生成的 `Output/batches/orchestration_plan.json`，报告给用户：
- 批次数量、待处理数、已完成数（checkpoint）
- 覆盖的指南组织
- grep 命令总数

**对 pending 批次逐一执行（严格顺序）：**

在开始处理新批次前，先读取该批次的 prompt 文件。prompt 开头的 CONTEXT_RESET 指令要求你从零开始——不引用前批的任何内容。

1. 读取 `batch_NNN_prompt.md`
   — 注意开头的 `<CONTEXT_RESET>` 指令，清除前批上下文
2. 按 prompt 中的 grep 命令逐条执行（不得跳过），每条命令标记为 `CMD-P{n}-{org}-{seq}`
3. 每条 grep 执行后，记录 `cmd_id`、`match_count`、`first_match_snippet`（≥30字）到 `execution_log`
4. 可补充额外 grep（基于已有结果中的线索），补充命令不需要 CMD-ID
5. 提取推荐、证据等级、来源信息
6. 生成共识/差异分析
7. 写入 `rag_batch_NNN.json`（必须包含 `execution_log` 和 `execution_summary`）
8. 报告进度: "已完成第 NNN 批（X/M 批），共 Y/N 位患者"

完成所有批次后，先验证执行证据再合并：

```bash
python scripts/batch_pipeline.py verify-batch \
  --input-dir Output/batches/ --kb-root $KB_ROOT
```

如果 verify-batch 报 FAIL：
- 提示用户哪些批次需要重新执行
- 删除失败批次的 `rag_batch_NNN.json`
- 重新读取对应的 `batch_NNN_prompt.md` 并从零执行
- 重新运行 verify-batch 确认通过

verify-batch 全部 PASS 后，执行 `orchestration_plan.json` 中 `next_steps` 的命令。

<HARD_CONSTRAINT>

### Batch Context Isolation Rules

When processing batches, the following rules are **mandatory** to prevent quality degradation:

1. **No cross-batch references**: Never reference, quote, or summarize results from previous batches when processing a new batch. Each batch starts fresh.
2. **No shorthand**: Never use phrases like "与前面患者类似", "同上", "参考前述" — every patient gets independently inferred clinical questions and independently executed grep searches.
3. **Re-read the root index**: Read `$KB_ROOT/data_structure.md` at the start of **every** batch, not just the first one.
4. **Write then release**: After writing `rag_batch_NNN.json`, that batch's data is no longer needed for subsequent batches. Do not carry it forward in reasoning.
5. **Equal depth**: Later batches must receive the same search depth (number of keywords, number of guidelines checked, context lines read) as earlier batches.
6. **Record execution evidence**: Every grep command (CMD-*) must have its match_count and first_match_snippet recorded in the execution_log of the corresponding guideline_results entry.

</HARD_CONSTRAINT>

### Step 4: Merge and Validate

```bash
# Merge all batch results into unified rag_results.json
python scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json

# Validate completeness and quality
python scripts/batch_pipeline.py validate --input Output/rag_results.json --patients Output/patients.json
```

If validate reports errors (missing patients, empty fields), inform the user and suggest re-running specific batches. If validate reports only warnings (short recommendations, missing consensus), note them but proceed.

### Step 5: Generate Outputs

```bash
python scripts/batch_pipeline.py generate --input Output/rag_results.json --format all
```

### Step 6: Verify and Report

Confirm all output files exist and report completion:

```markdown
## 批量推荐完成

| 项目 | 状态 |
|------|------|
| 患者总数 | N |
| 处理模式 | 直接模式 / 分批模式（M 批） |
| 成功检索 | K |
| 汇总表 | Output/批量推荐汇总表.xlsx ✓ |
| 推荐意见书 | Output/reports/ (K 份) ✓ |
| 幻灯片 | Output/批量推荐幻灯片.pptx ✓ |
| 质量验证 | ✓ 通过 / ⚠ N 个警告 |
```

### Checkpoint Recovery

When batch processing is interrupted (network failure, session timeout, etc.), the user can resume by re-triggering the batch skill:

1. Detect existing `Output/batches/rag_batch_*.json` checkpoint files
2. Validate each checkpoint (valid JSON + non-empty `results` + patient IDs match the batch file)
3. Skip validated batches
4. Resume from the next incomplete batch
5. After all batches complete, run merge + validate as normal

Report on resume: "检测到已完成 X/M 批（Y 位患者），从第 Z 批继续处理。"

---

## 8. Forbidden Actions

- **Never fabricate** drug dosages, regimens, or evidence levels
- **Never use web search** as substitute for knowledge base retrieval
- **Never skip** reading `data_structure.md` before searching
- **Never mix** evidence grading systems across guidelines
- **Never silently limit** guideline search scope
- **Never spawn subagents** for batch processing
- **Never write custom scripts** to replace batch_pipeline.py
- **Never process batches in parallel**
- **Never store output** outside orchestration_plan.json 指定的目录
- **Never skip any grep command** from the prompt
- **Never skip any org's** search results
- **Never ignore patient clinical features** in search
- **Never omit execution_log** from guideline_results output
- **Never omit execution_summary** from patient results
- **Never fabricate first_match_snippet** — it must be the actual text from grep output

---

*Last Updated: 2026-03-27*
