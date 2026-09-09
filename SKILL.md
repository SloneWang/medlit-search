---
name: medlit-search
description: 医学文献检索与筛选工作流（当前支持 PubMed）。当用户需要检索医学文献、做课题的 PICOS 分析、生成 MeSH 检索策略、按摘要初筛/复筛文献、下载开放获取原文，或将文献列表导出为 RIS/BibTeX/EndNote XML/PubMed 格式、APA/Harvard/MLA/Chicago/IEEE/Vancouver/GB/T 7714 引用格式、CSV/XLSX 表格时使用。触发词：文献检索、PubMed、PICOS、MeSH、系统综述、Meta 分析、文献筛选、导出参考文献。
---

# medlit-search — 医学文献检索与筛选工作流

基于 SciSearch 桌面文献检索工具（同一作者）的 PubMed 检索流程封装：esearch 分页拿 PMID（默认用 usehistory）→
efetch 拉 MEDLINE 文本或 PubMed XML → 解析提取字段 → 限流 3/s（有 NCBI API key 10/s）。

参数语义严格对齐 NCBI E-utilities：
- `--retmax`：ESearch 单次返回的 UID 数量（分页页大小，默认 20，最大 10000）。
- `--limit`：用户希望拉取的文献总量（0=全部，默认 0）。命中量大时**务必设置**，否则可能长时间运行。
- `--batch`：EFetch 单批拉取篇数（建议 ≤200，默认 200）。
- `--retmode`：EFetch 返回格式，`medline`（默认，保留原始 MEDLINE 文本）或 `xml`（PubMed XML）。
- `--usehistory`：默认开启，用 ESearch 历史会话（WebEnv/query_key）拉取记录，避免长 URL；可 `--no-usehistory` 退化到 ID 列表模式。
- `--mindate`/`--maxdate`/`--datetype`：日期过滤；NCBI 要求必须同时提供 mindate 与 maxdate，若只给 mindate，脚本会自动把 maxdate 设为今天。

## 安装（WorkBuddy skill）

本 Skill 托管于 `https://github.com/SloneWang/medlit-search`。\
**稳定版本请从 `v1.4` 分支安装**，不要直接安装 `main` 分支：

```bash
# 用户级（推荐）
git clone -b v1.4 https://github.com/SloneWang/medlit-search.git \
    ~/.workbuddy/skills/medlit-search

# 项目级
git clone -b v1.4 https://github.com/SloneWang/medlit-search.git \
    <你的项目>/.workbuddy/skills/medlit-search
```

重启 WorkBuddy 会话后即可通过触发词调用。

## 工作目录约定

每个课题在当前工作区建一个目录：`<工作区>/medlit/<课题slug>/`，结构：

```
01_picos.md          PICOS 分析（中英对照，含用户确认记录）
02_strategy.md       检索策略（中英对照 + 最终检索式 + MeSH 校验结果）
results.json         检索结果（pubmed.py 产出，含全部字段与原始 MEDLINE）
screening.md         初筛/复筛记录（每篇：纳入/排除/待定 + 理由）
screening.json       机器可读筛选状态（含 decision 字段的论文数组）
selected.json        最终纳入文献
papers/              下载的原文（PDF/tgz）+ manifest.json
exports/             所有导出产物
```

各步骤产生的 JSON 都是后续步骤的输入，可随时中断续做。

## 标准流程（六步）

### 第 1 步：接收输入，判断类型

用户输入必为以下之一，先判断再跳转：
- **课题描述**（自然语言，如"SGLT2 抑制剂对心衰患者预后的影响"）→ 走第 2 步
- **PICOS 分析**（已按 P/I/C/O/S 结构化）→ 走第 3 步
- **检索式**（含 [MeSH Terms]/[All Fields]/AND/OR 等 PubMed 语法）→ 走第 4 步
- **已有题录文件**（RIS / BibTeX / PubMed MEDLINE）→ 走"导入"分支后进入第 5 步

判断有歧义时直接问用户，不要猜。

### 第 2 步：PICOS 分析（中英对照，必须确认）

