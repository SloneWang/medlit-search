---
name: medlit-search
description: 医学文献检索与筛选工作流（逐步执行、每步停止等待用户确认）。当用户需要检索医学文献、做课题的 PICOS 分析、生成 MeSH 检索策略与知网/万方/维普检索式、制定纳入排除标准、按题目摘要初筛、按全文复筛并出证据地图、多来源文献合并去重、下载开放获取原文，或将文献列表导出为 RIS/BibTeX/EndNote XML/PubMed 格式、APA/Harvard/MLA/Chicago/IEEE/Vancouver/GB/T 7714 引用格式、CSV/XLSX 表格时使用。触发词：文献检索、PubMed、PICOS、MeSH、系统综述、Meta 分析、纳排标准、纳入排除标准、文献筛选、初筛、复筛、证据地图、导出参考文献。
---

# medlit-search — 医学文献检索与筛选工作流（逐步中断版）

基于 SciSearch 桌面文献检索工具（同一作者）的 PubMed 检索流程封装：esearch 分页拿 PMID（默认 usehistory，默认按发表日期降序）
→ efetch 拉 MEDLINE 文本或 PubMed XML → 解析提取字段 → 限流 3/s（有 NCBI API key 10/s）。

所有脚本均为**中文 Google 风格 docstring + 核心算法中文注释**，可直接阅读源码。

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

## ⛔ 核心原则一：一步一停，等用户指令

本 skill **绝不自动连续执行多个步骤**。每完成一步：

1. 把该步产物写入该步的中文文件夹（markdown / csv / json）；
2. 用**简短中文**汇报：产物路径、关键数字（命中数、纳入数、去重数、全文获取数）、**下一步请输入什么**；
3. **立即停止，不再往下做**。

用户可以：直接编辑你生成的 markdown / CSV 文件；在对话里输入下一步触发语；或补充新的资料/要求。

> **恢复执行前必须先重新读取该文件**（用户很可能已修改），以文件当前内容为准，不要凭记忆继续。
> 用户改动与对话指令冲突时，以文件为准并在汇报中说明。

## ⛔ 核心原则二：研究参数每次重新确认，不沿用既往偏好

**每一个新课题都是独立的**。不得因为"以前做过 Meta 分析"就默认本次也是 Meta 分析，也不得自行猜测研究类型、时间范围或检索数量——见第 1 步 1.2 节的强制确认清单。题目与对话没说的，**逐一问用户**。

---

## 步骤总览

| 步骤 | 用户输入触发 | 主要产物 | 完成后 |
|---|---|---|---|
| 1 PICOS 分析 | 课题描述（自然语言） | `01-PICOS分析/PICOS分析.md` | ⛔ 停止 |
| 2 生成检索式 + 纳排标准 | "确认PICOS，继续生成检索式" / 直接给 PICOS | `02-检索策略/检索策略.md` + `PubMed检索式.txt` + `纳入排除标准.md` | ⛔ 停止 |
| 3 执行检索 | "确认检索式，开始检索" / 直接给检索式 | `03-检索结果/检索结果.json` + `检索结果.csv` | ⛔ 停止 |
| 4 初筛 | "开始初筛"（可附额外文献） | `04-初筛/初筛报告.md` + `初筛结果.csv` | ⛔ 停止 |
| 5 复筛（全文） | 追加复筛要求 | `05-复筛/复筛报告.md` + `复筛结果.csv` | ⛔ 停止 |
| 6 迭代更新 | 新检索式 / 新文献 | 在原步骤文件夹生成 `_revN` 新文件（不覆盖旧版） | ⛔ 停止 |

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

每个课题一个目录，**项目名、步骤文件夹名、文件名全部用中文**：`<工作区>/medlit/<项目中文名>/`。结构：

