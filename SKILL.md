---
name: medlit-search
description: 医学文献检索与筛选工作流（逐步执行、每步停止等待用户确认）。当用户需要检索医学文献、做课题的 PICOS 分析、生成 MeSH 检索策略与知网/万方/维普检索式、制定纳入排除标准、按题目摘要初筛、按全文复筛并出证据地图、多来源文献合并去重、下载开放获取原文，或将文献列表导出为 RIS/BibTeX/EndNote XML/PubMed 格式、APA/Harvard/MLA/Chicago/IEEE/Vancouver/GB/T 7714 引用格式、CSV/XLSX 表格时使用。触发词：文献检索、PubMed、PICOS、MeSH、系统综述、Meta 分析、纳排标准、纳入排除标准、文献筛选、初筛、复筛、证据地图、导出参考文献。
---

# medlit-search v1.7 — 医学文献检索与筛选工作流

PubMed 检索流程封装（esearch → efetch → MEDLINE 解析 → 限流 3/s，有 key 10/s），
支持知网/万方/维普检索式生成、多源合并去重、摘要初筛、全文复筛与证据地图、多格式导出。

## 依赖（Agent 执行前先确认已安装）

| 依赖 | 必要性 | 安装 |
|---|---|---|
| Python ≥ 3.10 | 必需 | — |
| `aiohttp` | 必需 | `pip install aiohttp` |
| `openpyxl` | 可选（XLSX 导出） | `pip install openpyxl` |
| NCBI API key | 可选 | 限速 3→10 req/s，环境变量 `NCBI_API_KEY` |

完整清单见 `requirements.txt`。网络需可达 `eutils.ncbi.nlm.nih.gov`、`id.nlm.nih.gov`、`api.unpaywall.org`、`ftp.ncbi.nlm.nih.gov`。

## 安装（跨 Agent 通用）

本 Skill 托管于 `https://github.com/SloneWang/medlit-search`。**稳定版本从 `v1.7` 标签安装**，不要直接装 `main`：

```bash
# 任意 Agent 的技能目录（按你的 Agent 文档放到对应 skills 路径）
git clone -b v1.7 https://github.com/SloneWang/medlit-search.git <skills目录>/medlit-search

# 常见路径示例
# MiMo Desktop / MiMoCode:  ~/.config/mimocode/skills/medlit-search
# WorkBuddy:                ~/.workbuddy/skills/medlit-search
# Claude Code 等:           按其插件/技能目录约定放置
```

也可直接在任意目录使用 CLI：`python scripts/pubmed.py ...`。安装后重启 Agent 会话即可用触发词调用。

---

## ⛔ 核心原则（最高优先级，覆盖一切默认行为）

### 原则一：一步一停，等用户指令

本 skill **绝不连续执行多个步骤**。每完成一步：

1. 把产物写入该步的中文文件夹（markdown / csv / json）；
2. 用**简短中文**汇报：产物路径、关键数字、**下一步请输入什么**；
3. **立即停止**——本回合到此结束，**不得调用任何进一步工具，不得开始下一步**。

⛔ 即使你已知道下一步该做什么，也**必须停止**。用户确认前不得推进。
用户可直接编辑你生成的 markdown / CSV；**恢复执行前必须重新读取该文件**（以文件当前内容为准，不要凭记忆继续）。

### 原则二：执行隔离，不读历史记忆

执行本工作流时：

- **禁止读取或引用**任何历史记忆、项目记忆、用户偏好、既往会话记录、产出偏好；
- **禁止**根据"以前做过类似课题"推断本次的研究类型、时间范围、数量上限或输出风格；
- 所有输入以**当前对话 + 用户提供的文件**为准；所有输出格式以**本 SKILL.md** 为准。

### 原则三：研究参数每次重新确认

每一个新课题都是独立的。研究类型（S）、时间范围、最大检索数量等参数：
**只根据本次题目与当前对话判断；没给出的逐一询问用户**，禁止默认 RCT / Meta，禁止自行决定数量上限。

