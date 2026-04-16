"""VLM image classification, description, and injection."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import requests
from requests.exceptions import RequestException


LM_STUDIO_URL = "http://localhost:1234/v1"

CLASSIFY_PROMPT = """这张图片属于以下哪个类别？只回答类别名称，不要解释。
- 流程图
- 数据图表
- 医学示意图
- 表格图片
- 装饰性图片"""

PROMPTS_BY_TYPE = {
    "流程图": (
        "你是一个医学指南图片描述专家。请用简体中文详细描述这张临床决策流程图中的所有节点、"
        "分支条件和治疗路径。要求：\n"
        "1）列出每个决策节点和对应的处置方案\n"
        "2）保留所有英文医学术语和分期标记\n"
        "3）描述箭头指向的逻辑关系\n"
        "4）末尾附加逻辑关系汇总表\n"
        "输出格式为结构化 Markdown 文本。"
    ),
    "数据图表": (
        "请用简体中文描述这张医学数据图表。要求：\n"
        "1）说明图表类型（生存曲线/柱状图/折线图等）\n"
        "2）列出所有数据组和关键数据点\n"
        "3）描述主要趋势和统计学差异\n"
        "4）保留所有 p 值、HR、CI 等统计指标"
    ),
    "医学示意图": (
        "请用简体中文描述这张医学示意图的结构和内容。"
        "列出所有标注的解剖结构、分期标记或分类标准。"
    ),
    "表格图片": (
        "请将这张表格图片转换为 Markdown 表格格式。"
        "保留所有行列内容，保持原始语言（中文/英文）。"
    ),
}


def compute_image_hash(image_path: Path) -> str:
    """Compute SHA256 hash of image file content."""
    return hashlib.sha256(image_path.read_bytes()).hexdigest()


def load_cache(cache_path: Path) -> dict:
    """Load image description cache from JSON file."""
    if not cache_path.exists():
        return {}
    return json.loads(cache_path.read_text(encoding="utf-8"))


def save_cache(cache: dict, cache_path: Path) -> None:
    """Save image description cache to JSON file."""
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _call_vlm(
    image_path: Path,
    prompt: str,
    model: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> str:
    """Call LM Studio VLM API with an image and prompt."""
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    resp = requests.post(
        f"{LM_STUDIO_URL}/chat/completions",
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def check_vlm_health(target_model: str) -> bool:
    """Check if LM Studio is running and target model is loaded."""
    try:
        resp = requests.get(f"{LM_STUDIO_URL}/models", timeout=5)
        models = [m["id"] for m in resp.json().get("data", [])]
        return any(target_model in m for m in models)
    except (ConnectionError, RequestException):
        return False


def classify_image(image_path: Path, model: str) -> str:
    """Classify an image into one of the predefined categories."""
    result = _call_vlm(image_path, CLASSIFY_PROMPT, model, max_tokens=20)
    for category in PROMPTS_BY_TYPE:
        if category in result:
            return category
    if "装饰" in result:
        return "装饰性图片"
    return "医学示意图"  # default fallback


def describe_image(
    image_path: Path,
    classification: str,
    model: str,
) -> str | None:
    """Generate a text description of an image based on its classification.

    Returns None for decorative images (skipped).
    """
    if classification == "装饰性图片":
        return None

    prompt = PROMPTS_BY_TYPE.get(classification, PROMPTS_BY_TYPE["医学示意图"])
    return _call_vlm(image_path, prompt, model)


def inject_descriptions(md_text: str, descriptions: dict) -> str:
    """Inject image descriptions as blockquotes after image references.

    Args:
        md_text: Markdown content with image references
        descriptions: {image_ref: description_text}

    Returns:
        Markdown with descriptions injected
    """
    if not descriptions:
        return md_text

    def _replace(match):
        full_match = match.group(0)
        img_ref = match.group(1)
        if img_ref in descriptions:
            desc = descriptions[img_ref]
            desc_lines = desc.strip().split("\n")
            blockquote = "\n".join(f"> {line}" for line in desc_lines)
            return f"{full_match}\n\n> **[图片描述]**\n{blockquote}"
        return full_match

    return re.sub(r"!\[[^\]]*\]\(([^)]+)\)", _replace, md_text)
