# PDF Extraction Methods

## Overview

This document describes methods for extracting text from medical guideline PDF files.

---

## Method 1: pdftotext (Recommended for Text-Based PDFs)

### Installation

```bash
# macOS
brew install poppler

# Ubuntu/Debian
sudo apt-get install poppler-utils

# Windows (via chocolatey)
choco install poppler
```

### Basic Usage

```bash
# Simple extraction
pdftotext input.pdf output.txt

# Preserve layout (recommended for guidelines)
pdftotext -layout input.pdf output.txt

# With page markers
pdftotext -layout -nopgbrk input.pdf output.txt
# Then manually add page markers if needed
```

### Layout Preservation Options

| Option | Effect | Use Case |
|--------|--------|----------|
| `-layout` | Preserve original layout | Tables, multi-column |
| `-raw` | Raw content order | When layout causes issues |
| `-nopgbrk` | No page break characters | Continuous text |
| `-eol unix/dos/mac` | Line ending format | Cross-platform |

### Chinese PDF Handling

```bash
# Ensure CJK support (check poppler build)
pdftotext -v 2>&1 | grep -i cjk

# If no CJK support, reinstall with:
brew reinstall poppler --with-qt  # macOS
```

---

## Method 2: pdfplumber (Python, Better for Tables)

### Installation

```bash
pip install pdfplumber
```

### Basic Script

```python
#!/usr/bin/env python3
"""Extract text from PDF with table preservation."""

import pdfplumber
import sys
from pathlib import Path

def extract_pdf(pdf_path: str) -> str:
    """Extract text from PDF file."""
    output_lines = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            # Page marker
            output_lines.append(f"--- Page {page_num} ---\n")

            # Extract text
            text = page.extract_text()
            if text:
                output_lines.append(text)
                output_lines.append("\n")

            # Extract tables
            tables = page.extract_tables()
            for table_num, table in enumerate(tables, 1):
                output_lines.append(f"\n[Table {table_num} on Page {page_num}]\n")
                for row in table:
                    # Convert to markdown-style table
                    output_lines.append(" | ".join(str(cell) if cell else "" for cell in row))
                    output_lines.append("\n")

    return "".join(output_lines)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_pdf.py <input.pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = Path(pdf_path).stem + ".txt"

    content = extract_pdf(pdf_path)

    # Write to extracted directory
    output_dir = Path(pdf_path).parent / "extracted"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / output_path

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Extracted: {pdf_path} -> {output_file}")
    print(f"Lines: {len(content.splitlines())}")
```

---

## Method 3: PyMuPDF (fitz, Fastest)

### Installation

```bash
pip install pymupdf
```

### Basic Script

```python
#!/usr/bin/env python3
"""Fast PDF text extraction using PyMuPDF."""

import fitz  # PyMuPDF
import sys
from pathlib import Path

def extract_pdf(pdf_path: str) -> str:
    """Extract text from PDF file."""
    output_lines = []
    doc = fitz.open(pdf_path)

    for page_num in range(len(doc)):
        page = doc[page_num]
        output_lines.append(f"--- Page {page_num + 1} ---\n")
        output_lines.append(page.get_text())
        output_lines.append("\n")

    doc.close()
    return "".join(output_lines)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_pdf.py <input.pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    content = extract_pdf(pdf_path)

    output_dir = Path(pdf_path).parent / "extracted"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / (Path(pdf_path).stem + ".txt")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Extracted to: {output_file}")
```

---

## Method Comparison

| Method | Speed | Tables | Chinese | Best For |
|--------|-------|--------|---------|----------|
| pdftotext | ⚡⚡⚡ | ❌ | ✅ | Simple text |
| pdfplumber | ⚡ | ✅✅ | ✅ | Tables, structured |
| PyMuPDF | ⚡⚡⚡ | ❌ | ✅ | Large files, speed |

---

## Quality Verification

### Check Extraction Quality

```bash
# Line count
wc -l extracted/*.txt

# Character count (should be substantial)
wc -c extracted/*.txt

# Check encoding
file extracted/*.txt

# Preview
head -100 extracted/*.txt
```

### Common Issues

| Issue | Symptom | Solution |
|-------|---------|----------|
| Garbled Chinese | 乱码 | Reinstall poppler with CJK |
| Missing tables | 空白表格区域 | Use pdfplumber |
| Scanned PDF | 无文本输出 | Need OCR (tesseract) |
| Wrong encoding | `file` shows wrong | Re-extract with UTF-8 |

---

## Scanned PDF Handling (OCR)

If PDF is scanned (no text layer), use OCR:

```bash
# Install tesseract
brew install tesseract tesseract-lang

# OCR with Chinese
tesseract input.pdf output -l chi_sim+eng

# Or use pdf2image + OCR
pip install pdf2image pytesseract
```

---

*Last Updated: 2026-03-04*
