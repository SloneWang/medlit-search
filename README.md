# medlit-search

医学文献检索与筛选工作流 · Medical literature search & screening workflow（当前支持 PubMed）

一套**既可独立作为命令行工具、也可作为 [WorkBuddy](https://www.workbuddy.cn/) AI 助手技能**使用的医学文献工作流：
从课题出发，完成 PICOS 分析 → MeSH 检索策略 → PubMed 检索 → 摘要初筛/复筛 → 开放获取全文下载 → 多种格式导出的完整闭环。

检索流程移植自作者的桌面文献检索工具 **SciSearch**（C#/F#），并对其中解析与 API 语义问题做了修正。

## 稳定版本

**请从 `v1.4` 分支安装稳定版本**，`main` 分支为开发分支，可能包含未充分验证的改动。

```bash
# 用户级（推荐）
git clone -b v1.4 https://github.com/SloneWang/medlit-search.git \
    ~/.workbuddy/skills/medlit-search

# 项目级
git clone -b v1.4 https://github.com/SloneWang/medlit-search.git \
    <你的项目>/.workbuddy/skills/medlit-search
```

## 快速开始（命令行）

```bash
# 1. 检索（检索式写入文件可避免 shell 引号问题）
echo '("Heart Failure"[MeSH Terms]) AND "SGLT2 inhibitor*"[Title/Abstract]' > query.txt
python scripts/pubmed.py search --query-file query.txt --mindate 2023 --retmax 20 --limit 100 --out results.json

# 2. 校验术语是否为 MeSH 主题词
python scripts/pubmed.py mesh --terms-file terms.txt --out mesh_check.json

# 3. 导出（任选多种格式）
python scripts/export.py --input results.json --format ris  --out refs.ris
python scripts/export.py --input results.json --format gbt7714 --out refs.txt
python scripts/export.py --input results.json --format xlsx --out papers.xlsx

# 4. 基于摘要关键词批量预筛
python scripts/screen.py --input results.json \
    --include "empagliflozin,cardiovascular death,randomized" \
    --exclude "mice,rat,animal,in vitro" \
    --field title+abstract --mode and --out screening.json

# 5. 一键导出审查 CSV
python scripts/workflow.py --dir <任务目录>

# 6. 下载开放获取原文
python scripts/download.py --input selected.json --outdir papers/
```

## 目录结构

```
medlit-search/
├── SKILL.md                  # WorkBuddy 技能定义（六步交互流程）
├── README.md                 # 本文件
├── scripts/
│   ├── pubmed.py             # PubMed 检索 / 按 PMID 拉取 / MeSH 校验（纯标准库）
│   ├── import.py             # RIS/BibTeX/MEDLINE 导入为统一 JSON（保留 abstract）
│   ├── screen.py             # 基于摘要/标题关键词的初筛/复筛
│   ├── export.py             # 多种格式导出器（xlsx 需 openpyxl）
│   ├── download.py           # PMC 开放获取原文下载
│   └── workflow.py           # 一键导出 all/screening/selected CSV
├── references/
│   ├── medline_fields.md     # MEDLINE 字段速查
│   ├── mesh_strategy.md      # MeSH 检索策略构造规则
│   └── screening.md          # 初筛/复筛模板（PRISMA）
└── LICENSE                   # MIT
```

## 技术栈

- Python ≥ 3.10
- 除 XLSX 导出需 `openpyxl` 外，全部功能仅用 Python 标准库
- 代码中所有变量、常量、函数参数/返回值/局部变量、类字段/属性均带有类型注解

## 版本分支

| 分支 | 说明 |
|---|---|
| `main` | 开发分支，包含最新但可能未充分验证的改动 |
| `v1.4` | **稳定分支**，推荐生产环境 / WorkBuddy 安装 |

## 合规声明

- 遵循 [NCBI E-utilities 使用政策](https://www.ncbi.nlm.nih.gov/books/NBK25497/)：未配 API key ≤3 req/s，配置后 ≤10 req/s，脚本已内置限流。
- 原文下载仅使用 PMC 开放获取（OA Subset）合法渠道；非 OA 文献只保存 DOI/PubMed 落地页链接。
- 本工具输出仅供科研参考，不构成医学建议。

## License

[MIT](LICENSE) © 2026 王天乐 (Wang Tianle / SloneWang)
