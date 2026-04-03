---
title: document-release 遗漏描述性文档内容（表格、列表、叙述段落）
date: 2026-04-03
category: logic-errors
module: documentation
problem_type: documentation_gap
component: tooling
severity: moderate
applies_when:
  - "功能迁移后执行 document-release 同步文档"
  - "删除功能后检查文档是否完整更新"
tags:
  - document-release
  - stale-docs
  - format-migration
  - descriptive-content
---

# document-release 遗漏描述性文档内容（表格、列表、叙述段落）

## Context

执行 `/document-release` 同步文档后，README.md 中仍残留 6 处旧三格式输出（xlsx/docx/pptx）的引用。这些引用分布在 Output Deliverables 表格、File Structure 树、Requirements 列表和 Acknowledgments 叙述段落中。

第一轮 document-release 成功更新了 CLAUDE.md（架构树、依赖表、命令示例）和 CHANGELOG（新增 v2.5.0 条目），但完全遗漏了 README.md 的以下位置：

| 行号 | 内容 | 类型 |
|------|------|------|
| 110-112 | Output Deliverables 表格（3 行旧格式文件） | 描述性表格 |
| 132 | File Structure 中的 `report_template.pptx` | 目录树 |
| 138 | 测试数 `118 tests`（实际 148） | 数值 |
| 152-153 | Requirements 中的 `python-docx`、`python-pptx` | 依赖列表 |
| 158 | "multi-format report generation" | 叙述段落 |

## Guidance

document-release 工具（无论是 AI 还是人）倾向于更新**命令和配置引用**（`--format all` → `--format md`），却遗漏**描述功能的散文和表格**。

审查文档时，区分两种内容类型：

1. **指令性内容** — 命令、代码块、配置项。这些容易被 grep 和 diff 驱动的工具发现并更新。
2. **描述性内容** — 表格、列表、叙述段落。这些用自然语言描述功能，不包含可被精确匹配的关键词，容易被遗漏。

**修复方法：** 在 document-release 后，用**被删除的产品名**（不是代码关键词）搜索所有 `.md` 文件：

```bash
# 用产品名搜索，不是代码名
grep -rn 'xlsx\|docx\|pptx\|Excel.*汇总\|Word.*报告\|PowerPoint\|幻灯片' *.md docs/*.md
```

## Why This Matters

README 是用户的第一接触点。如果 README 说"输出 xlsx + docx + pptx"但实际只输出 MD，用户会：
1. 安装已不再需要的 `python-docx` 和 `python-pptx`
2. 期望得到三种文件但只收到一个 MD
3. 认为功能有 bug

GitHub Release 页面展示 README，所以这个问题影响面很大。

## When to Apply

- 功能迁移后的文档同步（尤其是删除/替换输出格式）
- 任何 document-release 完成后的二次验证
- 合并 PR 前的最终文档检查

## Examples

```bash
# document-release 完成后的验证步骤
# 1. 列出所有被删除的功能/文件关键词
DELETED_TERMS="xlsx docx pptx report_template python-docx python-pptx"

# 2. 搜索所有 .md 文件
for term in $DELETED_TERMS; do
  grep -rn "$term" *.md docs/*.md references/*.md 2>/dev/null
done

# 3. 如果有匹配，逐一确认是否需要更新
```

## Related

- 首次发现: 本次 v2.5.0 document-release 流程
- 相关文档: `docs/solutions/security-issues/llm-output-sanitization-markdown-generate.md` — 同一迁移中的代码审查清单
