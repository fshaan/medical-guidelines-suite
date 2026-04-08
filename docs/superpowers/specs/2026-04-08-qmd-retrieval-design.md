# QMD 混合检索引擎集成设计

> 用 QMD 本地混合检索引擎替换现有 grep 检索方案，同时用 Docling 重构文档提取层。

---

## 1. 背景与动机

当前检索方案基于纯 grep 关键词匹配（`extracted/*.txt`），存在四个核心局限：

- **无语义理解**：grep 只能做字面匹配，无法理解同义词或概念关联
- **关键词生成依赖 LLM**：orchestrate 阶段需要 LLM 预生成 grep 命令，关键词选择不当导致漏检
- **结果无排序**：grep 返回所有匹配行，无法按相关性排序
- **跨语言检索弱**：中英文指南混合，grep 无法处理跨语言语义对齐

## 2. 目标

- 引入 QMD（BM25 + 向量语义搜索 + LLM 重排序）替代 grep
- 用 Docling 重构文档提取层，产出从 `.txt` 改为 `.md`
- 保持批处理流水线接口兼容
- SKILL.md 查询流程改用 QMD MCP 原生集成

## 3. 决策记录

### 3.1 Brainstorming 决策

| # | 决策点 | 选择 | 理由 |
|---|--------|------|------|
| 1 | Python ↔ QMD 集成 | HTTP MCP（批处理）+ MCP 原生（交互查询） | 批处理需 context manager 管理生命周期；交互查询用 MCP 最自然 |
| 2 | 文件格式 | 提取产物改为 `.md` | Docling 原生输出 MD，QMD 按 MD 结构智能切分 |
| 3 | 提取工具 | Docling（IBM），完全重写提取脚本 | PDF→MD 质量优于 pdftotext→txt，统一处理 PDF/DOCX |
| 4 | Context 粒度 | Org + 文件级 | 版本信息对跨指南比较至关重要，章节级可依赖 MD 标题自然识别 |
| 5 | Batch prompt 范式 | 结果直给，LLM 纯分析 | 批处理追求确定性和可验证性，anti-laziness 依赖已知结果集 |
| 6 | Embedding 模型 | Qwen3-Embedding | 中英文混合知识库需要优秀的 CJK 支持 |

### 3.2 Eng Review 决策（来自 Update_Plan.md）

| # | 决策点 | 选择 |
|---|--------|------|
| 1 | QMD 服务生命周期 | Python context manager 自动管理 |
| 2 | prompt 改造策略 | 抽取模板框架，参数化检索结果填充 |
| 3 | Anti-laziness 简化 | 删除 V1/V2，V3 改造为引用覆盖率（≥50%），保留 V4 |
| 4 | grep 代码处置 | 彻底删除 |
| 5 | 测试策略 | 分层：单元测试 mock + 集成测试真实 QMD（@slow 门控） |
| 6 | 并发策略 | 首版串行查询 |
| 7 | LLM 合规 | 引用覆盖率检查 |
| 8 | subprocess 管理 | context manager + 专门生命周期测试 |

## 4. 架构设计

### 4.1 实施策略：三层递进

在同一个 PR 中，按依赖关系分 3 层顺序实施，每层完成后可独立测试验证。

```
Layer 1 — 提取层重构
  Docling 替换 pdftotext/python-docx → extracted/*.md

Layer 2 — 检索层替换
  retriever.py (QMDService) + index 子命令 + Context 注入

Layer 3 — 流水线适配
  orchestrate 预检索 + prompt 重写 + anti-laziness 改造 + verify-batch 适配 + SKILL.md
```

### 4.2 系统架构总览

```
                        ┌─────────────────────┐
                        │   SKILL.md (交互)    │
                        │   QMD MCP 原生集成    │
                        └─────────┬───────────┘
                                  │
┌──────────────┐    ┌─────────────▼───────────┐    ┌──────────────────┐
│  Docling      │    │   QMD (本地混合检索)      │    │  batch_pipeline  │
│  PDF/DOCX     │───▶│   BM25 + Vector + Rerank │◀───│  orchestrate     │
│  → *.md       │    │   Qwen3-Embedding        │    │  (HTTP MCP)      │
└──────────────┘    └─────────────┬───────────┘    └──────────────────┘
                                  │
                        ┌─────────▼───────────┐
                        │  extracted/*.md       │
                        │  $KB_ROOT/<Org>/      │
                        │  + QMD Context 元数据  │
                        └─────────────────────┘
```