### 原则四：复筛必须读全文

凡能获取到全文（用户提供 PDF/DOCX，或 `fulltext.py`/`download.py` 已下载到本地），
**必须用文件阅读工具打开并通读全文后再判定**，禁止仅凭摘要下结论。
只有全文确实无法获取时，才可仅基于摘要判定，且结论中必须显式标注「**仅基于摘要**」。

---

## 步骤总览

| 步骤 | 用户输入触发 | 主要产物 | 完成后 |
|---|---|---|---|
| 1 PICOS 分析 | 课题描述（自然语言） | `01-PICOS分析/PICOS分析.md` | ⛔ 停止 |
| 2 检索式 + 纳排标准 | "确认PICOS，继续生成检索式" / 直接给 PICOS | `02-检索策略/` 三件套 | ⛔ 停止 |
| 3 执行检索 | "确认检索式，开始检索" / 直接给检索式 | `03-检索结果/检索结果.json` + `.csv` | ⛔ 停止 |
| 4 初筛 | "开始初筛"（可附额外文献） | `04-初筛/初筛报告.md` + `初筛结果.csv` | ⛔ 停止 |
| 5 复筛（全文） | 追加复筛要求 | `05-复筛/复筛报告.md`（含证据地图） | ⛔ 停止 |
| 6 迭代更新 | 新检索式 / 新文献 | 原步骤文件夹内 `_revN` 新文件 | ⛔ 停止 |

**输入路由**：自然语言课题→1；结构化 PICOS→2；检索式或"确认检索式"→3；"开始初筛"或文献文件→4；复筛要求→5；已有流程中的新增→6。有歧义直接问用户，不要猜。

**工作目录**：`<工作区>/medlit/<项目中文名>/`，项目名、文件夹名、文件名全中文：

```
medlit/<项目名>/
  01-PICOS分析/PICOS分析.md
  02-检索策略/  检索策略.md · PubMed检索式.txt · 纳入排除标准.md
  03-检索结果/  检索结果.json · 检索结果.csv
  04-初筛/      文献池.* · 去重日志.log · 初筛审查表.csv · 初筛结果.* · 初筛报告.md
  05-复筛/      全文清单.csv · 复筛审查表.csv · 复筛结果.* · 复筛报告.md · 全文/
  导出/         题录、引用格式等
```

迭代更新不设新文件夹：产物写回原步骤文件夹，文件名加 `_revN` 另存，旧版保留不覆盖。

---

## 第 1 步：PICOS 分析 → `01-PICOS分析/PICOS分析.md`

1. 按 PICOS 拆解课题，输出**中英对照**表格（P/I/C/O/S 各 2–4 个核心概念词，英文供检索，中文供中文平台）。
2. **强制确认检索参数**（题目与对话没给出的，逐一询问）：

| 参数 | 题目未说明时 |
|---|---|
| S 研究类型 | ⛔ 必须询问，禁止默认 |
| 时间范围 | ⛔ 必须询问 |
| 最大检索数量（`--limit`，0=全部） | ⛔ 必须询问（可给参考值供拍板） |
| 语言限制 | 默认不限，向用户确认 |
| 排除的文献类型 | 列常见项请用户勾选 |

3. 末尾留 `## 待确认` 列出推断不确定的点。把确认结果写进 `## 检索参数` 一节。

⛔ **写完后立即停止**，汇报路径并提示：「可编辑 `01-PICOS分析/PICOS分析.md`；确认后回复 **确认PICOS，继续生成检索式**。」

---

## 第 2 步：生成检索式 + 纳入排除标准 → `02-检索策略/`

1. **MeSH 校验**：对每个核心概念词跑 `python scripts/pubmed.py mesh`，记录官方 descriptor。
   命中 MeSH 用 `"XXX"[MeSH Terms]`；补充概念词 `"XXX"[Supplementary Concept]`；自由词 `"XXX"[Title/Abstract]`。