```
medlit/
  他汀一级预防/                        ← 项目中文名（示例）
    01-PICOS分析/
      PICOS分析.md                     PICOS 分析（中英对照）+ 检索参数确认结果
    02-检索策略/
      检索策略.md                      思路 + 概念块 + 同义词来源表 + 三平台检索式 + MeSH 校验
      PubMed检索式.txt                 纯 PubMed 检索式，供 --query-file 使用
      纳入排除标准.md                  纳排标准（第 2 步生成，第 4/5 步筛选的依据）
    03-检索结果/
      检索结果.json / 检索结果.csv      PubMed 检索结果（按发表日期降序）
    04-初筛/
      文献池.json / 文献池.csv          合并去重后的文献池
      去重日志.log
      初筛审查表.csv                   prepare 产物（决策/理由列留空）
      初筛结果.json                    apply 回填决策后
      初筛报告.md / 初筛结果.csv
    05-复筛/
      全文清单.csv
      复筛审查表.csv
      复筛结果.json
      复筛报告.md                      含证据地图
      复筛结果.csv
      全文/                            下载的 OA 全文
    导出/                              其它导出产物（题录、引用格式等）
```

> 迭代更新**不设独立文件夹**：新检索式 / 新文献的产物直接放回原步骤文件夹，
> 以 `_revN` 后缀另存新文件（见第 6 步），旧版本一律保留、不覆盖。

---

## 第 1 步：PICOS 分析 → `01-PICOS分析/PICOS分析.md`

### 1.1 PICOS 拆解

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
- 末尾留一节 `## 待确认` 列出推断不确定的点。

### 1.2 检索参数确认（强制，任何课题都必须做）

⚠️ **只根据"本次题目描述 + 当前对话"重新判断下列参数；题目与对话中没给出的，逐一询问用户**。
不得沿用既往课题的偏好，不得自行默认研究类型是 RCT 还是 Meta 分析，不得自行决定数量上限。

| 参数 | 说明 | 题目未说明时 |
|---|---|---|
| S 研究类型 | RCT / 队列研究 / 病例对照 / Meta 分析 / 系统综述 / 指南…（决定检索式里的研究设计过滤块） | ⛔ 必须询问，禁止默认 |
| 时间范围 | 起止年份（决定第 3 步的 `--mindate` / `--maxdate`） | ⛔ 必须询问 |
| 最大检索数量 | 第 3 步 `--limit`（0=无限/全部） | ⛔ 必须询问（可给参考值供拍板）；**未设日期范围时尤其必须确认** |
| 语言限制 | 是否只要英文 / 中文 | 默认不限，向用户确认 |
| 排除的文献类型 | 动物实验、综述、会议摘要、社论、信件… | 列常见项请用户勾选 |

把确认结果写进 `PICOS分析.md` 的 `## 检索参数` 一节，作为第 2、3 步的输入。

> ESearch 页大小 `retmax` 固定为 20（内部参数，不对用户暴露、不询问）；论文总量上限由 `--limit` 控制。

⛔ **写完后立即停止**，汇报文件路径并提示：
「可直接编辑 `01-PICOS分析/PICOS分析.md`；确认后回复 **确认PICOS，继续生成检索式**。」

---

## 第 2 步：生成检索式 + 纳入排除标准 → `02-检索策略/`

1. **MeSH 校验**：对每个核心概念词跑 `pubmed.py mesh`（见下），记录官方 descriptor
   （如 `Heart Failure` → D006333）。命中 MeSH 用 `"XXX"[MeSH Terms]`；
   补充概念词 `"XXX"[Supplementary Concept]`；自由词 `"XXX"[Title/Abstract]` 或 `[All Fields]`。
2. **同义词发散（LLM 负责，这是召回率的关键）**：在 MeSH 校验基础上，为每个概念块补充医学领域常用、
   但**不是 MeSH 标准词**的同义词，把"模型惯性默认"换成显式的人为扩词：
   - **商品名 ↔ 通用名**（如 rivaroxaban ↔ Xarelto）——临床试验常只报商品名；
   - **常用缩写 ↔ 全称**（如 HF ↔ heart failure；T2DM ↔ type 2 diabetes mellitus）；
   - **临床俗称与变体拼写**（如 heart attack ↔ myocardial infarction；estrogen ↔ oestrogen）；
   - **上位 / 下位词适度扩展**（如 statin ↔ HMG-CoA reductase inhibitor ↔ atorvastatin / rosuvastatin）。
   
   在 `检索策略.md` 的**同义词来源表**里逐词写明它属于哪一类、**为什么加**
   （例："Xarelto 是 rivaroxaban 的商品名，多项 RCT 仅以商品名报告结局"），方便用户增删。