## 5. Layer 1：提取层重构

### 5.1 改动范围

| 文件 | 操作 |
|------|------|
| `scripts/extract_pdf.py` | 删除 |
| `scripts/extract_docx.py` | 删除 |
| `scripts/extract_all.py` | 重写 — 统一调用 Docling |

### 5.2 新 extract_all.py 核心逻辑

```python
from docling.document_converter import DocumentConverter

converter = DocumentConverter()

def extract_file(source_path: Path, output_dir: Path) -> Path:
    """PDF/DOCX → Markdown，输出到 extracted/<stem>.md"""
    result = converter.convert(source_path)
    md_content = result.document.export_to_markdown()
    out_path = output_dir / f"{source_path.stem}.md"
    out_path.write_text(md_content, encoding="utf-8")
    return out_path

def extract_all(kb_root: Path, force: bool = False):
    """遍历 $KB_ROOT/<Org>/ 下所有 PDF/DOCX，增量提取到 extracted/*.md"""
    for org_dir in kb_root.iterdir():
        if not org_dir.is_dir() or org_dir.name.startswith('.'):
            continue
        extracted_dir = org_dir / "extracted"
        extracted_dir.mkdir(exist_ok=True)
        for src in itertools.chain(
            org_dir.glob("*.[pP][dD][fF]"),
            org_dir.glob("*.[dD][oO][cC][xX]"),
        ):
            out = extracted_dir / f"{src.stem}.md"
            if out.exists() and not force:
                continue
            extract_file(src, extracted_dir)
```

### 5.3 下游引用适配

所有引用 `extracted/*.txt` 的位置改为 `extracted/*.md`：

- `SKILL.md` — 搜索/导航路径描述
- `templates/data_structure_org.md` — 文件清单模板
- `scripts/batch_pipeline.py` — `scan_knowledge_base()` 中的 glob pattern
- `references/` — 提取相关参考文档
- `CLAUDE.md` / `README.md` — 文档

### 5.4 依赖变化

| 移除 | 新增 |
|------|------|
| `pdftotext`（系统 bin） | `docling`（pip） |

Docling 内含 PDF + DOCX 支持，无需额外依赖。

## 6. Layer 2：检索层替换

### 6.1 QMDService — Context Manager

新增 `scripts/retriever.py`：

```python
class QMDService:
    """管理 QMD HTTP MCP Server 生命周期。"""

    def __init__(self, port: int = 8181):
        self.port = port
        self.process = None
        self.base_url = f"http://localhost:{port}/mcp"

    def __enter__(self):
        # 启动: qmd mcp --http --port {port} --daemon
        # 健康检查: 轮询 /mcp 直到就绪（超时 30s）
        # 端口冲突检测: 启动前检查端口是否被占用
        # 返回 self

    def __exit__(self, *exc):
        # 优雅关闭: SIGTERM → 等待 5s → SIGKILL
        # process.poll() 确认进程状态后决定清理策略
        # 清理僵尸进程

    def query(self, text: str, top_k: int = 10, min_score: float = 0.3) -> list[dict]:
        """执行 hybrid query，返回结构化结果。
        
        Returns: [{"content": str, "path": str, "score": float, "context": str}, ...]
        """

    def search(self, text: str, top_k: int = 10) -> list[dict]:
        """纯 BM25 搜索（无 LLM 参与，更快）。"""
```

关键设计点：
- `__enter__` 先检查端口占用，避免冲突
- `__exit__` 处理三种场景：正常退出、Python 异常、QMD 进程已 crash
- 所有 HTTP 调用设置合理超时（查询 60s，健康检查 5s）

### 6.2 index 子命令

新增 `batch_pipeline.py index` 子命令：

