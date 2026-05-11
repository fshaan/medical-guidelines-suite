# 医学指南提取管线重构设计

## 背景

当前系统使用 Docling 2.69.1 将 PDF/DOCX 指南转换为 Markdown，供 QMD 混合检索索引。
实测发现以下严重质量问题：

| 问题 | 影响文件 | 严重度 |
|------|----------|--------|
| 侧栏/水印文字泄漏到正文 | 中文 PDF（CACA） | 严重 |
| 文本碎片化，段落语义断裂 | 中文 PDF（CACA） | 严重 |
| 流程图/图表变为 `<!-- image -->` 占位符 | NCCN、CACA | 严重 |
| Unicode 连字渲染错误 `/uniFB01` | 英文 PDF（ESMO） | 中等 |
| 页眉页脚重复噪声 | NCCN | 中等 |

本设计通过引入 MinerU + VLM 图片描述管线解决上述问题。

## 方案概览

```
原始文件（PDF/DOCX）
        │
        ├── PDF ──→ MinerU ──→ .md + images/*.jpg
        │                         │
        │                         ├── LaTeX 后处理
        │                         │
        │                         └── VLM 图片描述（Gemma-4-31B）
        │                                    │
        │                                    └── 描述注入 .md
        │
        └── DOCX ──→ Docling ──→ .md + images/
                                    │
                                    └── VLM 图片描述（同上）
```

## 工具选型

### PDF 提取：MinerU

**选型依据（实测对比 6 个医学指南文件）：**

| 维度 | Docling (现) | MinerU (新) | Marker |
|------|-------------|-------------|--------|
| 中文侧栏/水印过滤 | 全部泄漏 | 完全过滤 | 崩溃 |
| 图片提取 | `<!-- image -->` 占位符 | 独立 jpg 文件 + 引用 | 崩溃 |
| Unicode 连字 (fi/fl) | `/uniFB01` 错误 | 正确渲染 | 正确 |
| 表格结构 | Markdown table（部分丢失） | HTML table（完整） | 未完成 |
| CACA 图片数 | 7 个占位符 | 23 张文件 | N/A |
| NCCN 图片数 | 175 个占位符 | 27 张文件 | N/A |
| 稳定性 | 稳定 | 稳定 | 中文 PDF OOB 崩溃 |

- **安装方式**: `uv tool install mineru`（独立 Python 环境，不影响项目依赖）
- **CLI 调用**: `mineru -p <file.pdf> -o <output_dir>/`
- **许可证**: AGPL-3.0 — 仅构建时使用，不打包分发，无合规风险
- **模型依赖**: PP-DocLayoutV2 + PaddleOCR（已通过 `mineru-models-download` 下载）

### DOCX 提取：Docling（保留）

- MinerU 不支持 DOCX 格式
- Docling 的 DOCX 处理质量优秀（标题层级、表格、术语保留完整）
- 无需更换

### VLM 图片描述：Gemma-4-31B-IT

**选型依据（实测对比 4 个模型 x 2 张图片）：**

| 维度 | Gemma-4-31B | Qwen3.5-35B-A3B | Qwen3.5-27B | MedGemma-4B |
|------|-------------|-----------------|-------------|-------------|
| NCCN 英文流程图 | 未测（同类验证） | 完美还原 | 未测 | 逻辑混淆 |
| CACA 中文流程图 OCR | 完美 | 完美 | 完美 | 严重误读 |
| 评分体系识别 | 17~23/12~16/0~11 正确 | 正确 | 正确 | 分数→年龄 |
| 输出格式 | Markdown + 汇总表，最优 | 分层 Markdown | JSON（不直观） | 思维链泄漏 |
| 术语对照 | 中英双语对照 | 中文为主 | 中英混合 | 不完整 |

- **部署方式**: LM Studio 本地，`http://localhost:1234/v1/chat/completions`
- **模型**: `gemma-4-31b-it-mystery-fine-tune-heretic-uncensored-thinking-instruct`
- **推理参数**: temperature=0.1, max_tokens=2048, top_p=0.9

## 详细设计

### 1. 新提取脚本 `scripts/extract_guidelines.py`

独立于现有 `extract_all.py`，不修改已有代码。

**核心逻辑：**

