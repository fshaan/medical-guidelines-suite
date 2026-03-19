# PDF File Processing Guide

## Overview

This guide explains how to process medical clinical guideline PDF files. Medical guideline PDFs typically contain complex tables, decision tree flowcharts, and multi-column layouts.

## Priority: Use Pre-Extracted Text

**IMPORTANT**: All guideline PDFs have been pre-extracted to plain text files in the `extracted/` directory.

```
$KB_ROOT/
├── <Organization>/
│   ├── *.pdf                    # Original PDF
│   └── extracted/
│       └── *.txt                # Pre-extracted text (PREFERRED)
```

**Recommended Workflow**:
1. First use `grep` to search `extracted/*.txt` files
2. Use `Read` tool to retrieve content around matching lines
3. Only reference original PDFs when precise page numbers are needed

## Chinese PDF Encoding Handling

Chinese medical guidelines (e.g., CSCO, CACA) use UTF-8 encoding:

```bash
# Check encoding
file $KB_ROOT/CSCO/extracted/*.txt
# Output: UTF-8 Unicode text

# grep search for Chinese content
grep -n "关键词" $KB_ROOT/CSCO/extracted/*.txt
```

## Table Processing

Key recommendations in medical guidelines are often presented in table format:

### Common Table Types

1. **Treatment Recommendation Tables** - Contain regimens, dosages, cycles
2. **Staging Tables** - TNM staging, classification systems
3. **Evidence Level Tables** - Recommendation strength and evidence grades
4. **Diagnostic Criteria Tables** - Criteria for diagnosis

### Table Search Strategy

Table content may lose structure in plain text extraction. Recommended approach:

1. **Keyword Location**: Search for table titles or key terms
2. **Context Reading**: Read text before/after tables to understand meaning
3. **Cross-Validation**: Combine with `data_structure.md` chapter index

## Flowchart Processing

Many guidelines (e.g., NCCN) heavily use decision tree flowcharts:

### Flowchart Characteristics

- Branch decision points (e.g., "Resectable?" → Yes/No)
- Treatment pathway selection
- Conditional logic

### Processing Recommendations

1. Flowcharts may be incomplete in plain text
2. Search for flowchart titles and keywords
3. Reference `data_structure.md` chapter descriptions

## Page Number Location

If page number markers are present in text:

```
--- Page 15 ---
```

Use this marker to locate corresponding pages in original PDF.

## Search Examples

### Example 1: Search Across All Guidelines

```bash
# Search across all guidelines
grep -rn "keyword" $KB_ROOT/*/extracted/*.txt

# Search in specific organization's guidelines
grep -n "keyword" $KB_ROOT/NCCN/extracted/*.txt
```

### Example 2: Search Drug/Regimen Information

```bash
grep -n "drug_name" $KB_ROOT/*/extracted/*.txt
grep -n "regimen" $KB_ROOT/*/extracted/*.txt
```

### Example 3: Search Evidence Levels

```bash
grep -n "Category 1" $KB_ROOT/NCCN/extracted/*.txt
grep -n "I,A" $KB_ROOT/ESMO/extracted/*.txt
grep -n "I级推荐" $KB_ROOT/CSCO/extracted/*.txt
```

## Error Handling

If extracted text quality is poor:

1. Check chapter descriptions in `data_structure.md`
2. Try different keyword combinations
3. Read surrounding context to infer content

## Output Language Reminder

<HARD_CONSTRAINT>
When outputting search results to users, ALL content MUST be in Simplified Chinese (简体中文), even if the source PDF is in English.

Example:
- Source: "Drug X is recommended for first-line treatment"
- Output: "推荐药物X用于一线治疗"
</HARD_CONSTRAINT>

---

*Last Updated: 2026-03-04*
