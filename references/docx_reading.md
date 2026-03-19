# DOCX File Processing Guide

## Overview

This guide explains how to process medical clinical guideline DOCX files. DOCX format is commonly used by some guideline organizations (e.g., CSCO in China).

## Priority: Use Pre-Extracted Text

**IMPORTANT**: All guideline DOCX files have been pre-extracted to plain text:

```
$KB_ROOT/
├── <Organization>/
│   ├── *.docx                   # Original DOCX
│   └── extracted/
│       └── *.txt                # Pre-extracted text (PREFERRED)
```

## python-docx Extraction Features

Pre-extraction process uses `python-docx` library and preserves:

### Preserved Structure

1. **Heading Hierarchy** - Marked using Markdown format
   ```
   # Level 1 Heading
   ## Level 2 Heading
   ### Level 3 Heading
   ```

2. **Tables** - Converted to Markdown table format
   ```
   | Column 1 | Column 2 | Column 3 |
   |----------|----------|----------|
   | Content  | Content  | Content  |
   ```

3. **Paragraphs** - Original paragraph structure maintained

## CSCO-Style Recommendation Tables

Some guidelines (especially CSCO) use tabular recommendation format:

### Recommendation Level Tables

Typical recommendation table structure:

| Clinical Question | I级推荐 (Grade I) | II级推荐 (Grade II) | III级推荐 (Grade III) |
|------------------|------------------|--------------------|-----------------------|
| ... | ... | ... | ... |

### Search Strategy

1. **Locate Tables**: Search for table titles or recommendation grade keywords
2. **Read Tables**: Use `Read` to retrieve content around matching lines
3. **Understand Context**: Combine with `data_structure.md` to understand table meaning

## Search Examples

### Example 1: Search Specific Topic

```bash
grep -n "topic_keyword" $KB_ROOT/<org>/extracted/*.txt
```

### Example 2: Search Recommendation Grades

```bash
grep -n "I级推荐" $KB_ROOT/CSCO/extracted/*.txt
```

### Example 3: Search Specific Drug

```bash
grep -n "drug_name" $KB_ROOT/*/extracted/*.txt
```

## Evidence Level Interpretation (CSCO Example)

CSCO guidelines use a dual grading system:

### Recommendation Grades

- **I级推荐 (Grade I)**: High evidence + high accessibility (insurance coverage)
- **II级推荐 (Grade II)**: High evidence but lower accessibility
- **III级推荐 (Grade III)**: Clinically useful but limited evidence

### Evidence Categories

| Category | Definition |
|----------|------------|
| 1A | Multicenter large-sample RCTs |
| 1B | Small-sample RCTs or high-quality non-RCTs |
| 2A | Retrospective or prospective non-RCTs |
| 2B | Case reports or retrospective studies |
| 3 | Expert consensus or clinical experience |

**IMPORTANT**: When outputting to users:
- Preserve original terminology like "I级推荐(1A类)"
- Do NOT translate to other grading systems like "Category 1" or "Level I,A"

## Processing Recommendations

1. **Prioritize Pre-Extracted Text**: Avoid re-processing DOCX
2. **Focus on Recommendation Grades**: Pay attention to highest-level recommendations
3. **Consider Regional Factors**: Account for local drug accessibility and guidelines
4. **Cross-Validation**: Compare with other guidelines when available

## Output Language Reminder

<HARD_CONSTRAINT>
When outputting search results to users, ALL content MUST be in Simplified Chinese (简体中文).

For guidelines already in Chinese, maintain original terminology:
- Keep "I级推荐", "II级推荐", "III级推荐"
- Keep "1A类", "1B类", "2A类", "2B类"
- Do NOT translate to other grading systems
</HARD_CONSTRAINT>

---

*Last Updated: 2026-03-04*