```bash
python scripts/batch_pipeline.py index --kb-root $MEDICAL_GUIDELINES_DIR
```

执行步骤：
1. 每个 Org 创建一个 QMD collection：`qmd collection add <kb_root>/<org>/extracted --name <org> --mask "**/*.md"`
2. 用 Qwen3-Embedding 生成向量：`QMD_EMBED_MODEL=Qwen3-Embedding qmd embed`
3. Context 注入：
   - Org 级：`qmd context add qmd://<org> "<Org 全称描述>"`（从 `data_structure.md` 读取）
   - 文件级：`qmd context add qmd://<org>/<filename> "<指南名称 版本>"`

### 6.3 对外接口

Layer 3 消费的核心接口：

```python
QMDService          # context manager
QMDService.query()  # hybrid 检索 → list[dict]
```

## 7. Layer 3：流水线适配

### 7.1 orchestrate 改造

```
cmd_orchestrate()
  ├── extract_patient_features()          # 保留不变
  ├── QMDService.__enter__()              # 新增
  ├── 对每位患者执行 QMD 预检索             # 替代 generate_grep_commands
  │   ├── build_queries() → 自然语言查询    # 从 patient features 构建
  │   └── qmd.query() → top-K 结果         # 全局查询，QMD 自动跨 collection 搜索
  ├── generate_batch_prompt()              # 重写：嵌入预检索结果
  └── QMDService.__exit__()
```

查询构建策略 — 从各维度关键词组合成自然语言查询：

```python
def build_queries(patient: dict, features: dict) -> list[str]:
    """从患者特征构建 QMD 查询。每个临床维度一条查询。"""
    queries = []
    disease = patient.get("disease_type", "")

    staging = features.get("staging_keywords", [])
    if staging:
        queries.append(f"{disease} {' '.join(staging)} 诊断 分期 治疗原则")

    molecular = features.get("molecular_keywords", [])
    if molecular:
        queries.append(f"{disease} {' '.join(molecular)} 靶向治疗 免疫治疗")

    treatment = features.get("treatment_keywords", [])
    if treatment:
        queries.append(f"{disease} {' '.join(treatment)} 推荐方案 证据等级")

    return queries or [f"{disease} 治疗推荐"]
```

### 7.2 generate_batch_prompt() 重写

核心变化 — 从"命令执行指令"变为"结果分析指令"：

| 旧结构 | 新结构 |
|--------|--------|
| MANDATORY_RULES（执行 grep） | MANDATORY_RULES（分析预检索结果） |
| 患者临床信息 | 患者临床信息（不变） |
| grep 命令列表（CMD-P001-NCCN-01） | 预检索结果（按 org 分组，含原文片段 + score） |
| 检查点（确认命令执行） | 引用要求（≥50% 预检索结果须被引用） |
| JSON 模板（含 execution_log） | JSON 模板（含 retrieval_sources + citation_coverage） |

JSON 输出模板字段变化：

| 字段 | 旧 | 新 |
|------|----|----|
| `execution_log` | `[{cmd_id, match_count, snippet}]` | **删除** |
| `execution_summary` | `{total_commands_in_prompt, ...}` | **删除** |
| `retrieval_sources` | — | **新增** `[{chunk_id, score, path, snippet}]` |
| `citation_coverage` | — | **新增** 引用率（float） |

### 7.3 Anti-laziness 改造

| 层 | 旧 | 新 |
|----|----|----|
| V1 | CMD-ID 覆盖率检查 | **删除** |
| V2 | 计数一致性检查 | **删除** |
| V3 | snippet 真实性验证 | **改造** → 引用覆盖率检查（≥50% 预检索 chunk 被引用） |
| V4 | 零匹配矛盾检测 | **保留**（适配：检查"预检索无结果但有推荐内容"的矛盾） |

V3 新逻辑：

```python
def check_citation_coverage(pre_retrieved: list[str], cited: list[str]) -> float:
    """预检索结果被引用的覆盖率。"""
    if not pre_retrieved:
        return 1.0
    cited_set = set(cited)
    covered = sum(1 for c in pre_retrieved if c in cited_set)
    return covered / len(pre_retrieved)
# 阈值: ≥ 0.5，否则 warning
```

