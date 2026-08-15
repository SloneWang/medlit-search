# 开发文档 · medlit-search

> 面向贡献者与二次开发者。阅读前建议先看 [USAGE.md](USAGE.md) 了解用户视角。

## 目录

1. [架构总览](#1-架构总览)
2. [数据 Schema](#2-数据-schema)
3. [MEDLINE 解析器](#3-medline-解析器)
4. [NCBI E-utilities 调用细节](#4-ncbi-e-utilities-调用细节)
5. [MeSH 校验](#5-mesh-校验)
6. [PMC OA 下载](#6-pmc-oa-下载)
7. [导出器架构](#7-导出器架构)
8. [扩展指南](#8-扩展指南)
9. [测试](#9-测试)
10. [贡献指南](#10-贡献指南)

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────┐
│ 编排层  SKILL.md（WorkBuddy Skill：六步交互流程定义）      │
│         PICOS 分析 / 策略生成 / 初筛复筛由 LLM 完成        │
├─────────────────────────────────────────────────────────┤
│ 工具层  scripts/pubmed.py   scripts/export.py   scripts/download.py
│         检索+解析+MeSH校验   13种格式导出        OA全文下载  │
├─────────────────────────────────────────────────────────┤
│ 服务层  NCBI E-utilities · NLM MeSH RDF API · PMC OA     │
└─────────────────────────────────────────────────────────┘
```

设计原则：

- **脚本无状态**：每一步读写 JSON 文件，任一步可中断续跑、可单独替换。
- **统一 Paper Schema**：所有工具以同一论文 JSON 结构通信（见下节），
  新数据源只需把数据映射到该 Schema 即可复用导出/下载能力。
- **标准库优先**：除 XLSX（openpyxl）外零三方依赖，降低部署门槛。
- **合规内建**：限流、重试、OA-only 下载写死在工具层，不依赖使用者自觉。

项目源流：检索/解析流程移植自桌面应用 **SciSearch**（C# WPF + F#，
`EUtilities.cs` / `Medline.fs` / `NCBIPaperFactory`），Python 重写时修正了两处问题
（见 [3. MEDLINE 解析器](#3-medline-解析器)）。

## 2. 数据 Schema

全流程统一的论文记录（`papers[]` 元素）：

| 字段 | 类型 | 来源（MEDLINE） | 说明 |
|---|---|---|---|
| `pmid` | string | PMID | 主键 |
| `title` | string | TI | |
| `authors` | string[] | FAU | "Last, First M" 全称 |
| `authors_abbr` | string[] | AU | "Last FM" 缩写 |
| `date` | string | DP | 归一化 `YYYY[-MM[-DD]]` |
| `journal` / `journal_abbr` | string | JT / TA | 全称 / 缩写 |
| `abstract` | string | AB | 初筛依据 |
| `mesh_terms` | string[] | MH | 含限定词；`*` 前缀 = 主要主题词 |
| `keywords` | string[] | OT | 作者关键词 |
| `volume` `issue` `pages` | string | VI / IP / PG | 页码可能为缩写式 `568-74` |
| `pub_types` | string[] | PT | RCT / Review / Meta-Analysis … |
| `language` | string | LA | |
| `doi` `pmc` `arxiv` `pii` | string | AID/LID 标签解析 + PMC 字段 | 见 3.3 |
| `url` | string | 生成 | PubMed 落地页 |
| `_medline` | string | 原始记录文本 | 下划线前缀 = 内部字段；PubMed 格式无损导出用 |

筛选阶段在元素上追加 `decision` 对象（见 [references/screening.md](../references/screening.md)），不改变 Schema 其余部分。

## 3. MEDLINE 解析器

### 3.1 格式规则

- 每条记录由空行分隔；属性行格式 `KEY - value`（KEY 占列 0-3，列 4 为 `-`，值从列 6 起）。
- 值可跨行：不以 `KEY -` 开头的行是上一属性的续行，拼接时以空格连接。

### 3.2 对 SciSearch 的两处修正

1. **发表日期字段**：SciSearch 用 `MHDA`（MeSH 标引完成日期）充当发表日期，
   本实现改用 `DP`（Date of Publication），并用 `_MONTHS` 表把
   `2023 May 15` / `2023 May-Jun` / `2023` 归一化为 ISO 风格。
2. **末属性丢失**：F# 版用 `List.pairwise` 圈定每个属性的行范围，记录内**最后一个
   属性没有后继边界**因而被丢弃（通常损失 SO 或最后一个 OT）。Python 版在
   `flush()` 里显式收尾，无此问题。

### 3.3 标识符提取

`AID`/`LID` 行形如 `10.1038/s41591-021-01659-1 [doi]`，用
`^(?P<value>.+?)\s*\[(?P<tag>[A-Za-z -]+)\]$` 解析标签映射：
`doi→doi`、`pmc/pmcid→pmc（补 PMC 前缀）`、`arxiv→arxiv（去 arXiv: 前缀）`、`pii→pii`；
另有独立 `PMC` 字段兜底。优先级 `setdefault`，首值生效。

## 4. NCBI E-utilities 调用细节

| 端点 | 用途 | 关键参数 |
|---|---|---|
| `esearch.fcgi` | 检索拿 PMID | `db=pubmed, term, retmax, retstart, retmode=json, datetype=pdat, mindate, maxdate, sort` |
| `efetch.fcgi` | 拉元数据 | `db=pubmed, id=逗号分隔, rettype=medline, retmode=text` |

- **限流**：`RateLimiter` 全局串行，间隔 `1/3s`（无 key）或 `1/10s`（有 key），
  与 SciSearch 的 `ResetTimer` 等价。key 经 `--api-key` 或 `NCBI_API_KEY` 注入。
- **重试**：`http_get` 对 429/5xx/网络异常做指数退避（≤4 次，封顶 16s）；4xx 直接抛。
- **分页**：esearch 单批 ≤10000；efetch 默认 200/批，单批失败跳过不中断。
- **tool/email**：始终带 `tool=workbuddy_medlit`；建议调用方提供 `--email`（NCBI 礼仪）。

## 5. MeSH 校验

走 NLM MeSH RDF 查询 API（无需 key）：

```
GET https://id.nlm.nih.gov/mesh/lookup/descriptor?label=<term>&match=exact|contains&limit=10
GET https://id.nlm.nih.gov/mesh/<DID>.json        # 取官方 label
```

判定逻辑：`exact` 命中 → 主题词；否则 `contains` 取前 5 作为建议；都没有 → `not_found`。
注意该 API 只查 **descriptor**；补充概念词（Supplementary Concept，如很多新药）会
返回 `not_found`，属预期行为，SKILL.md 与 references 已提示用 `[Supplementary Concept]` 字段检索。

## 6. PMC OA 下载

```
PMID --(idconv API)--> PMCID --(oa.fcgi)--> 下载链接(pdf/tgz) --(候选链)--> 文件
```

- **idconv**：`https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids=<pmid>&format=json`
- **oa.fcgi**：返回 `<link format="pdf|tgz" href="ftp://..."/>`；`format=pdf` 优先。
- **候选 URL 链**（`candidate_urls`）：oa.fcgi 返回的仍是旧 ftp 链接。
  2026 年 NCBI 把 `/pub/pmc/oa_package`、`oa_pdf`、`oa_bulk` 迁入
  `/pub/pmc/deprecated/`（旧路径 2026-08 起下线）。候选顺序：
  ① deprecated HTTPS 重写 → ② 原路径 HTTPS 重写 → ③ 原始 `ftp://`（urllib 原生支持）。
- **tgz 解包**：OA 包内常含 `<article>.pdf` + JATS XML + 图片；
  `extract_pdf_from_tgz` 自动抽出 PDF 另存，manifest 的 `file` 指向 PDF。
- **合规**：非 OA（oa.fcgi 无记录或 idconv 无 PMCID）只记录落地页，不尝试任何绕墙渠道。

## 7. 导出器架构

`export.py` 单文件注册表模式：

- 引用格式：`CITERS = {name: (fn, prefix_template)}`，`fn(paper)->str`，
  prefix 如 `"[{n}] "`（IEEE/GB/T）、`"{n}. "`（Vancouver）。
- 数据交换：`write_ris / write_bibtex / write_endnote_xml / write_medline`。
- 表格：`cell_value(paper, field)` 统一取值 → CSV（utf-8-sig）/ XLSX（openpyxl，懒导入）。
- 共用工具：`parse_author`（FAU/AU 拆解）、`initials_of`（名→缩写，连字符保留如 `S.-P.`）、
  `expand_pages`（MEDLINE 缩写页码展开 `568-74→568-574`）、`best_url`（ID→解析链接优先级）。

新增格式 = 写一个 `fn` + 注册一行，不碰其他代码。

## 8. 扩展指南

### 8.1 新增数据源（如 Europe PMC / Web of Science）

1. 写 `scripts/<source>.py`，实现与 `pubmed.py search/fetch` 相同的 CLI 契约；
2. 把源数据映射到 [第 2 节](#2-数据-schema) 的 Paper Schema（缺字段填空串/空数组即可）；
3. 导出器、下载器无需改动即可复用；下载器如需支持新源，扩展其 `download_one` 策略链。

### 8.2 新增引用格式

在 `export.py` 写 `cite_xxx(p)->str`，加入 `CITERS` 字典（编号格式给 prefix 模板）。

### 8.3 新增电子表格字段

`FIELDS` + `FIELD_LABELS` 各加一行；如需特殊取值逻辑，改 `cell_value`；
如需超链接，把字段名和 URL 构造函数加进 `ID_URL`。

## 9. 测试

无单测框架，采用真实 API 冒烟测试（限流内小样本）：

```bash
# 检索+解析（应得 5 篇字段完整的 JSON）
echo '"SGLT2 inhibitor"[All Fields] AND "heart failure"[MeSH Terms]' > q.txt
python scripts/pubmed.py search --query-file q.txt --mindate 2023 --retmax 5 --out t.json

# MeSH（"Heart Failure" 应 exact，"SGLT2 inhibitors" 应 not_found）
printf 'Heart Failure\nSGLT2 inhibitors\n' > terms.txt
python scripts/pubmed.py mesh --terms-file terms.txt --out mesh.json

# 13 种格式全部导出
for f in ris bibtex enxml medline apa harvard mla chicago ieee vancouver gbt7714 csv xlsx; do
  python scripts/export.py --input t.json --format $f --out out.$f
done

# 下载（PMC8938265 为 OA；应得 tgz + 解出 PDF）
python scripts/download.py --input t.json --ids 35228754 --outdir papers/
```

Windows 注意：PowerShell 传参会拆分含空格/分号的参数——一律用 `--query-file` / `--terms-file`；
长任务（批量下载）放后台跑。

## 10. 贡献指南

- 欢迎 Issue / PR（bug、新数据源、新导出格式、文档改进）。
- PR 请保持：标准库优先、Paper Schema 兼容、限流合规不被绕过。
-  commit message 用中文或英文均可，描述清楚"为什么改"。
- 行为准则：友善、对事不对人；医学相关内容务必标注来源与不确定性。
