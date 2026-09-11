---
name: medlit-search
description: 医学文献检索与筛选工作流（逐步执行、每步停止等待用户确认）。当用户需要检索医学文献、做课题的 PICOS 分析、生成 MeSH 检索策略与知网/万方/维普检索式、按题目摘要初筛、按全文复筛并出证据地图、多来源文献合并去重、下载开放获取原文，或将文献列表导出为 RIS/BibTeX/EndNote XML/PubMed 格式、APA/Harvard/MLA/Chicago/IEEE/Vancouver/GB/T 7714 引用格式、CSV/XLSX 表格时使用。触发词：文献检索、PubMed、PICOS、MeSH、系统综述、Meta 分析、文献筛选、初筛、复筛、证据地图、导出参考文献。
---

# medlit-search — 医学文献检索与筛选工作流（逐步中断版）

基于 SciSearch 桌面文献检索工具（同一作者）的 PubMed 检索流程封装：esearch 分页拿 PMID（默认 usehistory）
→ efetch 拉 MEDLINE 文本或 PubMed XML → 解析提取字段 → 限流 3/s（有 NCBI API key 10/s）。

## 安装（WorkBuddy skill）

本 Skill 托管于 `https://github.com/SloneWang/medlit-search`。\
**稳定版本请从 `v1.5` 分支安装**，不要直接安装 `main`（main 是开发分支）：

```bash
# 用户级（推荐）
git clone -b v1.5 https://github.com/SloneWang/medlit-search.git \
    ~/.workbuddy/skills/medlit-search

# 项目级
git clone -b v1.5 https://github.com/SloneWang/medlit-search.git \
    <你的项目>/.workbuddy/skills/medlit-search
```

重启 WorkBuddy 会话后即可通过触发词调用。

---

## ⛔ 核心原则：一步一停，等用户指令

本 skill **绝不自动连续执行多个步骤**。每完成一步：

1. 把该步产物写入任务目录（markdown / csv / json）；
2. 用**简短中文**汇报：产物路径、关键数字（命中数、纳入数、去重数、全文获取数）、**下一步请输入什么**；
3. **立即停止，不再往下做**。

用户可以：直接编辑你生成的 markdown / CSV 文件；在对话里输入下一步触发语；或补充新的资料/要求。

> **恢复执行前必须先重新读取该文件**（用户很可能已修改），以文件当前内容为准，不要凭记忆继续。
> 用户改动与对话指令冲突时，以文件为准并在汇报中说明。

---

## 步骤总览

| 步骤 | 用户输入触发 | 主要产物 | 完成后 |
|---|---|---|---|
| 1 PICOS 分析 | 课题描述（自然语言） | `01_picos.md` | ⛔ 停止 |
| 2 生成检索式 | "确认PICOS，继续生成检索式" / 直接给 PICOS | `02_strategy.md` + `query_pubmed.txt` | ⛔ 停止 |
| 3 执行检索 | "确认检索式，开始检索" / 直接给检索式 | `03_results.json` + `03_results.csv` | ⛔ 停止 |
| 4 初筛 | "开始初筛"（可附额外文献） | `04_screening_round1.md` + `04_screening_round1.csv` | ⛔ 停止 |
| 5 复筛（全文） | 追加复筛要求 | `05_screening_round2.md` + `05_screening_round2.csv` | ⛔ 停止 |
| 6 迭代更新 | 新检索式 / 新文献 | 更新上述文件 | ⛔ 停止 |

### 输入路由（每次收到用户消息先判断属于哪一步）

| 用户给的是 | 跳到 |
|---|---|
| 自然语言课题（如"他汀用于心血管一级预防的RCT证据"） | 第 1 步 |
| 结构化 PICOS / "确认PICOS，继续…" | 第 2 步 |
| 检索式（含 `[MeSH Terms]` / `AND` 等）或 "确认检索式，开始检索" | 第 3 步 |
| "开始初筛" / 若干文献文件（题录或表格） | 第 4 步 |
| 复筛要求（如"只要RCT、样本量≥100"） | 第 5 步 |
| 新检索式 / 新增文献（在已有流程进行中） | 第 6 步 |

判断有歧义时**直接问用户**，不要猜。

### 工作目录

每个课题一个目录：`<工作区>/medlit/<课题slug>/`。结构：