### 7.4 verify-batch 适配

`_verify_batch_results()` 改造：

- **删除**：CMD-ID 解析、prompt 命令提取、execution_log 遍历
- **新增**：验证 `retrieval_sources` 中的 `chunk_id` 和 `path` 是否来自实际预检索结果集
- **保留**：V4 矛盾检测（适配新字段名）
- **保留**：推荐长度异常检测、跨批次相似度检测、深度衰减检测

### 7.5 SKILL.md 查询流程

Part 2（QUERY Phase）从 grep + Agent 导航改为 QMD MCP 调用：

| 旧流程 | 新流程 |
|--------|--------|
| 读 `data_structure.md` 定位 | `qmd query "临床问题"` → top-K 结果 |
| `grep -n` 关键词 `extracted/*.txt` | QMD 自动跨 org 搜索 |
| Agent 导航 → 精读段落 | 直接读取 chunk 上下文 |
| 整理跨指南比较表 | 整理跨指南比较表（不变） |

### 7.6 删除的代码

| 函数/模块 | 行数估算 |
|-----------|---------|
| `escape_grep_keyword()` | ~20 行 |
| `generate_grep_commands()`（含 slim 分支） | ~80 行 |
| `_GREP_SPECIAL` 正则 | 1 行 |
| `generate_batch_prompt()` 中 grep 相关部分 | ~60 行 |
| `_verify_batch_results()` 中 CMD-ID 相关部分 | ~50 行 |
| `test_grep.py` | 整个文件 |
| `scripts/extract_pdf.py` | 整个文件 |
| `scripts/extract_docx.py` | 整个文件 |

## 8. 测试策略

### 8.1 单元测试（默认运行，mock QMD HTTP 响应）

| 测试文件 | 覆盖内容 |
|----------|---------|
| `test_extract.py` | 新增 — Docling 提取（mock `DocumentConverter`） |
| `test_retriever.py` | 新增 — QMDService 生命周期、query/search、异常处理 |
| `test_index.py` | 新增 — index 子命令（mock subprocess） |
| `test_orchestrate.py` | 改写 — 预检索 + 新 prompt 生成 |
| `test_prompt.py` | 改写 — 新 prompt 结构验证 |
| `test_verify_batch.py` | 改写 — citation coverage 验证、V4 适配 |
| `test_validate_enhance.py` | 适配 — 新 JSON 字段 |
| `test_grep.py` | 删除 |

### 8.2 集成测试（@pytest.mark.slow + QMD_AVAILABLE=1 门控）

| 测试文件 | 内容 |
|----------|------|
| `test_qmd_integration.py` | 真实 QMD 启动/查询/关闭全流程、Context 注入验证 |

## 9. 配置与环境变量

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `MEDICAL_GUIDELINES_DIR` | 知识库路径 | 不变 |
| `QMD_EMBED_MODEL` | Embedding 模型 | `Qwen3-Embedding` |
| `QMD_PORT` | HTTP MCP 端口 | `8181` |
| `QMD_AVAILABLE` | 集成测试门控 | 未设置则跳过 |

## 10. 文档更新

| 文件 | 改动 |
|------|------|
| `CLAUDE.md` | 依赖表（+docling, +qmd, -pdftotext）、架构描述、命令示例 |
| `README.md` | 同步 |
| `SKILL.md` | Part 1 提取流程、Part 2 查询流程、Part 3 批处理流程 |
| `skill.json` | `requires.bins` 加 `qmd`、`requires.pip` 加 `docling` |
| `references/` | 可选新增 `qmd_integration.md` |

## 11. 历史经验风险提醒

来自 `docs/solutions/llm-output-sanitization-markdown-generate.md`：

- **信任边界**：grep 双层转义问题不再存在，但 QMD API 输入/输出构成新的信任边界。对 QMD 返回结果做结构化解析（JSON），不做原始文本拼接。
- **幽灵引用**：删除 grep 代码时检查所有跨文件引用，确保无残留引用。