2. **同义词发散（LLM 负责，召回率关键）**：为每个概念块补充非 MeSH 标准同义词，逐词在
   `检索策略.md` 的**同义词来源表**里写明类别与原因：商品名↔通用名、缩写↔全称、俗称与变体拼写、上位/下位词。
3. **组合**：块内 OR、块间 AND，P → I（+C）→ O → S 过滤。
4. **中文平台检索式**（知网/万方/维普）：基于 PICOS 中文要素词，语法见 `references/mesh_strategy.md`。
   生成后建议用户在平台"专业检索"框试检微调。
5. **同步生成** `纳入排除标准.md`（模板 `references/inclusion_exclusion_template.md`）：
   纳入标准逐条对应 PICOS；排除标准明确文献类型与研究情形；判定细则区分"明确不符（exclude）"与"信息不足（uncertain）"。
   **第 4/5 步筛选以此为依据**；用户新要求先回填该文档再执行。
6. 写 `检索策略.md`（概念块表 + 三平台检索式 + MeSH 校验 + 预估命中）。
7. PubMed 检索式单独存 `PubMed检索式.txt`（UTF-8，供 `--query-file`）。

⛔ **写完后立即停止**，提示：「可编辑 `02-检索策略/` 下三个文件；确认后回复 **确认检索式，开始检索**。」

---

## 第 3 步：执行 PubMed 检索 → `03-检索结果/`

**参数缺失必须先询问**；研究类型、时间范围、数量上限复述给用户最终确认。

| 参数 | 说明 |
|---|---|
| 日期范围 | `--mindate` / `--maxdate`（YYYY 或 YYYY/MM/DD）**必须成对给** |
| 日期类型 | `--datetype`（默认 `pdat`） |
| 拉取总量 | `--limit`（0=全部；受 ESearch 单次 10000 条上限约束） |
| 排序 | `--sort`（默认 `pub_date` 发表日期降序，最新在前） |
| 返回格式 | `--retmode medline`（默认）/ `xml` |
| API key | `--api-key` 或 `NCBI_API_KEY` |

> ⚠️ 只传 `--mindate` 时 NCBI 会**静默忽略日期过滤**，必须同时给 `--maxdate`。
> 检索式经文件传入（避免 shell 引号问题）。

```bash
python scripts/pubmed.py search --query-file "02-检索策略/PubMed检索式.txt" \
    --mindate 2020 --maxdate 2026/09/17 --datetype pdat \
    --limit 100 --sort pub_date \
    --out "03-检索结果/检索结果.json"

python scripts/export.py --input "03-检索结果/检索结果.json" --format csv \
    --out "03-检索结果/检索结果.csv" \
    --fields title,authors,journal,date,keywords,abstract,pmid,doi,pmc,mesh,url
```

汇报：命中总数、实际拉取数、字段完整性、首末条日期（验证降序）、年份分布抽查。
提醒：知网/万方/维普需自行去平台检索，导出题录后可交回本 skill 合并（第 4 步）。

⛔ **写完后立即停止**。

---

## 第 4 步：初筛 → `04-初筛/`

**依据：`02-检索策略/纳入排除标准.md`**。逐篇对照判定，理由引用具体条目。

### 4.1 收拢文献

| 提供物 | 处理 |
|---|---|
| `.ris` `.bib` `.nbib` `.txt`(MEDLINE) / `.csv` `.xlsx` / `.json` / 目录 | `merge.py` 直接读（中文表头自动映射） |
| `.caj` `.kdh` | 无法解析，请用户另存 PDF 或导出题录 |
| 全文 PDF/DOCX | 留到第 5 步，初筛不参与 |

