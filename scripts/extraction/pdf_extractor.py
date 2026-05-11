"""MinerU PDF extraction wrapper."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from scripts.extraction.postprocess import postprocess_latex

# Candidate locations for the mineru binary when it is installed in a venv
# and wrapped as a shell function (not visible to subprocess via PATH).
_MINERU_CANDIDATES = [
    Path.home() / ".venvs" / "mineru" / "bin" / "mineru",
    Path.home() / ".local" / "bin" / "mineru",
]


def _resolve_mineru_cmd(cmd: str) -> str:
    """Return the first usable mineru binary path.

    Subprocess cannot execute shell functions, so we check venv candidates
    before falling back to the caller-supplied command string.
    """
    for candidate in _MINERU_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    found = shutil.which(cmd)
    return found if found else cmd


def resolve_mineru_output(output_dir: Path, stem: str) -> tuple:
    """Resolve MinerU's nested output structure.

    MinerU outputs to: output_dir/{stem}/hybrid_auto/{stem}.md
    Images go to:      output_dir/{stem}/hybrid_auto/images/

    Returns:
        (md_path, images_dir)

    Raises:
        FileNotFoundError: if the expected output structure doesn't exist
    """
    md_path = output_dir / stem / "hybrid_auto" / f"{stem}.md"
    images_dir = output_dir / stem / "hybrid_auto" / "images"
    if not md_path.exists():
        raise FileNotFoundError(
            f"MinerU output not found at {md_path}. "
            f"Check that mineru processed the file successfully."
        )
    return md_path, images_dir


def extract_pdf(
    source_path: Path,
    output_dir: Path,
    *,
    mineru_cmd: str = "mineru",
) -> dict:
    """Extract a PDF file using MinerU CLI.

    Args:
        source_path: Path to the source PDF
        output_dir: Directory for MinerU output
        mineru_cmd: MinerU CLI command name

    Returns:
        {"md_path": Path, "images_dir": Path, "image_count": int}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = source_path.stem

    resolved_cmd = _resolve_mineru_cmd(mineru_cmd)
    result = subprocess.run(
        [resolved_cmd, "-p", str(source_path), "-o", str(output_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"MinerU failed for {source_path.name}: {result.stderr[:500]}"
        )

    md_path, images_dir = resolve_mineru_output(output_dir, stem)

    # LaTeX postprocessing
    content = md_path.read_text(encoding="utf-8")
    cleaned = postprocess_latex(content)
    md_path.write_text(cleaned, encoding="utf-8")

    image_count = len(list(images_dir.glob("*.jpg"))) if images_dir.exists() else 0

    return {
        "md_path": md_path,
        "images_dir": images_dir,
        "image_count": image_count,
    }
