# DOCX Extraction Methods

## Overview

This document describes methods for extracting text from medical guideline DOCX files (commonly used by Chinese guidelines like CSCO, CACA).

---

## Method 1: python-docx (Recommended)

### Installation

```bash
pip install python-docx
```

### Basic Script

```python
#!/usr/bin/env python3
"""Extract text from DOCX with structure preservation."""

from docx import Document
import sys
from pathlib import Path

def extract_docx(docx_path: str) -> str:
    """Extract text from DOCX file with structure preservation."""
    doc = Document(docx_path)
    output_lines = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        # Detect heading level from style
        style_name = para.style.name if para.style else ""
        if "Heading 1" in style_name or "Title" in style_name:
            output_lines.append(f"# {text}\n")
        elif "Heading 2" in style_name:
            output_lines.append(f"## {text}\n")
        elif "Heading 3" in style_name:
            output_lines.append(f"### {text}\n")
        else:
            output_lines.append(f"{text}\n")

    # Extract tables
    for i, table in enumerate(doc.tables, 1):
        output_lines.append(f"\n### 表格 {i}\n\n")

        # Convert to markdown table
        for row_idx, row in enumerate(table.rows):
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            output_lines.append("| " + " | ".join(cells) + " |\n")

            # Add header separator after first row
            if row_idx == 0:
                output_lines.append("| " + " | ".join(["---"] * len(cells)) + " |\n")

    return "".join(output_lines)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_docx.py <input.docx>")
        sys.exit(1)

    docx_path = sys.argv[1]
    content = extract_docx(docx_path)

    output_dir = Path(docx_path).parent / "extracted"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / (Path(docx_path).stem + ".txt")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Extracted: {docx_path} -> {output_file}")
    print(f"Lines: {len(content.splitlines())}")
```

---

## Method 2: Enhanced Table Extraction

For CSCO-style recommendation tables:

```python
#!/usr/bin/env python3
"""Enhanced DOCX extraction for CSCO-style recommendation tables."""

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
import sys
from pathlib import Path

def is_recommendation_table(table: Table) -> bool:
    """Check if table contains CSCO-style recommendations."""
    if not table.rows:
        return False

    header_text = " ".join(cell.text for cell in table.rows[0].cells)
    recommendation_keywords = ["I级推荐", "II级推荐", "III级推荐", "推荐", "Category"]

    return any(kw in header_text for kw in recommendation_keywords)

def extract_recommendation_table(table: Table, table_num: int) -> str:
    """Extract recommendation table with special formatting."""
    lines = [f"\n### 推荐表 {table_num}\n\n"]

    for row_idx, row in enumerate(table.rows):
        cells = []
        for cell in row.cells:
            text = cell.text.strip().replace("\n", " | ")
            cells.append(text)

        lines.append("| " + " || ".join(cells) + " |\n")

        if row_idx == 0:
            lines.append("| " + " | ".join(["---"] * len(cells)) + " |\n")

    return "".join(lines)

def extract_docx_enhanced(docx_path: str) -> str:
    """Enhanced extraction for CSCO-style guidelines."""
    doc = Document(docx_path)
    output_lines = []

    # Process document in order (maintain paragraph/table sequence)
    for element in doc.element.body:
        if element.tag.endswith('}p'):
            # Paragraph
            para = Paragraph(element, doc)
            text = para.text.strip()
            if text:
                style_name = para.style.name if para.style else ""
                if "Heading 1" in style_name or "Title" in style_name:
                    output_lines.append(f"\n# {text}\n")
                elif "Heading 2" in style_name:
                    output_lines.append(f"\n## {text}\n")
                elif "Heading 3" in style_name:
                    output_lines.append(f"\n### {text}\n")
                else:
                    output_lines.append(f"{text}\n")

        elif element.tag.endswith('}tbl'):
            # Table
            table = Table(element, doc)
            if is_recommendation_table(table):
                output_lines.append(extract_recommendation_table(table, len(output_lines)))

    return "".join(output_lines)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_docx_enhanced.py <input.docx>")
        sys.exit(1)

    docx_path = sys.argv[1]
    content = extract_docx_enhanced(docx_path)

    output_dir = Path(docx_path).parent / "extracted"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / (Path(docx_path).stem + ".txt")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Extracted: {output_file}")
```

---

## CSCO-Style Table Handling

### Typical CSCO Table Format

```
| 分期/分层 | I级推荐 | II级推荐 | III级推荐 |
|----------|---------|---------|----------|
| ... | 方案A [1A类] | 方案B [2A类] | 方案C [3类] |
```

### Extraction Strategies

1. **Preserve recommendation levels**: I级/II级/III级
2. **Preserve evidence categories**: [1A类], [2A类], etc.
3. **Merge multi-line cells**: Use " | " separator
4. **Note merged cells**: Some cells span multiple columns

---

## Quality Verification

```bash
# Check line count
wc -l extracted/*.txt

# Check for recommendation tables
grep -c "I级推荐" extracted/*.txt

# Check evidence categories preserved
grep -c "\[1A类\]" extracted/*.txt

# Preview content
head -100 extracted/*.txt
```

---

## Common Issues

| Issue | Symptom | Solution |
|-------|---------|----------|
| Missing tables | 表格为空 | Use enhanced extraction |
| Lost formatting | 无标题层级 | Check style detection |
| Merged cells | 单元格内容重复 | Handle rowspan explicitly |
| Encoding issues | 乱码 | Force UTF-8 output |

---

## Encoding Handling

```python
# Always write with UTF-8
with open(output_file, "w", encoding="utf-8") as f:
    f.write(content)

# Verify encoding
import locale
print(locale.getpreferredencoding())  # Should be UTF-8
```

---

*Last Updated: 2026-03-04*