```bash
python scripts/merge.py --input "03-检索结果/检索结果.json" 用户补充文献.xlsx 用户补充目录/ \
    --out "04-初筛/文献池.json" --csv "04-初筛/文献池.csv" --dedupe-log "04-初筛/去重日志.log"
```

去重优先级：DOI → PMID → 标题+年份。汇报读入/去重后/移除篇数。**旧池在前、新结果在后**（已有决策不被覆盖）。

### 4.2 逐篇判定

```bash
python scripts/screen.py prepare --input "04-初筛/文献池.json" --out "04-初筛/初筛审查表.csv" --round 1
```

逐篇读 **title + abstract** 对照纳排标准填写：`include` / `exclude`（必写理由）/ `uncertain`（待全文）。
每批 10–20 篇输出简表。回填：

```bash
python scripts/screen.py apply --input "04-初筛/文献池.json" --review "04-初筛/初筛审查表.csv" \
    --out "04-初筛/初筛结果.json" --round 1
```

### 4.3 出报告

写 `初筛报告.md`（PRISMA 计数 + 纳入/排除/待定清单 + 依据版本），导出 `初筛结果.csv`。

⛔ **写完后立即停止**。用户可改 CSV 后要求重跑 `apply`。

---

## 第 5 步：复筛（全文）→ `05-复筛/`

用户先给出**本轮复筛要求**（如"只留 RCT""样本量≥100"）。**新要求先回填** `纳入排除标准.md` 再执行。

### 5.1 准备全文

```bash
python scripts/fulltext.py --input "04-初筛/初筛结果.json" --dir "05-复筛" \
    --provide <用户全文目录或文件> ... \
    --email <真实邮箱> \
    --out "05-复筛/全文清单.csv"
```

- 索引用户全文（DOI → PMID → PMC → 标题匹配）；剩余有 DOI 的走 Unpaywall 合法 OA 补全到 `05-复筛/全文/`。
- `--email` 必须真实邮箱（或 `UNPAYWALL_EMAIL`）；占位邮箱会 422，脚本会跳过网络获取。
- 下载校验：`%PDF` 魔数 **且** ≥20KB 双重校验。订阅制课题获取率可能为 0，属正常。
- **下载耗时长，必须后台执行**。

### 5.2 判定（⛔ 必须读全文）

- **有全文**（用户提供或已下载）：**必须用 Read 工具打开 PDF/DOCX，通读正文**后再按复筛要求判定，
  理由引用具体章节/页码/表格。禁止仅凭题目+摘要对有全文的文献下结论。
- **无全文**：仅据题目+摘要判定，结论显式标注「**仅基于摘要**」，不得伪装成已读全文。

```bash
python scripts/screen.py prepare --input "04-初筛/初筛结果.json" --out "05-复筛/复筛审查表.csv" --round 2
# 填写后
python scripts/screen.py apply --input "04-初筛/初筛结果.json" --review "05-复筛/复筛审查表.csv" \
    --out "05-复筛/复筛结果.json" --round 2
```

### 5.3 出报告（含证据地图）

写 `复筛报告.md`：本轮标准（引用纳排标准版本）、全文获取统计、逐篇结论+依据、
**证据地图**（干预×结局交叉表，格内文献数与 PMID，空格标「证据缺口」）、最终纳入清单与建议。
导出 `复筛结果.csv`。

⛔ **写完后立即停止**。

---

## 第 6 步：迭代更新（`_revN`，不覆盖旧版）

用户更新检索式、新增文献或调整纳排标准时：产物写回**原步骤文件夹**，文件名加 `_revN` 另存
（`_rev1`、`_rev2`…，每份文件独立编号）。旧版一律保留。

```bash
# 示例：新检索式重跑 + 合并
python scripts/pubmed.py search --query-file "02-检索策略/PubMed检索式_rev1.txt" \
    --mindate 2020 --maxdate 2026/09/17 --limit 100 \
    --out "03-检索结果/检索结果_rev1.json"
python scripts/merge.py --input "04-初筛/初筛结果.json" "03-检索结果/检索结果_rev1.json" \
    --out "04-初筛/文献池_rev1.json" --csv "04-初筛/文献池_rev1.csv" \
    --dedupe-log "04-初筛/去重日志_rev1.log"
```

