#!/usr/bin/env python3
"""医学指南提取管线 — MinerU(PDF) + Docling(DOCX) + VLM(图片描述)。

子命令:
  extract         提取单个文件
  extract-all     批量提取整个知识库
  describe-images 对提取的图片生成 VLM 描述
  pipeline        全流程（提取 + 后处理 + 图片描述）
"""

from __future__ import annotations

import argparse
import itertools
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path for direct invocation
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.extraction.pdf_extractor import extract_pdf
from scripts.extraction.docx_extractor import extract_docx
from scripts.extraction.vlm_describer import (
    check_vlm_health,
    classify_image,
    compute_image_hash,
    describe_image,
    inject_descriptions,
    load_cache,
    save_cache,
)


DEFAULT_VLM_MODEL = (
    "gemma-4-31b-it-mystery-fine-tune-heretic-uncensored-thinking-instruct"
)


def cmd_extract(args):
    """Extract a single file."""
    src = Path(args.input).resolve()
    out_dir = Path(args.output_dir).resolve()

    if src.suffix.lower() == ".pdf":
        result = extract_pdf(src, out_dir)
        print(f"PDF 提取完成: {result['md_path']}")
        print(f"  图片: {result['image_count']} 张")
    elif src.suffix.lower() == ".docx":
        result = extract_docx(src, out_dir)
        print(f"DOCX 提取完成: {result['md_path']}")
    else:
        print(f"不支持的格式: {src.suffix}", file=sys.stderr)
        sys.exit(1)


def cmd_extract_all(args):
    """Extract all files in a knowledge base directory."""
    kb_root = Path(args.kb_root).resolve()
    force = args.force
    stats = {"success": 0, "skipped": 0, "failed": 0}

    extracted_dir = kb_root / "extracted"
    extracted_dir.mkdir(exist_ok=True)
    images_root = kb_root / "images"
    images_root.mkdir(exist_ok=True)

    # Scan source/ for PDF/DOCX files
    source_dir = kb_root / "source"
    if not source_dir.exists():
        source_dir = kb_root  # fallback: scan root

    # When scanning kb_root, skip our own output subdirs so MinerU's
    # intermediate *_layout.pdf / *_origin.pdf files aren't re-ingested.
    excluded_roots = {extracted_dir, images_root, kb_root / "archive"}

    def _under_excluded(path: Path) -> bool:
        return any(
            excluded in path.parents or path == excluded
            for excluded in excluded_roots
        )

    sources = sorted(
        p
        for p in itertools.chain(
            source_dir.rglob("*.[pP][dD][fF]"),
            source_dir.rglob("*.[dD][oO][cC][xX]"),
        )
        if not _under_excluded(p)
    )

    if not sources:
        print("未找到 PDF/DOCX 文件", file=sys.stderr)
        return stats

    for src in sources:
        out_md = extracted_dir / f"{src.stem}.md"
        if out_md.exists() and not force:
            print(f"  跳过 {src.name}: 已存在")
            stats["skipped"] += 1
            continue

        try:
            if src.suffix.lower() == ".pdf":
                result = extract_pdf(src, images_root)
                # Flatten: copy md to extracted/
                shutil.copy2(result["md_path"], out_md)
                print(f"  完成 {src.name} (图片: {result['image_count']})")
            else:
                extract_docx(src, extracted_dir)
                print(f"  完成 {src.name}")
            stats["success"] += 1
        except Exception as e:
            print(f"  失败 {src.name}: {e}", file=sys.stderr)
            stats["failed"] += 1

    print(
        f"\n提取完成: 成功 {stats['success']}, "
        f"跳过 {stats['skipped']}, 失败 {stats['failed']}"
    )
    return stats