```
01_picos.md                  PICOS 分析（中英对照）
02_strategy.md               检索策略（思路 + PubMed + 知网/万方/维普 检索式 + MeSH 校验）
query_pubmed.txt             仅 PubMed 检索式，供 --query-file 使用
03_results.json / .csv       PubMed 检索结果
04_pool.json / 04_pool.csv   合并去重后的文献池
04_review_round1.csv         初筛待填审查表（决策列留空）
04_screened.json             回填决策后的初筛结果
04_screening_round1.md / .csv 初筛报告 + 初筛后文献表
05_fulltext_manifest.csv     全文清单
05_screening_round2.md / .csv 复筛报告（含证据地图）+ 复筛后文献表
papers/                      下载的 OA 全文
exports/                     其它导出产物
```

---

## 第 1 步：PICOS 分析 → `01_picos.md`

按 PICOS 拆解课题，输出**中英对照**：

| 要素 | 中文 | English | 检索用词（英文） |
|---|---|---|---|
| P Population | 研究人群 | ... | ... |
| I Intervention | 干预措施 | ... | ... |
| C Comparison | 对照措施 | ... | ... |
| O Outcome | 结局指标 | ... | ... |
| S Study design | 研究类型 | ... | ... |

要求：

- 每个要素给 **2–4 个核心概念词**，英文供检索用，中文供中文平台检索式用。
- 补充：目标数据库、语言限制、排除的文献类型（如动物实验、综述、会议摘要）、时间范围意向。
- 末尾留一节 `## 待确认` 列出推断不确定的点。

⛔ **写完后立即停止**，汇报文件路径并提示：
「可直接编辑 `01_picos.md`；确认后回复 **确认PICOS，继续生成检索式**。」

---

## 第 2 步：生成检索式 → `02_strategy.md` + `query_pubmed.txt`

1. **MeSH 校验**：对每个核心概念词跑 `pubmed.py mesh`（见下），记录官方 descriptor
   （如 `Heart Failure` → D006333）。命中 MeSH 用 `"XXX"[MeSH Terms]`；
   补充概念词 `"XXX"[Supplementary Concept]`；自由词 `"XXX"[Title/Abstract]` 或 `[All Fields]`。
2. **同义词发散**：每个概念块自动补充同义词、缩写、拼写变体、上位词（如
   `statin` → `HMG-CoA reductase inhibitor` / `atorvastatin` / `rosuvastatin`），
   并在文档里**写明每个词为什么加**（MeSH 词 / 同义 / 缩写 / 上位词），方便用户增删。
3. **组合**：块内 OR、块间 AND，先 P → I（+C 若有）→ O → S 过滤。
4. **同时生成中文平台检索式**（知网 / 万方 / 维普），基于 PICOS 的中文要素词：

   | 平台 | 字段与运算符 | 模板 |
   |---|---|---|
   | 中国知网 CNKI | `SU=`主题 `TI=`题名 `KY=`关键词 `AB=`摘要 `FT=`全文 `AU=`作者；`AND/OR/NOT`（或 `* + -`） | `SU=('他汀'+'HMG-CoA还原酶抑制剂') AND SU=('心血管疾病'+'一级预防') AND (FT='随机' OR FT='RCT')` |
   | 万方 | `主题:()` `题名或关键词:()` `摘要:()` `作者:()`；`and/or/not` | `主题:("他汀" or "HMG-CoA还原酶抑制剂") and 主题:("心血管疾病" or "一级预防") and 摘要:("随机")` |
   | 维普 | `M=`主题 `T=`题名 `K=`关键词 `R=`文摘 `A=`作者；`*`=AND `+`=OR `-`=NOT | `M=(他汀 + HMG-CoA还原酶抑制剂) * M=(心血管疾病 + 一级预防) * R=(随机)` |

   > 中文平台语法随版本会变，生成后**建议用户在平台"专业检索"框内小范围试检微调**。
   > 本 skill 负责给出结构、词表与运算符，不对平台渲染结果负责。

5. 写入 `02_strategy.md`：概念块表格（意图 / MeSH 词 / 自由词 / 同义词来源）、
   PubMed 完整检索式、三平台检索式、MeSH 校验结果表、预估命中与备注。
6. 把 **PubMed 检索式单独存 `query_pubmed.txt`**（UTF-8，供 `--query-file`，避免 shell 引号问题）。