3. **组合**：块内 OR、块间 AND，先 P → I（+C 若有）→ O → S 过滤。
4. **同时生成中文平台检索式**（知网 / 万方 / 维普），基于 PICOS 的中文要素词：

   | 平台 | 字段与运算符 | 模板 |
   |---|---|---|
   | 中国知网 CNKI | `SU=`主题 `TI=`题名 `KY=`关键词 `AB=`摘要 `FT=`全文 `AU=`作者；`AND/OR/NOT`（或 `* + -`） | `SU=('他汀'+'HMG-CoA还原酶抑制剂') AND SU=('心血管疾病'+'一级预防') AND (FT='随机' OR FT='RCT')` |
   | 万方 | `主题:()` `题名或关键词:()` `摘要:()` `作者:()`；`and/or/not` | `主题:("他汀" or "HMG-CoA还原酶抑制剂") and 主题:("心血管疾病" or "一级预防") and 摘要:("随机")` |
   | 维普 | `M=`主题 `T=`题名 `K=`关键词 `R=`文摘 `A=`作者；`*`=AND `+`=OR `-`=NOT | `M=(他汀 + HMG-CoA还原酶抑制剂) * M=(心血管疾病 + 一级预防) * R=(随机)` |

   > 中文平台语法随版本会变，生成后**建议用户在平台"专业检索"框内小范围试检微调**。
   > 本 skill 负责给出结构、词表与运算符，不对平台渲染结果负责。
5. **同步生成纳入排除标准**：写 `02-检索策略/纳入排除标准.md`
   （模板见 `references/inclusion_exclusion_template.md`），内容包含：
   - **纳入标准**：逐条对应 PICOS（人群、干预/暴露、对照、结局、研究设计、时间范围、语言）；
   - **排除标准**：明确排除的文献类型与研究情形（如动物实验、综述、会议摘要、样本量为 0 的报告）；
   - **判定细则**：什么情况算"明确不符"（直接 exclude）、什么情况算"信息不足"（uncertain 待全文）。
   
   **第 4、5 步筛选必须以此为依据，不得另立标准**；用户在第 4/5 步提出的新复筛要求，应先回填进该文档再执行。
6. 写入 `02-检索策略/检索策略.md`：概念块表格（意图 / MeSH 词 / 自由词与发散同义词 / 每个词的来源与原因）、
   PubMed 完整检索式、三平台检索式、MeSH 校验结果表、预估命中与备注。
7. 把 **PubMed 检索式单独存 `02-检索策略/PubMed检索式.txt`**（UTF-8，供 `--query-file`，避免 shell 引号问题）。

⛔ **写完后立即停止**，提示：「可编辑 `02-检索策略/` 下三个文件；确认后回复 **确认检索式，开始检索**。」

---

## 第 3 步：执行 PubMed 检索 → `03-检索结果/`

**参数缺失时必须先询问**（不要替用户默认关键范围）；其中研究类型、时间范围、数量上限已在第 1 步 1.2 确认，此处复述给用户最终确认一遍：

| 参数 | 说明 |
|---|---|
| 日期范围 | `--mindate` / `--maxdate`（YYYY 或 YYYY/MM/DD） |
| 日期类型 | `--datetype`（默认 `pdat` 发表日期；`edat` 录入日期；`mdat` MeSH 日期） |
| 拉取总量 | `--limit`（0=无限/全部）。**未设日期范围时必须向用户确认**；受 API 单次 10000 条上限约束 |
| 页大小 | 固定 `retmax=20`（内部参数，不对用户暴露、不询问） |
| 排序 | `--sort`（默认 `pub_date` = 按发表日期**降序**，最新在前；NCBI 官方取值，usehistory 下顺序保留，脚本另做客户端降序双保险） |
| 返回格式 | `--retmode medline`（默认）/ `xml` |
| API key | `--api-key`（或环境变量 NCBI_API_KEY），3/s → 10/s |

> 📌 检索式编码：空格/换行先归一化为单空格、编码为 `+`；`| [ ] "` 等特殊字符按百分号编码
> （与 Biopython 一致，符合 NCBI "special characters must be URL encoded"）；编码后超 500 字符自动改 HTTP POST。

