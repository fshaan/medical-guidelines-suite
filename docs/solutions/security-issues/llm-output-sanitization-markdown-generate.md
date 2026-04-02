---
title: AI 代码迁移的对抗性审查清单：LLM 输出信任边界与跨文件一致性
date: 2026-04-03
category: security-issues
module: batch-pipeline-generate
problem_type: security_issue
component: tooling
severity: critical
symptoms:
  - "md_escape() 未转义 HTML 实体 — LLM 输出含 <script> 时触发 XSS"
  - "md_escape() 未处理换行符 — LLM 推荐文本含 \\n 时 Markdown 表格行断裂"
  - "skill.json 仍声明已删除的 report_template.pptx 为 required:true"
  - "--format all 静默降级为 MD 输出，无废弃警告"
  - "同名患者产生相同 TOC 锚点，目录链接失效"
root_cause: missing_validation
resolution_type: code_fix
related_components:
  - documentation
tags:
  - llm-trust-boundary
  - markdown-escape
  - xss-prevention
  - ai-code-review
  - format-migration
  - cross-file-consistency
---

# AI 代码迁移的对抗性审查清单：LLM 输出信任边界与跨文件一致性

## Problem

GLM-5.1 将 `batch_pipeline.py generate` 子命令从三格式输出（xlsx/docx/pptx）迁移到单一 Markdown 输出。功能层面正确，但对抗性审查发现 7 个问题（4 严重、3 信息性），集中在三个 AI 模型的系统性盲区：信任边界、删除涟漪、边界条件。

## Symptoms

- `md_escape()` 仅转义 Markdown 语法字符，未处理 HTML 实体（`<`, `>`, `&`）和换行符
- `skill.json` 仍列出已删除的 `templates/report_template.pptx`（`required: true`）和未使用的 pip 依赖
- `--format all` 静默只产出 MD，用户脚本中最常用的调用方式无任何废弃提示
- 同名患者的 TOC 锚点碰撞，目录链接指向第一个
- `generated_at` 直接拼入文件名，可能导致路径遍历
- `.gitignore` 被意外加入 `tests/` 条目

## What Didn't Work

这些问题不是通过常规测试发现的——148 个测试全部通过。原因：

- 测试数据中不含 HTML 标签或换行符，所以 `md_escape` 的遗漏不会触发失败
- `skill.json` 校验不在测试范围内
- `--format all` 的测试只验证了产出文件存在，未检查是否发出了废弃警告
- 测试数据中无同名患者

**需要对抗性思维（"如何让这段代码在生产中失败？"）才能发现这些问题。**

## Solution

### 1. md_escape 完整转义链

```python
def md_escape(text: str) -> str:
    if not text:
        return ""
    # HTML 实体最先处理（避免二次转义）
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    # 结构性字符
    text = text.replace("\\", "\\\\")
    text = text.replace("\n", " ").replace("\r", "")
    text = text.replace("|", "\\|")
    text = text.replace("*", "\\*")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("`", "\\`")
    text = text.replace("_", "\\_")
    text = text.replace("#", "\\#")
    text = text.replace("~", "\\~")
    return text
```

### 2. 废弃路径完整覆盖

```python
# 包含聚合入口 "all"
if fmt in ("all", "xlsx", "docx", "pptx"):
    warnings.warn(...)
```

### 3. 文件名白名单过滤

```python
safe_date = re.sub(r"[^\w-]", "", generated_at)
filename = f"批量指南推荐报告_{safe_date}.md"
```

### 4. 锚点去重

```python
seen_slugs: dict[str, int] = {}

def _unique_slug(text: str) -> str:
    base = _slugify(text)
    if base in seen_slugs:
        seen_slugs[base] += 1
        return f"{base}-{seen_slugs[base]}"
    seen_slugs[base] = 1
    return base
```

### 5. 元数据清理

- `skill.json`: 删除 `report_template.pptx` 引用，pip 依赖仅保留 `openpyxl`
- `.gitignore`: 移除错误的 `tests/` 条目

## Why This Works

AI 模型在代码迁移中有三个结构性盲区：

1. **删除的涟漪效应** — AI 能正确删除函数/文件，但不会自动搜索所有交叉引用（`skill.json`、包清单、配置文件）。用 `grep -r <已删除文件>` 全项目搜索可以发现幽灵引用。

2. **信任边界不敏感** — AI 把 LLM 输出当作"自己写的数据"处理。但在 RAG 管道中，LLM 返回的推荐文本可能包含 HTML 标签、换行符、路径分隔符。本项目已有前车之鉴：grep 命令需要同时处理正则转义和 shell 转义。(auto memory [claude])

3. **聚合入口遗漏** — AI 逐个检查了 `xlsx`、`docx`、`pptx`，却遗漏了 `all`。当功能收窄时，聚合入口的语义发生了变化但代码未同步。

## Prevention

对 AI 生成的迁移代码执行以下 5 项检查：

| # | 检查项 | 方法 |
|---|--------|------|
| 1 | 幽灵引用 | `grep -r <已删除文件/依赖>` 全项目 |
| 2 | 废弃路径完整性 | 列举旧功能的所有 CLI 入口点，逐一确认废弃处理 |
| 3 | 非可信输入转义 | 追踪 LLM 输出 → 最终渲染的完整数据流，检查每个嵌入点 |
| 4 | 标识符碰撞 | 检查生成的 ID/slug/文件名在重复输入下的唯一性 |
| 5 | 配置文件附带损伤 | `git diff` 逐行审查 `.gitignore`、`skill.json` 等配置文件变更 |

## Related Issues

- PR: https://github.com/fshaan/medical-guidelines-suite/pull/4
- 设计文档: `docs/superpowers/specs/2026-04-02-generate-md-migration-design.md`
- 实现计划: `docs/superpowers/plans/2026-04-02-generate-md-migration.md`
- 相关记忆: `feedback_grep_escaping.md` — grep 命令的双层转义问题属于同一信任边界模式