```python
def extract(file_path, output_dir):
    if file_path.suffix == '.pdf':
        # MinerU CLI 调用
        subprocess.run(['mineru', '-p', str(file_path), '-o', str(output_dir)])
        # 注意：MinerU 输出路径为嵌套结构
        #   output_dir/{stem}/hybrid_auto/{stem}.md
        #   output_dir/{stem}/hybrid_auto/images/*.jpg
        stem = file_path.stem
        mineru_md = output_dir / stem / 'hybrid_auto' / f'{stem}.md'
        mineru_images = output_dir / stem / 'hybrid_auto' / 'images'
        # LaTeX 后处理
        postprocess_latex(mineru_md)
        # 内联术语补回（尽力而为）
        recover_inline_terms(mineru_md, file_path)
        # 扁平化输出到 extracted/ 和 images/
        flatten_output(mineru_md, mineru_images, output_dir)
    elif file_path.suffix == '.docx':
        # Docling Python API
        converter = DocumentConverter()
        result = converter.convert(str(file_path))
        md = result.document.export_to_markdown(image_mode='referenced')
        write_output(md, output_dir)
    else:
        raise ValueError(f"Unsupported format: {file_path.suffix}")
```

**子命令设计：**

```bash
# 提取单个文件
python3 scripts/extract_guidelines.py extract --input <file> --output-dir <dir>

# 批量提取整个知识库
python3 scripts/extract_guidelines.py extract-all --kb-root $MEDICAL_GUIDELINES_DIR

# 图片描述（VLM）
python3 scripts/extract_guidelines.py describe-images --input-dir <dir> --model gemma-4-31b

# 全流程（提取 + 后处理 + 图片描述）
python3 scripts/extract_guidelines.py pipeline --kb-root $MEDICAL_GUIDELINES_DIR
```

**Pipeline 健康检查与部分成功：**

`pipeline` 命令在启动时执行健康检查：
1. 验证 `mineru` CLI 可用（`which mineru`）
2. 如果需要 VLM，ping `localhost:1234/v1/models`，验证目标模型已加载且支持视觉
3. 健康检查失败时，提取仍然执行，VLM 步骤跳过并打印警告

Pipeline 将提取和 VLM 描述视为**独立阶段**，分别报告状态：
```
[1/2] 提取完成: 5/5 文件成功
[2/2] VLM 描述: 跳过（LM Studio 未运行）
```

提取结果不会因 VLM 不可用而丢失。

### 2. LaTeX 后处理

MinerU 输出中的 LaTeX 标记清理：

| 输入 | 输出 | 正则 |
|------|------|------|
| `$75.7\%$` | `75.7%` | `\$([^$]*?)\\%\$` → `\1%` |
| `$\textcircled{1}$` | `①` | `\$\\textcircled\{(\d)\}\$` → 映射表 |
| `$\geqslant 40$` | `>=40` | `\$\\geqslant\s*(\d+)\$` → `>=\1` |
| `$\mathsf{GC}$` | `GC` | `\$\\math\w+\{([^}]+)\}\$` → `\1` |

### 3. 内联术语补回

MinerU 对中文 PDF 中内联英文术语有丢失问题（如 "Globocan 2022"、"GC"）。

**策略**: 针对一个具体的、可观测的故障模式 — MinerU 输出中出现空的中文括号。

**匹配算法（窄范围，高精度）：**

```python
def recover_inline_terms(mineru_md_path, pdf_path):
    """
    检测 MinerU 输出中的空括号模式，从 PyMuPDF 原始文本中补回内容。
    
    目标模式:
      "据全球最新数据（ ），胃癌（ ， ）"
      → 补回为 "据全球最新数据（Globocan 2022），胃癌（Gastric Cancer，GC）"
    
    算法:
    1. 正则检测 MinerU 输出中的 "（ ）" 或 "（ ， ）" 等空括号
    2. 如果无空括号 → 直接返回，不做任何处理
    3. PyMuPDF 提取原始 PDF 同一页的文本
    4. 在 PyMuPDF 文本中，定位空括号前后的中文上下文（取前后各 10 字符作为锚点）
    5. 从 PyMuPDF 锚点匹配区域提取括号内的实际内容
    6. 填回 MinerU 输出
    """
```

**不做的事情：**
- 不做通用段落对齐（复杂度高，收益不确定）
- 不处理非括号形式的术语丢失（如段落中孤立的 "GC" 行）
- 如果 PyMuPDF 提取同样有空括号，跳过（说明原始 PDF 就是这样）

此功能标记为**尽力而为**，不保证 100% 补回。无法补回时静默跳过，不影响其他流程。

### 4. VLM 图片描述管线

#### 4.1 图片分类（过滤）

先对每张图片做分类，仅对有价值的图片生成详细描述：