> ⚠️ **日期过滤必须成对给** —— 只传 `--mindate` 时 NCBI esearch 会**静默忽略整个日期过滤**
> （返回全年份，但 metadata 里仍记 mindate，极具迷惑性）。实测：同一检索式
> `--mindate 2025/09/01` → 命中 617（含 1992 年记录）；补 `--maxdate` → 命中 30。
> 拉取后用下列命令抽查年份分布确认生效：
> `python -c "import json,collections;d=json.load(open('03-检索结果/检索结果.json',encoding='utf-8'));print(sorted(collections.Counter((p['date'] or '')[:4] for p in d['papers']).items()))"`
>
> 📌 **PubMed ESearch 单次查询最多返回前 10000 条**。命中超过 10000 时脚本会打印警告；
> 此时应按日期分段查询或改用 EDirect，不要指望一次拉全。

执行（检索式必须经文件传入）：

```
python scripts/pubmed.py search --query-file "02-检索策略/PubMed检索式.txt" \
    --mindate 2020 --maxdate 2026/09/17 --datetype pdat \
    --limit 100 --sort pub_date \
    --out "03-检索结果/检索结果.json"
```

导出为 CSV（含题目、作者、期刊、日期、关键词、摘要与各种文献号）：

```
python scripts/export.py --input "03-检索结果/检索结果.json" --format csv \
    --out "03-检索结果/检索结果.csv" \
    --fields title,authors,journal,date,keywords,abstract,pmid,doi,pmc,mesh,url
```

汇报：命中总数、实际拉取数、字段完整性（缺摘要 / 缺 DOI 篇数）、**首条与末条文献的发表日期（验证降序）**、年份分布抽查结果。
**提醒用户：知网/万方/维普需自行去平台检索，导出题录后可交回本 skill 合并（第 4 步）。**

⛔ **写完后立即停止**。

---

## 第 4 步：初筛 → `04-初筛/`

**筛选依据：`02-检索策略/纳入排除标准.md`**。逐篇对照其中的纳入标准与排除标准判定，理由引用具体条目（如"不符合纳入标准 3：非 RCT"）。

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
python scripts/merge.py --input "03-检索结果/检索结果.json" 用户补充文献.xlsx 用户补充目录/ \
    --out "04-初筛/文献池.json" --csv "04-初筛/文献池.csv" --dedupe-log "04-初筛/去重日志.log"
```

- 去重优先级：归一化 DOI → PMID → 归一化标题+年份；重复记录会合并（字段互补、list 取并集、记录 `sources`）。
- 汇报读入 / 去重后 / 移除各多少篇。

### 4.2 生成待填审查表并逐篇判定

```
python scripts/screen.py prepare --input "04-初筛/文献池.json" --out "04-初筛/初筛审查表.csv" --round 1
```

生成 CSV（UTF-8-SIG，Excel 可直接开），`初筛决策` 与 `初筛理由` 两列留空。
随后**逐篇读 title + abstract 对照纳排标准**填写：

- `include`：明确符合；`exclude`：明确不符（必写理由，引用纳排标准条目）；`uncertain`：信息不足待全文判定。
- 每批 10–20 篇，输出简表（PMID / 题录 / 结论 / 一句话理由）。

回填决策：

```
python scripts/screen.py apply --input "04-初筛/文献池.json" --review "04-初筛/初筛审查表.csv" \
    --out "04-初筛/初筛结果.json" --round 1
```

- 决策值支持中英文：`include/纳入`、`exclude/排除`、`uncertain/待定`；**空值会被跳过不改动**。
- 输出 counts 明细（include / exclude / uncertain / unfilled）。

### 4.3 出报告

写 `04-初筛/初筛报告.md`：PRISMA 式计数（检索 n → 去重后 n → 排除 n → 待定 n → 纳入 n）、
纳入清单、排除清单（含理由）、待定清单、去重说明，并附一句"本轮筛选依据《纳入排除标准.md》第 X 版"。
同时导出 `04-初筛/初筛结果.csv`（初筛后文献全字段，供复核）。

⛔ **写完后立即停止**。用户可改 CSV 后要求重跑 `apply`。

---

## 第 5 步：复筛（通读全文）→ `05-复筛/`

用户先给出**本轮复筛要求**（如"只留 RCT""样本量≥100""必须报告全因死亡"）。
**把新要求先回填进 `02-检索策略/纳入排除标准.md` 的复筛附加标准一节**，再执行筛选。

### 5.1 准备全文

```
python scripts/fulltext.py --input "04-初筛/初筛结果.json" --dir "05-复筛" \
    --provide <用户全文目录或文件> ... \
    --email <你的真实邮箱> \
    --out "05-复筛/全文清单.csv"