后续步骤一律以**最新 rev 文件**为输入；已确认决策保留，只对新文献补判。
**旧池在前、新结果在后**合并。⛔ 每完成一次更新即停止并汇报。

---

## 脚本速查

| 脚本 | 用途 | 关键命令 |
|---|---|---|
| `pubmed.py` | 检索 / 拉取 / MeSH 校验（默认 `--sort pub_date`） | `search --query-file q.txt --mindate --maxdate --limit --out`<br>`fetch --ids ...`<br>`mesh --terms ...` |
| `import.py` | 单文件导入统一 JSON（`--format auto`） | `--input x.xlsx --format auto --out x.json` |
| `merge.py` | 多源汇总 + 去重 | `--input a.json b.xlsx dir/ --out pool.json --csv pool.csv` |
| `screen.py` | 审查表 prepare / apply / 关键词预筛 | `prepare --round 1` / `apply --round 2` |
| `fulltext.py` | 全文索引 + Unpaywall OA 补全 | `--input pool.json --provide dir/ --email you@x.com --out manifest.csv` |
| `export.py` | RIS/BibTeX/EndNote XML/MEDLINE/引用格式/CSV/XLSX | `--input x.json --format csv --fields ...` |
| `download.py` | 批量下载 PMC OA 原文 | `--input selected.json --outdir papers/` |
| `workflow.py` | 一键导出 all/screening/selected CSV | `--dir <任务目录>` |

参数语义对齐 NCBI E-utilities：`--limit` 总量（0=全部）；`--batch` EFetch 每批（≤200）；
`--retmode medline|xml`；`--usehistory` 默认开；ESearch 页大小固定 20（内部参数，不问用户）。

## 导出（任何步骤随时可用）

| 类别 | 格式 |
|---|---|
| 数据交换 | `ris` `bibtex` `enxml` `medline` |
| 引用格式 | `apa` `harvard` `mla` `chicago` `ieee` `vancouver` `gbt7714` |
| 电子表格 | `csv` `xlsx` |

`--fields` 可选：title, authors, journal, date, abstract, keywords, mesh, pub_types, volume, issue, pages, pmid, doi, pmc, arxiv, url。

## 已知坑

- **Windows**：PowerShell 工具可能不回显 stdout——输出重定向到文件再读；检索式、多词术语一律走 `--query-file` / `--terms-file`。
- **长任务**（下载、大批量检索）必须后台执行，前台约 2 分钟可能被杀。
- **日期过滤必须成对**给 `--mindate` + `--maxdate`，否则 NCBI 静默忽略过滤。
- **ESearch 单次最多 10000 条**，超限按日期分段或用 EDirect。
- **PMC `oa.fcgi` 可能 404**，此时回退 Unpaywall（`fulltext.py`）；不要把"未取到"当成"均已核对"。
- **Unpaywall 占位邮箱 → 422**；`url_for_pdf` 常为 null，需 fallback 落地页。
- **排序默认 `pub_date`**（最新在前），不等同 PubMed 网页默认 "Most recent" 的全部语义，不要擅自改回 `relevance`。
- 详细字段见 `references/medline_fields.md`；MeSH 策略见 `references/mesh_strategy.md`；
  筛选模板见 `references/screening.md`；纳排模板见 `references/inclusion_exclusion_template.md`。

## 合规

- 遵守 NCBI 使用政策：≤3 req/s（有 key ≤10），已内置限流与重试。
- 只下载合法开放获取全文（PMC OA / Unpaywall）；非 OA 只保存落地页链接，不绕付费墙。
- 未获取到全文的文献，结论必须标注"仅基于摘要"。