```python
CLASSIFY_PROMPT = """这张图片属于以下哪个类别？只回答类别名称。
- 流程图：临床决策树、诊疗路径、筛查流程
- 数据图表：统计图、生存曲线、柱状图
- 医学示意图：解剖图、分期示意图、手术示意图
- 表格图片：以图片形式嵌入的表格
- 装饰性图片：logo、封面、页眉装饰、空白页"""
```

| 分类结果 | 处理方式 |
|---------|---------|
| 流程图 | 详细描述（专用提示词） |
| 数据图表 | 数据提取描述 |
| 医学示意图 | 结构描述 |
| 表格图片 | 转为 Markdown 表格 |
| 装饰性图片 | 跳过，不生成描述 |

#### 4.2 描述提示词（按类型）

**流程图提示词：**
```
你是一个医学指南图片描述专家。请用简体中文详细描述这张临床决策流程图中的所有节点、
分支条件和治疗路径。要求：
1）列出每个决策节点和对应的处置方案
2）保留所有英文医学术语和分期标记
3）描述箭头指向的逻辑关系
4）末尾附加逻辑关系汇总表
输出格式为结构化 Markdown 文本。
```

**数据图表提示词：**
```
请用简体中文描述这张医学数据图表。要求：
1）说明图表类型（生存曲线/柱状图/折线图等）
2）列出所有数据组和关键数据点
3）描述主要趋势和统计学差异
4）保留所有 p 值、HR、CI 等统计指标
```

#### 4.3 描述注入格式

在 Markdown 中图片引用下方追加 blockquote：

```markdown
![](images/50c82c3a1e...37.jpg)

> **[图片描述 - 流程图]** NCCN GAST-1 胃癌初始评估与分期流程：
> - cTis 或 cT1a：Medically fit → 多学科会诊(GAST-2)；Nonsurgical candidate → 多学科会诊(GAST-2)
> - Locoregional (cM0, Any N)：Medically fit, potentially resectable → 推荐腹腔镜+细胞学 → 多学科会诊(GAST-2)；
>   Medically fit, surgically unresectable → 考虑腹腔镜+细胞学 → 多学科会诊(GAST-2)；
>   Nonsurgical candidate → 姑息治疗(GAST-9)
> - Stage IV (cM1) → 姑息治疗(GAST-9)
```

#### 4.4 缓存与幂等

- 描述结果持久化为 `image_descriptions.json`
- 已有描述的图片跳过，支持增量处理
- **SHA256(文件内容)** 作为 key（非路径），确保文件移动后缓存仍有效
- 每个 entry 包含: `{hash, classification, description, model, timestamp}`

### 5. 输出目录结构

```
$MEDICAL_GUIDELINES_DIR/
├── source/                          # 原始文件（按指南分子目录）
│   ├── NCCN/
│   │   └── NCCN_GastricCancer_2026.V2_EN.pdf
│   ├── CSCO/
│   │   ├── CSCO_胃癌诊疗指南2025.pdf
│   │   └── CSCO_胃癌诊疗指南2025.docx
│   ├── CACA/
│   │   └── CACA_中国肿瘤整合诊治指南_胃癌2025版.pdf
│   ├── ESMO/
│   │   └── ESMO_PAN-Asia_gastric_cancer_2024.pdf
│   └── JGCA/
│       └── JGCA_gastric_cancer_treatment_guidelines_2025.pdf
├── extracted/                       # 提取输出（QMD 索引此目录）
│   ├── NCCN_GastricCancer_2026.V2_EN.md
│   ├── CSCO_胃癌诊疗指南2025.md
│   ├── CACA_中国肿瘤整合诊治指南_胃癌2025版.md
│   ├── ESMO_PAN-Asia_gastric_cancer_2024.md
│   └── JGCA_gastric_cancer_treatment_guidelines_2025.md
└── images/                          # 提取的图片 + 描述
    ├── NCCN_GastricCancer_2026.V2_EN/
    │   ├── *.jpg
    │   └── image_descriptions.json
    ├── CSCO_胃癌诊疗指南2025/
    │   ├── *.jpg
    │   └── image_descriptions.json
    └── ...
```

## 依赖变更

| 工具 | 安装方式 | 用途 | 许可证 |
|------|---------|------|--------|
| `mineru` | `uv tool install mineru` | PDF → Markdown | AGPL-3.0（构建时） |
| `docling` | `pip install docling`（已有） | DOCX → Markdown | MIT |
| `pymupdf` | `pip install pymupdf` | 内联术语补回 | AGPL-3.0（构建时） |
| LM Studio | 桌面应用（已有） | VLM 图片描述 | 本地推理 |