```

- 索引用户提供的全文：按文件名中的 **DOI → PMID → PMC → 标题** 匹配（置信度递减）。
- 仍未匹配且有 DOI 的走 **Unpaywall** 合法 OA 渠道补全，下载到 `05-复筛/全文/`。
- 产出 `05-复筛/全文清单.csv`：`是否可判全文 / 获取方式(provided/unpaywall/pmc/none) / 说明`。

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
python scripts/screen.py prepare --input "04-初筛/初筛结果.json" --out "05-复筛/复筛审查表.csv" --round 2
# 填写后
python scripts/screen.py apply --input "04-初筛/初筛结果.json" --review "05-复筛/复筛审查表.csv" \
    --out "05-复筛/复筛结果.json" --round 2
```

`apply --round 2` 会保留 `round1` 的决策，只追加 `round2` / `reason2`。

### 5.3 出报告（含证据地图）

写 `05-复筛/复筛报告.md`：

- 本轮复筛标准（引用更新后的纳入排除标准版本）；
- 全文获取统计（用户全文 n / 本次下载 n / 仍无全文 n）；
- 逐篇结论 + 依据（有全文者引用正文位置，无全文者标注仅摘要）；
- **证据地图**：以 干预 × 结局（或 人群 × 研究设计）作交叉表，格内填文献数与 PMID，
  空格显式标注「证据缺口」；
- 最终纳入清单 + 建议（如需要补检索的方向）。

同时导出 `05-复筛/复筛结果.csv`。

⛔ **写完后立即停止**。

---

## 第 6 步：迭代更新（`_revN` 修订，不覆盖旧版）

用户随时可以更新检索式、新增文献或调整纳排标准。**不新建文件夹**：新产物写回它所属步骤的原文件夹，
文件名末尾追加 `_revN` 后缀另存新文件（rev1、rev2、rev3…），旧版本一律保留、**不得覆盖**。

修订命名规则：

| 情形 | 新文件命名示例 |
|---|---|
| 换了检索式重跑检索 | `03-检索结果/检索结果_rev1.json` / `检索结果_rev1.csv`（再改则 `_rev2`…） |
| 合并新文献后的新文献池 | `04-初筛/文献池_rev1.json` / `文献池_rev1.csv` |
| 重出初筛报告 | `04-初筛/初筛报告_rev1.md` / `初筛结果_rev1.csv` |
| 更新了纳排标准 | `02-检索策略/纳入排除标准_rev1.md`（报告里注明依据的版本） |

典型流程（以"补充检索式后再合并"为例）：

```
# 1) 用新检索式重跑第 3 步，产物以 _revN 另存
python scripts/pubmed.py search --query-file "02-检索策略/PubMed检索式_rev1.txt" \
    --mindate 2020 --maxdate 2026/09/17 --limit 100 \
    --out "03-检索结果/检索结果_rev1.json"

# 2) 旧池在前、新结果在后合并（已有决策不被覆盖），产物同样 _revN 另存
python scripts/merge.py --input "04-初筛/初筛结果.json" "03-检索结果/检索结果_rev1.json" \
    --out "04-初筛/文献池_rev1.json" --csv "04-初筛/文献池_rev1.csv" \
    --dedupe-log "04-初筛/去重日志_rev1.log"
```

- 每份文件独立编号：`_revN` 从 1 开始、按**该文件自身**的修订次数递增，不受其它文件编号影响。
- 后续步骤（初筛 / 复筛 / 导出）一律以**最新 rev 的文件**为输入；确需引用旧版时须显式写明 rev 号。
- **更新纳排标准** → 另存 `纳入排除标准_revN.md`，并在筛选报告中注明依据的版本。
- 重新生成**受影响步骤**的审查表与报告：已确认的决策保留，只对新文献补判。

