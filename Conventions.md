# Conventions

## 目录结构

```
medical-guidelines-suite/
├── SKILL.md                    # Skill 定义（build + query + batch 三个触发词）
├── scripts/                    # 构建脚本（Docling 提取、索引构建）
├── templates/                  # 报告 Markdown 模板
├── examples/                   # 示例输入/输出
├── docs/                       # 设计文档
├── tests/                      # 测试
├── references/                 # 参考资料
├── Input/                      # 患者 Excel 输入（不入 git）
├── Output/                     # 生成报告输出（不入 git）
├── logs/                       # 运行日志（不入 git）
└── _meta.json                  # skill 元数据
```

## 约定

- `Input/`、`Output/`、`logs/` 均列入 `.gitignore`，不存储患者数据
- 知识库目录（`MEDICAL_GUIDELINES_DIR` 指向）完全在仓库外，不做任何引用
- `extracted/*.md` 是 Docling 从 PDF 提取的中间产物，由脚本生成，不手动编辑
- Skill 的三个入口词（build / query / batch）在 `SKILL.md` 头部声明，不在脚本里硬编码

## KB 目录与文件命名（v3.1 sidecar 约定）

`scripts/kb_metadata.infer_chunk_tags` 通过文件名 token 切分（连字符/下划线/点）匹配
`_SYNONYM_SEED` 词表来推断 `chunks[*].disease_tags`。**KB 目录与 `extracted/*.md`
文件名应避免在 stem 中混入非疾病 token**（如 `pancreas-research.md`、
`liver-cohort-2026.md`、`肝-科研笔记.md`），否则歧义器官 alias（`pancreas`、`liver`、
`stomach`、`pulmonary`、`cervix`、`肝`、`胃`、`肺`）会让该文件被错误归入 pancreatic /
hepatic / gastric / lung / cervical 病种 tags。

推荐命名形式：`<ORG>_<DiseaseName>_<Year>.md` 或 `<DiseaseName>-<Year>-v<N>.md`，
即疾病 token 出现在 stem 中**仅当**该文件确实是疾病专属指南。研究、综述、议程类
文档建议放进独立子目录（如 `research/`、`drafts/`）或加 `_meta_` / `_protocol_`
前缀以避免被 `extract-all` / `cmd_index` 当作指南扫描。

Phase 2 计划引入 stop-token 过滤减少误命中；当前 Phase 1 依赖命名约定。