**不新增运行时依赖** — MinerU 和 PyMuPDF 仅在知识库构建阶段使用。

## 已知限制

1. **MinerU 内联术语丢失**: 中文 PDF 中嵌入的英文术语（如段落中的 "GC"、"Globocan 2022"）可能被丢弃。通过 PyMuPDF 补回策略缓解，但不保证 100%。
2. **MinerU LaTeX 标记**: 百分比、特殊符号会被 LaTeX 化，需后处理清理。
3. **VLM 描述质量**: 依赖 Gemma-4-31B 的视觉理解能力，复杂嵌套流程图可能有遗漏。
4. **DOCX 图片提取**: Docling 的 `image_mode='referenced'` 需验证图片实际导出路径。

## 测试策略

### 自动化测试（pytest）

**单元测试（必须，无外部依赖）：**

| 测试函数 | 验证内容 |
|---------|---------|
| `test_postprocess_latex_percent` | `$75.7\%$` → `75.7%` |
| `test_postprocess_latex_circled` | `$\textcircled{1}$` → `①`，覆盖 1-9 |
| `test_postprocess_latex_geqslant` | `$\geqslant 40$` → `>=40` |
| `test_postprocess_latex_mathsf` | `$\mathsf{GC}$` → `GC` |
| `test_postprocess_latex_passthrough` | 无 LaTeX 标记 → 原文不变 |
| `test_postprocess_latex_mixed` | 混合多种标记的真实段落 |
| `test_recover_empty_parens` | 空括号 `（ ）` 被填回 |
| `test_recover_no_parens` | 无空括号 → no-op |
| `test_recover_pymupdf_fails` | PyMuPDF 异常 → 静默跳过 |
| `test_inject_description` | 图片引用后插入 blockquote |
| `test_inject_no_description` | 无描述 → 图片引用不变 |
| `test_extract_unsupported_format` | `.pptx` → ValueError |

测试数据: 从实际 MinerU 输出中截取的真实片段作为 fixture。

**集成测试（gated，需外部工具）：**

| 测试 | Gate 环境变量 | 验证内容 |
|------|-------------|---------|
| `test_mineru_pdf_extraction` | `MINERU_AVAILABLE=1` | ESMO PDF → .md + images/ |
| `test_docling_docx_extraction` | — (Docling 已安装) | 灰区指南 DOCX → .md |
| `test_vlm_classify_image` | `LM_STUDIO_AVAILABLE=1` | 流程图 → "流程图"，logo → "装饰性图片" |
| `test_vlm_describe_flowchart` | `LM_STUDIO_AVAILABLE=1` | NCCN 流程图 → 含节点和路径的描述 |
| `test_pipeline_no_vlm` | `MINERU_AVAILABLE=1` | Pipeline 在无 LM Studio 时仍完成提取 |

### 性能预估

| 阶段 | 单文件耗时 | 5 文件知识库 |
|------|-----------|------------|
| MinerU PDF 提取 | ~2 min (82 页 CPU) | ~10 min |
| Docling DOCX 提取 | ~30s | ~30s |
| LaTeX 后处理 | <1s | <5s |
| VLM 分类（每图） | ~5s | ~100 张 × 5s = ~8 min |
| VLM 描述（每图） | ~30-60s | ~60 张有效图 × 45s = ~45 min |
| **总计** | — | **提取 ~11 min + VLM ~53 min** |

`describe-images` 命令打印进度：`描述图片 12/60 (NCCN_GAST-2.jpg)...`

## 手动验证矩阵

| 测试场景 | 输入文件 | 验证点 |
|---------|---------|--------|
| PDF 中文（侧栏） | CACA 胃癌 2025 | 无侧栏水印，段落连续 |
| PDF 英文（流程图） | NCCN 胃癌 2026 | 图片提取为文件，无页眉噪声 |
| PDF 英文（连字） | ESMO 2024 | fi/fl 正确渲染 |
| PDF 日文混排 | JGCA 2025 | 文本完整，表格保留 |
| DOCX 中文 | 灰区指南 v1 | 标题层级、表格、术语完整 |
| VLM 流程图 | NCCN GAST-1 图 | 节点/分支/路径完整描述 |
| VLM 中文图 | CACA 筛查流程图 | 中文 OCR 正确，评分体系正确 |
| VLM 装饰图 | 封面/logo | 正确分类为装饰性，跳过描述 |
| 全量提取 | 整个知识库 | 所有文件成功提取，QMD 可索引 |
