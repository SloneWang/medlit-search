---
name: medlit-search
description: 医学文献检索与筛选工作流（当前支持 PubMed）。当用户需要检索医学文献、做课题的 PICOS 分析、生成 MeSH 检索策略、按摘要初筛/复筛文献、下载开放获取原文，或将文献列表导出为 RIS/BibTeX/EndNote XML/PubMed 格式、APA/Harvard/MLA/Chicago/IEEE/Vancouver/GB/T 7714 引用格式、CSV/XLSX 表格时使用。触发词：文献检索、PubMed、PICOS、MeSH、系统综述、Meta 分析、文献筛选、导出参考文献。
---

# medlit-search — 医学文献检索与筛选工作流

基于 SciSearch 桌面文献检索工具（同一作者）的 PubMed 检索流程封装：esearch 分页拿 PMID →
efetch 拉 MEDLINE 文本 → 解析提取字段 → 限流 3/s（有 NCBI API key 10/s）。

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

用户输入必为三者之一，先判断再跳转：
- **课题描述**（自然语言，如"SGLT2 抑制剂对心衰患者预后的影响"）→ 走第 2 步
- **PICOS 分析**（已按 P/I/C/O/S 结构化）→ 走第 3 步
- **检索式**（含 [MeSH Terms]/[All Fields]/AND/OR 等 PubMed 语法）→ 走第 4 步

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

询问两个参数（用户不答则用默认）：
- **日期范围**：`--mindate`/`--maxdate`（YYYY 或 YYYY/MM/DD，按发表日期 pdat 过滤）
- **最大篇数**：`--retmax`（默认 100；0 = 全部，需提醒大结果集耗时）

执行（检索式必须经文件传入）：

```
python scripts/pubmed.py search --query-file query.txt \
    --mindate 2020 --retmax 100 --out results.json
```

- 有 NCBI API key 时加 `--api-key`（或设环境变量 NCBI_API_KEY），速度 3/s→10/s。
- 完成后报告：命中总数、实际拉取数、字段完整性（缺摘要/缺 DOI 的篇数）。
- 任何一步用户都可以要求导出（见"导出"节）。

### 第 5 步：初筛与复筛（基于摘要，对照 PICOS）

**初筛**（我逐篇完成，不需要脚本）：
1. 读取 results.json，逐篇看 title+abstract 对照 PICOS 五要素。
2. 每篇给结论：`include`（明确符合）/ `exclude`（明确不符，写排除理由）/
   `uncertain`（摘要信息不足，需全文判定）。
3. 批量操作时每批 10-20 篇，输出简表（PMID、题录、结论、一句话理由）。
4. 结果写入 `screening.json`：在 results.json 的每篇 paper 上加
   `"decision": {"round1": "include|exclude|uncertain", "reason": "..."}` 后另存；
   同时写 `screening.md` 供人读（含 PRISMA 式计数：检索 n → 排除 n → 待定 n → 纳入 n）。
5. 给用户看汇总，**排除清单必须经用户过目**。

**复筛**：按用户追加的标准（如"只留 RCT"、"排除动物实验"、"只要近 5 年"）
对 include/uncertain 集合再过一遍，决策记入 `decision.round2`。
用户确认最终纳入集后，抽出入选论文存 `selected.json`：
`export.py --format xlsx` 或直接用 --ids 过滤均可生成子集。

### 第 6 步：原文下载（仅开放获取）

```
python scripts/pubmed.py fetch --ids <pmid列表> --out selected.json   # 如需补齐元数据
python scripts/download.py --input selected.json --outdir papers/
```

- 只走合法 OA 渠道：PMC OA Subset（oa.fcgi，含 deprecated 路径兼容）；
  拿不到 OA 的，manifest.json 记录 DOI/PubMed 落地页，提示用户走机构权限。
- tgz 包会自动尝试解出内含 PDF。
- **下载耗时较长时必须用后台任务执行**（10MB/篇级别，前台 shell 会被超时杀掉）。

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
  默认 `title,authors,journal,date,doi,url`。
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
