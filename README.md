# medlit-search

医学文献检索与筛选工作流 · Medical literature search & screening workflow（当前支持 PubMed）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-blue.svg)](https://www.python.org/)
[![Data source](https://img.shields.io/badge/Data%20source-PubMed%20%2F%20NCBI-green.svg)](https://pubmed.ncbi.nlm.nih.gov/)

一套**既可独立作为命令行工具、也可作为 [WorkBuddy](https://www.workbuddy.cn/) AI 助手技能**使用的医学文献工作流：
从课题出发，完成 PICOS 分析 → MeSH 检索策略 → PubMed 检索 → 摘要初筛/复筛 → 开放获取原文下载 → 13 种格式导出的完整闭环。

检索流程移植自作者的桌面文献检索工具 **SciSearch**（C#/F#），并对其中两处解析问题做了修正（见[开发文档](docs/DEVELOPMENT.md)）。

---

## 特性

- 🔍 **PubMed 检索**：E-utilities 分页检索 + MEDLINE 全文级元数据拉取，内置限流（3 req/s，配 API key 后 10 req/s）与重试
- 🧩 **MeSH 校验**：基于 NLM MeSH RDF API 校验主题词，非主题词自动给出官方建议词
- 📋 **PICOS 工作流**：课题 → PICOS 中英对照 → 双语检索策略 → 初筛/复筛（PRISMA 计数），每步用户确认
- 📦 **13 种导出格式**：RIS / BibTeX / EndNote XML / PubMed(MEDLINE) / APA / Harvard / MLA / Chicago / IEEE / Vancouver / GB/T 7714 / CSV / XLSX
- 🔗 **智能超链接**：XLSX 中 PMID、DOI、PMC、arXiv、网址列自动挂解析链接
- 📄 **OA 原文下载**：PMC 开放获取全文（tgz 自动解出 PDF），非 OA 文献仅记录落地页，不绕付费墙
- 🪶 **零重依赖**：除 XLSX 导出需 `openpyxl` 外，全部功能仅用 Python 标准库

## 快速开始（命令行）

```bash
# 1. 检索（检索式写入文件可避免 shell 引号问题）
echo '("Heart Failure"[MeSH Terms]) AND "SGLT2 inhibitor*"[Title/Abstract]' > query.txt
python scripts/pubmed.py search --query-file query.txt --mindate 2023 --retmax 100 --out results.json

# 2. 校验术语是否为 MeSH 主题词
python scripts/pubmed.py mesh --terms-file terms.txt --out mesh_check.json

# 3. 导出（任选 13 种格式）
python scripts/export.py --input results.json --format ris  --out refs.ris
python scripts/export.py --input results.json --format gbt7714 --out refs.txt
python scripts/export.py --input results.json --format xlsx \
    --fields title,authors,journal,date,keywords,abstract,doi,url --out papers.xlsx

# 4. 下载开放获取原文
python scripts/download.py --input results.json --ids 35228754,34711976 --outdir papers/
```

## 作为 WorkBuddy 技能使用

把整个文件夹复制到 `~/.workbuddy/skills/medlit-search/`（用户级）或项目 `.workbuddy/skills/`（项目级），
重启会话后，对 AI 说"帮我检索 XX 课题的文献"即进入六步引导流程（详见 [SKILL.md](SKILL.md)）。

## 目录结构

```
medlit-search/
├── SKILL.md                  # WorkBuddy 技能定义（六步交互流程）
├── scripts/
│   ├── pubmed.py             # PubMed 检索 / 按PMID拉取 / MeSH 校验（纯标准库）
│   ├── export.py             # 13 种格式导出器（xlsx 需 openpyxl）
│   └── download.py           # PMC 开放获取原文下载
├── references/
│   ├── medline_fields.md     # MEDLINE 字段速查
│   ├── mesh_strategy.md      # MeSH 检索策略构造规则
│   └── screening.md          # 初筛/复筛模板（PRISMA）
├── docs/
│   ├── USAGE.md              # 使用文档
│   └── DEVELOPMENT.md        # 开发文档
└── LICENSE                   # MIT
```

## 文档

- 📖 [使用文档](docs/USAGE.md)：安装、参数详解、完整工作流示例、FAQ
- 🛠️ [开发文档](docs/DEVELOPMENT.md)：架构、数据 Schema、NCBI API 细节、扩展数据源指南

## 合规声明

- 遵循 [NCBI E-utilities 使用政策](https://www.ncbi.nlm.nih.gov/books/NBK25497/)：未配 API key ≤3 req/s，配置后 ≤10 req/s，脚本已内置限流。
- 原文下载仅使用 PMC 开放获取（OA Subset）合法渠道；非 OA 文献只保存 DOI/PubMed 落地页链接。
- 本工具输出仅供科研参考，不构成医学建议。

## English Summary

**medlit-search** is a PubMed literature workflow toolkit (CLI + WorkBuddy skill): PICOS analysis → MeSH-validated search strategy → PubMed search (E-utilities, rate-limited) → abstract-based screening → open-access full-text download from PMC → export to 13 formats (RIS, BibTeX, EndNote XML, MEDLINE, APA/Harvard/MLA/Chicago/IEEE/Vancouver/GB-T 7714 citations, CSV/XLSX with hyperlinked IDs). Python ≥ 3.10, standard library only (except `openpyxl` for XLSX). MIT licensed.

## 致谢

- 检索与解析流程移植自作者的 SciSearch 项目
- 数据服务：[NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25497/) 与 [NLM MeSH RDF API](https://id.nlm.nih.gov/mesh/)

## License

[MIT](LICENSE) © 2026 王天乐 (Wang Tianle / SloneWang)