def cmd_describe_images(args):
    """Generate VLM descriptions for extracted images."""
    images_dir = Path(args.input_dir).resolve()
    model = args.model or DEFAULT_VLM_MODEL

    if not check_vlm_health(model):
        print(f"LM Studio 未运行或模型 {model} 未加载", file=sys.stderr)
        sys.exit(1)

    cache_path = images_dir / "image_descriptions.json"
    cache = load_cache(cache_path)

    images = sorted(images_dir.glob("*.jpg"))
    total = len(images)
    if total == 0:
        print("  无图片文件")
        return

    new_count = 0
    for i, img_path in enumerate(images, 1):
        img_hash = compute_image_hash(img_path)
        if img_hash in cache:
            print(f"  [{i}/{total}] 缓存命中: {img_path.name}")
            continue

        print(f"  [{i}/{total}] 分类: {img_path.name}...")
        classification = classify_image(img_path, model)
        print(f"           → {classification}")

        description = describe_image(img_path, classification, model)
        cache[img_hash] = {
            "file": img_path.name,
            "classification": classification,
            "description": description,
            "model": model,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        new_count += 1

        if description:
            print(f"           描述: {description[:80]}...")
        else:
            print("           跳过（装饰性图片）")

        # Save cache after each image (crash safety)
        save_cache(cache, cache_path)

    print(f"\n描述完成: {new_count} 新增, {total - new_count} 缓存命中")


def cmd_pipeline(args):
    """Full pipeline: extract → postprocess → VLM describe."""
    kb_root = Path(args.kb_root).resolve()
    model = args.model or DEFAULT_VLM_MODEL

    # Phase 1: Extract
    print("=" * 60)
    print("[1/2] 提取阶段")
    print("=" * 60)
    cmd_extract_all(args)

    # Phase 2: VLM (optional)
    print("\n" + "=" * 60)
    print("[2/2] VLM 图片描述阶段")
    print("=" * 60)

    if not check_vlm_health(model):
        print(f"⚠ LM Studio 未运行或模型未加载 → 跳过 VLM 描述")
        print(
            "  后续可单独运行: python3 scripts/extract_guidelines.py "
            "describe-images --input-dir <images_dir>"
        )
        return

    images_root = kb_root / "images"
    if not images_root.exists():
        print("  无图片目录，跳过")
        return

    for img_dir in sorted(images_root.iterdir()):
        if not img_dir.is_dir():
            continue
        # Only process dirs that have images
        img_subdir = img_dir / "hybrid_auto" / "images"
        if img_subdir.exists():
            target = img_subdir
        elif list(img_dir.glob("*.jpg")):
            target = img_dir
        else:
            continue

        print(f"\n处理 {img_dir.name}/")
        # Temporarily set args for describe
        args.input_dir = str(target)
        args.model = model
        cmd_describe_images(args)

    # Inject descriptions into markdown files
    extracted_dir = kb_root / "extracted"
    for md_file in sorted(extracted_dir.glob("*.md")):
        stem = md_file.stem
        # Check multiple possible cache locations
        for cache_candidate in [
            images_root / stem / "hybrid_auto" / "images" / "image_descriptions.json",
            images_root / stem / "image_descriptions.json",
        ]:
            if cache_candidate.exists():
                cache = load_cache(cache_candidate)
                descriptions = {}
                for entry in cache.values():
                    if entry.get("description"):
                        descriptions[f"images/{entry['file']}"] = entry["description"]

                if descriptions:
                    content = md_file.read_text(encoding="utf-8")
                    updated = inject_descriptions(content, descriptions)
                    md_file.write_text(updated, encoding="utf-8")
                    print(f"  注入 {len(descriptions)} 个描述到 {md_file.name}")
                break


def main():
    parser = argparse.ArgumentParser(
        description="医学指南提取管线（MinerU + Docling + VLM）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # extract
    p_extract = sub.add_parser("extract", help="提取单个文件")
    p_extract.add_argument("--input", required=True, help="输入文件路径")
    p_extract.add_argument("--output-dir", required=True, help="输出目录")

    # extract-all
    p_all = sub.add_parser("extract-all", help="批量提取整个知识库")
    p_all.add_argument("--kb-root", required=True, help="知识库根目录")
    p_all.add_argument("--force", action="store_true", help="强制重新提取")

    # describe-images
    p_desc = sub.add_parser("describe-images", help="VLM 图片描述")
    p_desc.add_argument("--input-dir", required=True, help="图片目录")
    p_desc.add_argument("--model", default=None, help="VLM 模型名称")

    # pipeline
    p_pipe = sub.add_parser("pipeline", help="全流程")
    p_pipe.add_argument("--kb-root", required=True, help="知识库根目录")
    p_pipe.add_argument("--force", action="store_true", help="强制重新提取")
    p_pipe.add_argument("--model", default=None, help="VLM 模型名称")

    args = parser.parse_args()

    commands = {
        "extract": cmd_extract,
        "extract-all": cmd_extract_all,
        "describe-images": cmd_describe_images,
        "pipeline": cmd_pipeline,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
