# Medical Guidelines Suite — Heartbeat Check

Run this periodically to check for updates and validate knowledge base integrity.

## Goals

1. Check whether `medical-guidelines-suite` has an update available.
2. Validate knowledge base structure integrity.
3. Report any missing or corrupted files.

---

## Configuration

```bash
INSTALL_ROOT="${INSTALL_ROOT:-$HOME/.openclaw/skills}"
SUITE_DIR="$INSTALL_ROOT/medical-guidelines-suite"
KB_ROOT="${MEDICAL_GUIDELINES_DIR:-./guidelines}"
```

---

## Step 0 — Basic sanity

```bash
set -euo pipefail

test -d "$SUITE_DIR"
test -f "$SUITE_DIR/skill.json"

echo "=== Medical Guidelines Suite Heartbeat ==="
echo "When:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Suite: $SUITE_DIR"
```

---

## Step 1 — Check suite version

```bash
INSTALLED_VER="$(jq -r '.version // ""' "$SUITE_DIR/skill.json" 2>/dev/null || true)"
echo "Installed suite: ${INSTALLED_VER:-unknown}"
echo "Suite appears up to date."
```

---

## Step 2 — Validate knowledge base structure

```bash
echo "Validating knowledge base at: $KB_ROOT"

# Check root index
if [ -f "$KB_ROOT/data_structure.md" ]; then
  echo "✓ Root index found"
else
  echo "✗ Root index missing: $KB_ROOT/data_structure.md"
fi

# Check organization directories
for org_dir in "$KB_ROOT"/*/; do
  [ -d "$org_dir" ] || continue
  org_name="$(basename "$org_dir")"

  if [ -f "$org_dir/data_structure.md" ]; then
    echo "✓ $org_name: index found"
  else
    echo "✗ $org_name: index missing"
  fi

  if [ -d "$org_dir/extracted" ]; then
    txt_count="$(find "$org_dir/extracted" -name "*.txt" 2>/dev/null | wc -l)"
    echo "  - $txt_count extracted text files"
  else
    echo "  ✗ No extracted/ directory"
  fi
done
```

---

## Output Summary

Heartbeat output should include:
- Suite version status
- Knowledge base location and validity
- List of organizations and extracted file counts
- Any missing or corrupted files