⛔ **写完后立即停止**，提示：「可编辑 `02_strategy.md`；确认后回复 **确认检索式，开始检索**。」

---

## 第 3 步：执行 PubMed 检索 → `03_results.*`

**参数缺失时必须先询问**（不要替用户默认关键范围）：

| 参数 | 说明 |
|---|---|
| 日期范围 | `--mindate` / `--maxdate`（YYYY 或 YYYY/MM/DD） |
| 日期类型 | `--datetype`（默认 `pdat` 发表日期；`edat` 录入日期；`mdat` MeSH 日期） |
| 拉取总量 | `--limit`（0=全部；命中大时**务必设**，否则长时间运行） |
| 页大小 | `--retmax`（默认 20，最大 10000） |
| 返回格式 | `--retmode medline`（默认）/ `xml` |
| API key | `--api-key`（或环境变量 NCBI_API_KEY），3/s → 10/s |

> ⚠️ **日期过滤必须成对给** —— 只传 `--mindate` 时 NCBI esearch 会**静默忽略整个日期过滤**
> （返回全年份，但 metadata 里仍记 mindate，极具迷惑性）。实测：同一检索式
> `--mindate 2025/09/01` → 命中 617（含 1992 年记录）；补 `--maxdate` → 命中 30。
> 拉取后用下列命令抽查年份分布确认生效：
> `python -c "import json,collections;d=json.load(open('03_results.json',encoding='utf-8'));print(sorted(collections.Counter((p['date'] or '')[:4] for p in d['papers']).items()))"`

执行（检索式必须经文件传入）：

```
python scripts/pubmed.py search --query-file query_pubmed.txt \
    --mindate 2020 --maxdate 2026/09/11 --datetype pdat \
    --retmax 20 --limit 100 --out 03_results.json
```

导出为 CSV（含题目、作者、期刊、日期、关键词、摘要与各种文献号）：

```
python scripts/export.py --input 03_results.json --format csv --out 03_results.csv \
    --fields title,authors,journal,date,keywords,abstract,pmid,doi,pmc,mesh,url
```

汇报：命中总数、实际拉取数、字段完整性（缺摘要 / 缺 DOI 篇数）、年份分布抽查结果。
**提醒用户：知网/万方/维普需自行去平台检索，导出题录后可交回本 skill 合并（第 4 步）。**

⛔ **写完后立即停止**。

---

## 第 4 步：初筛 → `04_screening_round1.*`

### 4.1 收拢文献（本机 + 用户提供）

用户可提供任意来源的文献，按类型处理：

| 提供物 | 处理 |
|---|---|
| `.ris` `.bib` `.nbib` `.txt`(MEDLINE) | `merge.py` 直接读 |
| `.csv` `.xlsx`（CNKI/万方/维普/EndNote/NoteExpress/Scopus 导出） | `merge.py` 直接读，中文表头自动映射（题名/作者/文献来源/关键词/摘要…） |
| `.json`（本 skill 任一阶段产物） | `merge.py` 直接读 |
| **目录** | `merge.py` 递归扫描上述扩展名 |
| `.caj` `.kdh` | **无法解析**，请用户另存 PDF 或在平台导出题录 |
| 网络路径 / URL | 先让用户下载到本地再给路径 |
| 全文 `.pdf` `.docx` 等 | 留到**第 5 步**，初筛阶段不参与 |

合并去重（**旧池在前、新结果在后**，可保留已有决策）：

```
python scripts/merge.py --input 03_results.json 用户补充文献.xlsx 用户补充目录/ \
    --out 04_pool.json --csv 04_pool.csv --dedupe-log 04_dedupe.log
```

- 去重优先级：归一化 DOI → PMID → 归一化标题+年份；重复记录会合并（字段互补、list 取并集、记录 `sources`）。
- 汇报读入 / 去重后 / 移除各多少篇。

### 4.2 生成待填审查表并逐篇判定

```
python scripts/screen.py prepare --input 04_pool.json --out 04_review_round1.csv --round 1
```

生成 CSV（UTF-8-SIG，Excel 可直接开），`初筛决策` 与 `初筛理由` 两列留空。
随后**逐篇读 title + abstract 对照 PICOS 五要素**填写：