按 PICOS 原则拆解课题，输出中英对照表：

| 要素 | 中文 | English |
|---|---|---|
| P (Population) | 研究人群 | ... |
| I (Intervention) | 干预措施 | ... |
| C (Comparison) | 对照措施 | ... |
| O (Outcome) | 结局指标 | ... |
| S (Study design) | 研究类型 | ... |

- 每个要素给出 2-4 个核心概念词（英文用于检索）。
- **必须请求用户修改或确认**后才可继续；用户修改后更新 `01_picos.md`。
- 若用户直接给了 PICOS，原样记录并确认一次即可。

### 第 3 步：生成 MeSH 检索策略（中英双语，必须确认）

1. 对 PICOS 的每个核心概念词，用 `pubmed.py mesh` 校验是否为 MeSH 主题词，
   记录官方 descriptor（如 "Heart Failure" → D006333）；
   非主题词给出官方建议词或标注为自由词/补充概念词。
   - MeSH 词规范：`"XXX"[MeSH Terms]`；补充概念词：`"XXX"[Supplementary Concept]`；
     自由词：`"XXX"[Title/Abstract]` 或 `[All Fields]`。
2. 按"P 词 OR 组内同义词 → I → O"分组成 AND 组合，生成检索式，例如：
   `("Heart Failure"[MeSH Terms] OR "heart failure"[Title/Abstract]) AND ("Sodium-Glucose Transporter 2 Inhibitors"[MeSH Terms] OR "SGLT2 inhibitor*"[Title/Abstract])`
3. 输出**中英对照的策略说明**（每个概念块：意图、MeSH 词、自由词）+ 完整检索式。
4. **必须请求用户修改或确认**。确认后写入 `02_strategy.md`，并把最终检索式
   单独存为 `query.txt`（UTF-8，供 --query-file 使用，避免 shell 引号问题）。
   若用户直接给检索式：跳过生成，但仍可用 mesh 校验帮忙核对，记录后直接进第 4 步。

### 第 4 步：执行检索

询问参数（用户不答则用默认）：
- **日期范围**：`--mindate`/`--maxdate`（YYYY 或 YYYY/MM/DD）
- **日期类型**：`--datetype`（默认 `pdat` 发表日期；可选 `edat` Entrez 录入日期、`mdat` MeSH 日期）

> ⚠️ **日期过滤必须成对给** —— 只传 `--mindate` 时，NCBI esearch 会**静默忽略整个日期过滤**
> （返回全年份结果，脚本 metadata 里仍会记 mindate，极具迷惑性）。实测：同一检索式
> `--mindate 2025/09/01` → 命中 617（含 1992 年记录）；补 `--maxdate 2026/09/09` → 命中 30。
> **务必同时传 `--maxdate`**，并在拉取后用下列命令抽查年份分布确认生效：
> `python -c "import json,collections;d=json.load(open('results.json',encoding='utf-8'));print(sorted(collections.Counter((p['date'] or '')[:4] for p in d['papers']).items()))"`
- **ESearch 页大小**：`--retmax`（默认 20，最大 10000）
- **拉取总量**：`--limit`（默认 0 = 全部；命中量大时**务必设置**）
- **EFetch 格式**：`--retmode medline|xml`（默认 `medline`）

执行（检索式必须经文件传入）：

```
python scripts/pubmed.py search --query-file query.txt \
    --mindate 2020 --datetype pdat --retmax 20 --limit 100 --out results.json
```

- 未设置 `--limit` 且命中 >200 时，脚本会在 stderr 打印警告，提醒用户设置上限。

- 有 NCBI API key 时加 `--api-key`（或设环境变量 NCBI_API_KEY），速度 3/s→10/s。
- 完成后报告：命中总数、实际拉取数、字段完整性（缺摘要/缺 DOI 的篇数）。
- 任何一步用户都可以要求导出（见"导出"节）。

### 导入分支：从已有题录进入工作流

