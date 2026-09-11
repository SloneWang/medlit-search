# medlit-search

医学文献检索与筛选工作流 · Medical literature search & screening workflow（当前支持 PubMed；可生成知网/万方/维普检索式供手工检索）

一套**既可独立作为命令行工具、也可作为 [WorkBuddy](https://www.workbuddy.cn/) AI 助手技能**使用的医学文献工作流：
从课题出发，完成 PICOS 分析 → 检索策略（PubMed + 中文平台）→ PubMed 检索 → 多源合并去重 → 摘要初筛 → 全文复筛与证据地图 → 多格式导出的完整闭环。

检索流程移植自作者的桌面文献检索工具 **SciSearch**（C#/F#），并对其中解析与 API 语义问题做了修正。

## v1.5：逐步中断式工作流

**每一步执行完即停止，等待用户下一步指令**，用户可随时编辑中间产物：

| 步骤 | 触发 | 产物 |
|---|---|---|
| 1 PICOS 分析 | 课题描述 | `01_picos.md` |
| 2 生成检索式 | "确认PICOS，继续生成检索式" | `02_strategy.md` + `query_pubmed.txt`（含知网/万方/维普检索式） |
| 3 执行检索 | "确认检索式，开始检索" | `03_results.json` / `.csv` |
| 4 初筛 | "开始初筛"（可附任意来源文献） | `04_screening_round1.md` / `.csv` |
| 5 复筛（通读全文） | 追加复筛要求 | `05_screening_round2.md`（含证据地图）/ `.csv` |
| 6 迭代更新 | 新检索式 / 新增文献 | 更新上述产物 |

详细协议见 `SKILL.md`。v1.5 新增能力：

- `merge.py`：多来源文献（PubMed 结果 + RIS/BibTeX/MEDLINE + CNKI/万方/维普导出的 CSV/XLSX + 目录）汇总，按 DOI→PMID→标题+年份去重；
- `import.py`：`--format auto` 自动识别 json/csv/xlsx/ris/bibtex/medline，中文表头自动映射；
- `fulltext.py`：索引用户提供的全文文件 + Unpaywall 合法 OA 补全，产出全文清单供复筛；
- `screen.py`：`prepare` / `apply` 两模式，生成待填审查表并回填初筛/复筛决策，决策可人工复核。

## 稳定版本

**请从 `v1.5` 分支安装稳定版本**，`main` 分支为开发分支，可能包含未充分验证的改动。

```bash
# 用户级（推荐）
git clone -b v1.5 https://github.com/SloneWang/medlit-search.git \
    ~/.workbuddy/skills/medlit-search

# 项目级
git clone -b v1.5 https://github.com/SloneWang/medlit-search.git \
    <你的项目>/.workbuddy/skills/medlit-search
```

WorkBuddy 技能安装入口直接填：`https://github.com/SloneWang/medlit-search.git@v1.5`

## 快速开始（命令行）

```bash
# 1. 检索（检索式写入文件可避免 shell 引号问题）
echo '("Heart Failure"[MeSH Terms]) AND "SGLT2 inhibitor*"[Title/Abstract]' > query.txt
python scripts/pubmed.py search --query-file query.txt \
    --mindate 2023 --maxdate 2026/09/11 --retmax 20 --limit 100 --out 03_results.json

# 2. 校验术语是否为 MeSH 主题词
python scripts/pubmed.py mesh --terms-file terms.txt --out mesh_check.json

# 3. 多来源汇总 + 去重（可混合文件与目录）
python scripts/merge.py --input 03_results.json cnki导出.xlsx 其它题录/ \
    --out 04_pool.json --csv 04_pool.csv --dedupe-log 04_dedupe.log

# 4. 生成待填审查表 → 人工/AI 逐篇填决策 → 回填
python scripts/screen.py prepare --input 04_pool.json --out 04_review_round1.csv --round 1
python scripts/screen.py apply   --input 04_pool.json --review 04_review_round1.csv \
    --out 04_screened.json --round 1

# 5. 复筛：准备全文（用户提供 + OA 补全），必须给真实邮箱
python scripts/fulltext.py --input 04_screened.json --dir . \
    --provide 我的全文/ --email you@example.org --out 05_fulltext_manifest.csv

# 6. 导出（任选多种格式）
python scripts/export.py --input 04_screened.json --format ris     --out refs.ris
python scripts/export.py --input 04_screened.json --format gbt7714 --out refs.txt
python scripts/export.py --input 04_screened.json --format xlsx    --out papers.xlsx
```

## 目录结构

```
medlit-search/
├── SKILL.md                  # WorkBuddy 技能定义（六步逐步中断流程）
├── README.md                 # 本文件
├── scripts/
│   ├── pubmed.py             # PubMed 检索 / 按 PMID 拉取 / MeSH 校验（纯标准库）
│   ├── import.py             # RIS/BibTeX/MEDLINE/CSV/XLSX/JSON 导入为统一 JSON
│   ├── merge.py              # 多来源汇总 + 去重（v1.5）
│   ├── screen.py             # 关键词预筛 + prepare/apply 审查表（v1.5）
│   ├── fulltext.py           # 全文准备：用户提供索引 + Unpaywall OA 补全（v1.5）
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
| `v1.5` | **稳定分支**，推荐生产环境 / WorkBuddy 安装 |
| `v1.4` | 上一稳定版（全量类型注解，无逐步中断流程） |

## 合规声明

- 遵循 [NCBI E-utilities 使用政策](https://www.ncbi.nlm.nih.gov/books/NBK25497/)：未配 API key ≤3 req/s，配置后 ≤10 req/s，脚本已内置限流。
- 原文下载仅使用合法开放获取渠道（PMC OA / Unpaywall）；非 OA 文献只保存 DOI/PubMed 落地页链接，不绕付费墙。
- 未取得全文的文献，筛选结论须标注"仅基于摘要"。
- 本工具输出仅供科研参考，不构成医学建议。

## License

[MIT](LICENSE) © 2026 王天乐 (Wang Tianle / SloneWang)