- `include`：明确符合；`exclude`：明确不符（必写理由）；`uncertain`：信息不足待全文判定。
- 每批 10–20 篇，输出简表（PMID / 题录 / 结论 / 一句话理由）。

回填决策：

```
python scripts/screen.py apply --input 04_pool.json --review 04_review_round1.csv \
    --out 04_screened.json --round 1
```

- 决策值支持中英文：`include/纳入`、`exclude/排除`、`uncertain/待定`；**空值会被跳过不改动**。
- 输出 counts 明细（include / exclude / uncertain / unfilled）。

### 4.3 出报告

写 `04_screening_round1.md`：PRISMA 式计数（检索 n → 去重后 n → 排除 n → 待定 n → 纳入 n）、
纳入清单、排除清单（含理由）、待定清单、去重说明。
同时导出 `04_screening_round1.csv`（初筛后文献全字段，供复核）。

⛔ **写完后立即停止**。用户可改 CSV 后要求重跑 `apply`。

---

## 第 5 步：复筛（通读全文）→ `05_screening_round2.*`

用户先给出**本轮复筛要求**（如"只留 RCT""样本量≥100""必须报告全因死亡"）。

### 5.1 准备全文

```
python scripts/fulltext.py --input 04_screened.json --dir . \
    --provide <用户全文目录或文件> ... \
    --email <你的真实邮箱> \
    --out 05_fulltext_manifest.csv
```

- 索引用户提供的全文：按文件名中的 **DOI → PMID → PMC → 标题** 匹配（置信度递减）。
- 仍未匹配且有 DOI 的走 **Unpaywall** 合法 OA 渠道补全，下载到 `papers/`。
- 产出 `05_fulltext_manifest.csv`：`是否可判全文 / 获取方式(provided/unpaywall/pmc/none) / 说明`。

> ⚠️ **`--email` 必须是真实邮箱**，或用环境变量 `UNPAYWALL_EMAIL`。
> Unpaywall 对占位邮箱（如 `example.com`）返回 **HTTP 422**，脚本检测到占位邮箱会**直接跳过网络获取**
> 并在 note 里说明，避免每篇空耗超时。
> ⚠️ 下载校验：`data[:4] == b"%PDF"` **且** `len(data) >= 20000` 双重校验。
> PMC 常返回 ~1.8 KB 反爬 HTML（魔数 `\n\n\n\n`），DOAJ/Cloudflare 可能返回几百 KB 拦截页（魔数 `<!DO`）——
> **只看大小会把拦截页当 PDF 落盘**。
> ⚠️ 订阅/混合出版为主的课题，脚本全文获取率可能为 **0**，这属于正常结果；
> 报告里如实写"N/M 篇取到全文、其余仅基于摘要"，并附 DOI 与落地页供用户走机构权限。
> ⚠️ 下载耗时长，**必须后台执行**。

### 5.2 判定

- **有全文**：用 Read 通读 PDF/DOCX 正文，按复筛要求判定，理由里引用具体章节/页码/表格。
- **无全文**：仅据题目 + 摘要判定，并在结论中显式标注「**仅基于摘要**」，不得伪装成已读全文。

```
python scripts/screen.py prepare --input 04_screened.json --out 05_review_round2.csv --round 2
# 填写后
python scripts/screen.py apply --input 04_screened.json --review 05_review_round2.csv \
    --out 05_screened.json --round 2
```

`apply --round 2` 会保留 `round1` 的决策，只追加 `round2` / `reason2`。

### 5.3 出报告（含证据地图）

写 `05_screening_round2.md`：

- 本轮复筛标准；
- 全文获取统计（用户全文 n / 本次下载 n / 仍无全文 n）；
- 逐篇结论 + 依据（有全文者引用正文位置，无全文者标注仅摘要）；
- **证据地图**：以 干预 × 结局（或 人群 × 研究设计）作交叉表，格内填文献数与 PMID，
  空格显式标注「证据缺口」；
- 最终纳入清单 + 建议（如需要补检索的方向）。

同时导出 `05_screening_round2.csv`。

⛔ **写完后立即停止**。

---

## 第 6 步：迭代更新

用户随时可以：

- **更新检索式** → 重跑第 3 步得到新结果 → 合并进池：
  ```
  python scripts/merge.py --input 04_screened.json 03_results_v2.json \
      --out 04_pool_v2.json --csv 04_pool_v2.csv
  ```