如果用户提供 RIS / BibTeX / PubMed MEDLINE 文件（如从 Zotero / EndNote / SciSearch 导出），
先用 `import.py` 转成统一 JSON，**摘要会被完整保留**：

```
python scripts/import.py --input references.ris --format ris --out results.json
python scripts/import.py --input references.bib --format bibtex --out results.json
python scripts/import.py --input pubmed_result.txt --format medline --out results.json
```

导入后继续第 5 步（初筛/复筛）和第 6 步（导出/下载）。

### 第 5 步：初筛与复筛（基于摘要，对照 PICOS）

**初筛**（先跑 `screen.py` 关键词预筛，再逐篇人工/AI 精读）：
1. 若用户给出明显可程序化的标准，先执行 `screen.py` 做批量预筛（大小写不敏感，支持正则）：
   ```
   # 示例：纳入提到 "empagliflozin" 且摘要/标题出现 "cardiovascular death" 的 RCT；排除动物实验
   python scripts/screen.py --input results.json \
       --include "empagliflozin,cardiovascular death,randomized" \
       --exclude "mice,rat,animal,in vitro" \
       --field title+abstract --mode and --out screening.json
   ```
2. 读取 results.json / screening.json，逐篇看 title+abstract 对照 PICOS 五要素。
3. 每篇给结论：`include`（明确符合）/ `exclude`（明确不符，写排除理由）/
   `uncertain`（摘要信息不足，需全文判定）。
3. 批量操作时每批 10-20 篇，输出简表（PMID、题录、结论、一句话理由）。
4. 结果写入 `screening.json`：在 results.json 的每篇 paper 上加
   `"decision": {"round1": "include|exclude|uncertain", "reason": "..."}` 后另存；
   同时写 `screening.md` 供人读（含 PRISMA 式计数：检索 n → 排除 n → 待定 n → 纳入 n）。
   **screen.py 默认还会生成同名 CSV 审查表 `screening.csv`**（含题目、作者、期刊、发表日期、关键词、MeSH、摘要、决策与理由），便于在 Excel 中复核；不需要时用 `--no-csv` 关闭。
5. 给用户看汇总，**排除清单必须经用户过目**。

**复筛**：按用户追加的标准（如"只留 RCT"、"排除动物实验"、"只要近 5 年"）
对 include/uncertain 集合再过一遍，决策记入 `decision.round2`。
用户确认最终纳入集后，抽出入选论文存 `selected.json`：
`export.py --format xlsx` 或直接用 --ids 过滤均可生成子集。

### 第 6 步：原文下载（仅开放获取）

```
python scripts/pubmed.py fetch --ids <pmid列表> --retmode xml --out selected.json   # 如需补齐元数据
python scripts/download.py --input selected.json --outdir papers/
```

- 只走合法 OA 渠道：PMC OA Subset（oa.fcgi）；
  拿不到 OA 的，manifest.json 记录 DOI/PubMed 落地页，提示用户走机构权限。
- tgz 包会自动尝试解出内含 PDF。
- **下载耗时较长时必须用后台任务执行**（10MB/篇级别，前台 shell 会被超时杀掉）。

> ⚠️ **PMC oa.fcgi 端点目前对所有 PMCID 返回 404**（2026-09 实测：连已知 OA 的
> PMC8279037 也 404），此时 `download.py` 会把**所有文献一律报成 `not_oa`**，
> 并非真的都不可获取。判断方法：用下面的三行脚本探活——若一个确认 OA 的 PMCID 也 404，
> 说明是端点失效而非文献状态问题。
>
> ```python
> import urllib.request
> u="https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC8279037"
> try: print(urllib.request.urlopen(u,timeout=30).status)
> except Exception as e: print("ERR",e)   # ERR HTTP Error 404 → 端点失效
> ```
>
> **回退方案（仍属合法 OA 渠道）：Unpaywall**——用 DOI 查 `oa_locations[].url_for_pdf`，
> 优先取 `host_type == publisher`，下载后校验 `%PDF` 魔数与文件大小 ≥20 KB
> （PMC 的 `/pmc/articles/PMCxxx/pdf/` 直链常返回约 1.8 KB 的反爬 HTML，会被该校验挡掉）：
>
> ```python
> d=json.loads(urllib.request.urlopen(
>     "https://api.unpaywall.org/v2/<DOI>?email=<你的邮箱>", timeout=45).read())
> locs=[x for x in (d.get("oa_locations") or []) if x.get("url_for_pdf")]
> locs.sort(key=lambda x:(x.get("host_type")!="publisher",x["url_for_pdf"]))
> # 取 locs[0]["url_for_pdf"] 下载，校验 data[:4]==b"%PDF" 且 len>20000 再落盘
> ```
>
> 无论走哪条路径，**都应在报告里如实写清实际取到几篇全文**，不要把 `not_oa` 当成"均已核对"。

