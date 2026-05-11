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