- **新增文献** → 同样 `merge.py` 合并。
- 重新生成**受影响步骤**的审查表与报告：已确认的决策保留，只对新文献补判。

> 合并顺序建议**旧池在前、新结果在后**：`merge.py` 遇到重复记录时以先出现的为基准保留，
> 后到的只补空字段与 `decision`，这样已有判定不会被新批次覆盖。

⛔ 每完成一次更新即停止并汇报。

---

## 脚本速查

| 脚本 | 用途 | 关键命令 |
|---|---|---|
| `pubmed.py` | 检索 / 拉取 / MeSH 校验 | `search --query-file q.txt --mindate --maxdate --limit --out`<br>`fetch --ids ...`<br>`mesh --terms ...` |
| `import.py` | 单文件导入为统一 JSON（`--format auto` 可自动识别 json/csv/xlsx/ris/bibtex/medline） | `--input x.xlsx --format auto --out x.json` |
| `merge.py` | **多源汇总 + 去重**（文件/目录混合） | `--input a.json b.xlsx dir/ --out pool.json --csv pool.csv --dedupe-log d.log` |
| `screen.py` | `prepare` 生成待填审查表 / `apply` 回填决策 / 默认关键词预筛 | `prepare --round 1`<br>`apply --round 2` |
| `fulltext.py` | 索引用户提供全文 + Unpaywall OA 补全 + 全文清单 | `--input pool.json --provide dir/ --email you@x.com --out manifest.csv` |
| `export.py` | 导出 RIS/BibTeX/EndNote XML/MEDLINE/引用格式/CSV/XLSX | `--input x.json --format csv --fields ...` |
| `download.py` | 单独批量下载 OA 原文 | `--input selected.json --outdir papers/` |
| `workflow.py` | 一键产出 `all_papers.csv` / `screening_round1.csv` / `selected.csv` | `--dir <任务目录>` |

参数语义严格对齐 NCBI E-utilities：

- `--retmax`：ESearch 单次返回 UID 数（分页页大小，默认 20，最大 10000）。
- `--limit`：用户希望拉取的总量（0=全部）。
- `--batch`：EFetch 单批拉取篇数（建议 ≤200）。
- `--retmode`：`medline`（默认）或 `xml`。
- `--usehistory`：默认开启；`--no-usehistory` 退化为 ID 列表模式。

## 导出（任何步骤随时可用）

| 类别 | 格式 |
|---|---|
| 数据交换 | `ris` `bibtex` `enxml` `medline` |
| 引用格式 | `apa` `harvard` `mla` `chicago` `ieee` `vancouver` `gbt7714` |
| 电子表格 | `csv` `xlsx` |

`--fields` 可选：title, authors, journal, date, abstract, keywords, mesh, pub_types,
volume, issue, pages, pmid, doi, pmc, arxiv, url。
依赖：Python ≥ 3.10；除 xlsx 外仅需标准库，xlsx 需 `openpyxl`。

## 已知坑（Windows + WorkBuddy 实测）

- **Git Bash 经常 fork 失败**（0xC0000142）：一律用 PowerShell 工具调脚本。
- **PowerShell 工具不回显 stdout**：输出重定向到文件再 Read。
- **Start-Process -ArgumentList 不加引号**：检索式、多词术语一律走 `--query-file`。
- **长任务必须后台执行**（下载、大批量检索），前台约 2 分钟会被杀。
- **PMC `oa.fcgi` 端点目前对所有 PMCID 返回 404**，会把所有文献误报 `not_oa`；
  判断方法见第 5 步的 Unpaywall 回退说明，不要把它当成"均已核对"。
- **Unpaywall 占位邮箱 → HTTP 422**，且 `url_for_pdf` 常为 null（需 fallback 到落地页 `url`），
  出版社直链需带 UA + Referer。
- 详细字段含义见 `references/medline_fields.md`；MeSH 策略见 `references/mesh_strategy.md`；
  筛选模板见 `references/screening.md`。

## 合规

- 遵守 NCBI 使用政策：≤3 req/s（有 key ≤10），已内置限流与重试。
- 只下载合法开放获取全文（PMC OA / Unpaywall）；非 OA 文献只保存落地页链接，不绕付费墙。
- 未获取到全文的文献，结论必须标注"仅基于摘要"，不得含糊其辞。