## 批量导出审查 CSV（推荐在筛选完成后使用）

若任务目录下已有 `results.json`、`screening.json`、`selected.json`，可一键生成三份 CSV：

```
python scripts/workflow.py --dir <任务目录>
```

输出（均在 `--dir` 目录下）：
- `all_papers.csv`：检索到的全部文献
- `screening_round1.csv`：初筛结果（含决策与理由）
- `selected.csv`：最终纳入文献（优先用 `selected.json`；否则取 screening 中 decision.round1/round2 为 include 的文献）

字段：PMID、题目、作者、期刊、发表日期、关键词、MeSH 主题词、摘要、DOI、网址；
screening CSV 额外含初筛/复筛决策与理由。

## 导出（任何步骤随时可用）

```
python scripts/export.py --input <任意阶段JSON> --format <格式> --out <文件> [选项]
```

| 类别 | 格式 | 说明 |
|---|---|---|
| 数据交换 | `ris` `bibtex` `enxml` `medline` | EndNote/Zotero 可导入 RIS 与 EndNote XML；`medline` 即 PubMed 格式（原始文本无损输出） |
| 引用格式 | `apa` `harvard` `mla` `chicago` `ieee` `vancouver` `gbt7714` | 输出 .txt 参考列表，ieee/vancouver/gbt7714 带编号 |
| 电子表格 | `csv` `xlsx` | 字段可选；xlsx 中 PMID/DOI/PMC/arXiv/网址 列自动带超链接 |

选项：
- `--fields`：csv/xlsx 字段，逗号分隔。可选：title, authors, journal, date,
  abstract, keywords, mesh, pub_types, volume, issue, pages, pmid, doi, pmc, arxiv, url。
  默认 `title,authors,journal,date,keywords,abstract,doi,url`（摘要、关键词默认导出，方便初筛/复筛）。
- `--ids` / `--exclude`：按 PMID 选/排子集。
- `--url-source`：网址列取值优先级（auto/doi/pmc/pmid/arxiv，默认 auto = doi>pmc>pmid>arxiv）。

**依赖**：Python ≥ 3.10。除 xlsx 导出外全部仅需标准库；xlsx 导出依赖
`openpyxl`（`pip install openpyxl` 到运行 export.py 所用的解释器环境即可）。

## 环境注意事项（Windows + WorkBuddy 实测，其他环境可忽略）

- **Git Bash 经常 fork 失败**（0xC0000142）：一律用 PowerShell 工具调脚本。
- **PowerShell 工具不回显 stdout**：输出重定向到文件再 Read；长命令用
  `Start-Process ... -Wait -PassThru -RedirectStandardOutput/Error` 模式。
- **Start-Process -ArgumentList 不给含空格/分号的元素加引号**：检索式、多词术语
  一律走 `--query-file` / `--terms-file`。
- **下载等长任务必须 run_in_background**，前台 2 分钟左右会被杀。
- 详细字段含义见 references/medline_fields.md；MeSH 策略规则见
  references/mesh_strategy.md；筛选模板见 references/screening.md。

## 合规

- 遵守 NCBI 使用政策：≤3 req/s（有 key ≤10），已内置限流与重试。
- 只下载 PMC 开放获取全文；非 OA 文献只保存落地页链接，不绕付费墙。