> 合并顺序建议**旧池在前、新结果在后**：`merge.py` 遇到重复记录时以先出现的为基准保留，
> 后到的只补空字段与 `decision`，这样已有判定不会被新批次覆盖。

⛔ 每完成一次更新即停止并汇报。

---

## 脚本速查

| 脚本 | 用途 | 关键命令 |
|---|---|---|
| `pubmed.py` | 检索 / 拉取 / MeSH 校验（默认 `--sort pub_date` 最新在前） | `search --query-file q.txt --mindate --maxdate --limit --out`<br>`fetch --ids ...`<br>`mesh --terms ...` |
| `import.py` | 单文件导入为统一 JSON（`--format auto` 可自动识别 json/csv/xlsx/ris/bibtex/medline） | `--input x.xlsx --format auto --out x.json` |
| `merge.py` | **多源汇总 + 去重**（文件/目录混合） | `--input a.json b.xlsx dir/ --out pool.json --csv pool.csv --dedupe-log d.log` |
| `screen.py` | `prepare` 生成待填审查表 / `apply` 回填决策 / 默认关键词预筛 | `prepare --round 1`<br>`apply --round 2` |
| `fulltext.py` | 索引用户提供全文 + Unpaywall OA 补全 + 全文清单 | `--input pool.json --provide dir/ --email you@x.com --out manifest.csv` |
| `export.py` | 导出 RIS/BibTeX/EndNote XML/MEDLINE/引用格式/CSV/XLSX | `--input x.json --format csv --fields ...` |
| `download.py` | 单独批量下载 OA 原文 | `--input selected.json --outdir papers/` |
| `workflow.py` | 一键产出 `all_papers.csv` / `screening_round1.csv` / `selected.csv` | `--dir <任务目录>` |

参数语义严格对齐 NCBI E-utilities：

- ESearch 分页页大小：固定 20（内部常量 `ESearch_PAGE_SIZE`，不开放给用户）。
- `--limit`：用户希望拉取的总量（0=无限/全部）；未设日期范围时须向用户确认。
- `--batch`：EFetch 单批拉取篇数（建议 ≤200）。
- `--retmode`：`medline`（默认）或 `xml`。
- `--usehistory`：默认开启；`--no-usehistory` 退化为 ID 列表模式。
- `--sort`：`pub_date`（默认，按发表日期降序）/ `relevance` / `most_recent` / `journal` / `title`。

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
- **排序默认值以 API 为准，不等同网页版**：PubMed 网页默认 "Most recent"，而 ESearch 默认 `relevance`
  （Best Match）；本 skill 已把默认改为 `pub_date` 保证最新在前，不要擅自改回 `relevance` 而不告知用户。
- **ESearch 单次最多 10000 条**：命中超限按日期分段或 EDirect。
- **检索式编码（已与 Biopython 对齐）**：空格/换行/制表符先归一化为单空格、再编码为 `+`；
  `| [ ] "` 等非 ASCII 字符按百分号编码（符合 NCBI "special characters must be URL encoded"）；
  编码后超 500 字符自动改 HTTP POST（NCBI 对超长查询的建议）。`%2B` 只会出现在字面加号，不会由空格产生。
- **usehistory 下 ESearch 只调一次**：结果集按排序整体挂 History 服务器，EFetch 按 retstart/retmax
  窗口取回；retmax 固定 20 只影响无 History 的退化路径，不会造成大量 ESearch 请求。
- 详细字段含义见 `references/medline_fields.md`；MeSH 策略见 `references/mesh_strategy.md`；
  筛选模板见 `references/screening.md`；纳排标准模板见 `references/inclusion_exclusion_template.md`。

## 合规

- 遵守 NCBI 使用政策：≤3 req/s（有 key ≤10），已内置限流与重试。
- 只下载合法开放获取全文（PMC OA / Unpaywall）；非 OA 文献只保存落地页链接，不绕付费墙。
- 未获取到全文的文献，结论必须标注"仅基于摘要"，不得含糊其辞。
