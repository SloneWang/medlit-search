# 使用文档 · medlit-search

> 适用版本：v1.0 · 数据源：PubMed（NCBI E-utilities）

## 目录

1. [安装与前置条件](#1-安装与前置条件)
2. [pubmed.py：检索 / 拉取 / MeSH 校验](#2-pubmedpy检索--拉取--mesh-校验)
3. [export.py：13 种格式导出](#3-exportpy13-种格式导出)
4. [download.py：开放获取原文下载](#4-downloadpy开放获取原文下载)
5. [完整工作流示例](#5-完整工作流示例)
6. [FAQ](#6-faq)

---

## 1. 安装与前置条件

**要求**：Python ≥ 3.10（3.13 已验证）。克隆或下载本仓库即可，无需安装。

```bash
git clone https://github.com/SloneWang/medlit-search.git
cd medlit-search
```

| 依赖 | 必要性 | 说明 |
|---|---|---|
| Python 标准库 | 必需 | 检索、解析、绝大多数导出格式只用标准库 |
| `openpyxl` | 可选 | 仅 XLSX 导出需要：`pip install openpyxl` |
| NCBI API key | 可选 | 免费申请，把限速从 3 req/s 提到 10 req/s。见 [FAQ](#6-faq) |

**网络**：需要能访问 `eutils.ncbi.nlm.nih.gov`、`id.nlm.nih.gov`、`www.ncbi.nlm.nih.gov`、`ftp.ncbi.nlm.nih.gov`。

---

## 2. pubmed.py：检索 / 拉取 / MeSH 校验

### 2.1 search — 检索并拉取元数据

```bash
python scripts/pubmed.py search --query-file query.txt \
    [--query "..."] [--mindate 2020/01/01] [--maxdate 2026/12/31] \
    [--retmax 100] [--batch 200] [--sort relevance] \
    [--api-key KEY] [--email you@example.com] --out results.json
```

| 参数 | 说明 |
|---|---|
| `--query` / `--query-file` | 二选一（必填）。PubMed 检索式；**推荐写文件**，避免 shell 引号/空格被拆 |
| `--mindate` `--maxdate` | 发表日期过滤（`datetype=pdat`），格式 `YYYY` 或 `YYYY/MM/DD` |
| `--retmax` | 最多拉取篇数；`0` = 全部命中（大结果集耗时长） |
| `--batch` | efetch 每批篇数（默认 200，≤500） |
| `--sort` | `relevance`（默认）/ `pub_date` |
| `--api-key` | NCBI API key；也可用环境变量 `NCBI_API_KEY` |
| `--out` | 输出 JSON 路径；`-` 输出到 stdout |

流程：esearch 分页收集 PMID（单批最多 10000）→ efetch 分批拉 MEDLINE 文本 → 解析为统一 JSON。某一批失败会跳过并在 stderr 提示，不中断整体（与 SciSearch 同款容错）。

### 2.2 fetch — 按 PMID 重新拉取

```bash
python scripts/pubmed.py fetch --ids 35228754,34711976 --out selected.json
```

用于：筛选后补齐/刷新元数据；把散列的 PMID 清单变成完整 JSON。

### 2.3 mesh — MeSH 主题词校验

```bash
python scripts/pubmed.py mesh --terms-file terms.txt --out mesh_check.json
# terms.txt 每行一个英文术语；也可直接跟位置参数（注意多词术语要加引号）
```

输出每个术语的判定：

| status | 含义 | 检索式写法 |
|---|---|---|
| `exact` | 是 MeSH 主题词（含 descriptor_id 如 D006333） | `"Heart Failure"[MeSH Terms]` |
| `suggest` | 非主题词，附 ≤5 个官方相近词 | 从 suggestions 里挑 |
| `not_found` | 不是 descriptor（可能是补充概念词或自由词） | `"X"[Supplementary Concept]` 或 `"X"[Title/Abstract]` |

### 2.4 输出 JSON Schema

```jsonc
{
  "source": "pubmed",
  "query": "...",                 // 实际执行的检索式
  "mindate": "", "maxdate": "",
  "search_date": "2026-08-15T10:56:48+0800",
  "count": 5,                     // 计划拉取数（受 retmax 限制后的命中数）
  "fetched": 5,                   // 实际成功解析数
  "papers": [
    {
      "pmid": "35228754",
      "title": "...",
      "authors": ["Voors, Adriaan A", ...],      // FAU 全称
      "authors_abbr": ["Voors AA", ...],          // AU 缩写（Vancouver 引用用）
      "date": "2022-03",                          // DP 归一化：YYYY[-MM[-DD]]
      "journal": "Nature medicine",               // JT 全称
      "journal_abbr": "Nat Med",                  // TA 缩写
      "abstract": "...",
      "mesh_terms": ["*Heart Failure", ...],      // MH；* = 主要主题词
      "keywords": ["..."],                        // OT 作者关键词
      "volume": "28", "issue": "3", "pages": "568-574",
      "pub_types": ["Randomized Controlled Trial", ...],
      "language": "eng",
      "doi": "10.1038/...", "pmc": "PMC8938265", "arxiv": "", "pii": "...",
      "url": "https://pubmed.ncbi.nlm.nih.gov/35228754/",
      "_medline": "PMID- 35228754\n..."           // 原始 MEDLINE 记录（无损导出用）
    }
  ]
}
```

---

## 3. export.py：13 种格式导出

```bash
python scripts/export.py --input results.json --format <格式> --out <文件> \
    [--fields ...] [--ids ...] [--exclude ...] [--url-source auto]
```

`--input` 接受：`results.json` / `screening.json` / `selected.json`（含 `papers` 数组的任何 JSON，或直接是论文数组）。

### 3.1 格式一览

| 类别 | `--format` | 建议扩展名 | 说明 |
|---|---|---|---|
| 数据交换 | `ris` | .ris | EndNote / Zotero / NoteExpress 可导入 |
| | `bibtex` | .bib | LaTeX 文献管理 |
| | `enxml` | .xml | EndNote XML（含 style 包装） |
| | `medline` | .txt | PubMed 格式；有原始记录则原样输出，否则按字段重建 |
| 引用格式 | `apa` | .txt | APA 7th（>20 作者：前 19 + … + 末位） |
| | `harvard` | .txt | Harvard（>3 作者 et al.） |
| | `mla` | .txt | MLA 9 |
| | `chicago` | .txt | Chicago 17 author-date |
| | `ieee` | .txt | IEEE（[n] 编号，>6 作者 et al.） |
| | `vancouver` | .txt | Vancouver/ICMJE（n. 编号，>6 作者 et al.） |
| | `gbt7714` | .txt | GB/T 7714-2015（[n] 编号，[J] 文献标识，>3 作者 et al） |
| 电子表格 | `csv` | .csv | UTF-8 BOM（Excel 直接打开不乱码） |
| | `xlsx` | .xlsx | 需 openpyxl；ID/网址列自动超链接 |

### 3.2 电子表格字段（`--fields`）

可选值：`title, authors, journal, date, abstract, keywords, mesh, pub_types, volume, issue, pages, pmid, doi, pmc, arxiv, url`
默认：`title,authors,journal,date,doi,url`

- 表头为中文（题目/作者/期刊/发表日期/关键词/摘要/…）。
- XLSX 中 `pmid / doi / pmc / arxiv` 列自动链接到 PubMed / doi.org / PMC / arXiv；
  `url` 列的取值优先级由 `--url-source` 控制（默认 `auto` = doi > pmc > pmid > arxiv）。
- 页码自动展开 MEDLINE 缩写式（`568-74` → `568-574`）。

### 3.3 子集选择

```bash
# 只导出指定 PMID
python scripts/export.py --input results.json --format ris --ids 35228754,36592186 --out sub.ris
# 排除指定 PMID
python scripts/export.py --input results.json --format csv --exclude 36216487 --out rest.csv
```

---

## 4. download.py：开放获取原文下载

```bash
python scripts/download.py --input selected.json [--ids ...] --outdir papers/ \
    [--email you@example.com] [--api-key KEY]
```

策略（**仅合法渠道**）：

1. 论文 JSON 里有 `pmc` → 调 PMC OA 服务（`oa.fcgi`）取 PDF/tgz 链接下载；
2. 没有 `pmc` → 用 idconv API 尝试 PMID→PMCID，命中则走 1；
3. 仍不可得 → `manifest.json` 记录 `not_oa` + DOI/PubMed 落地页，请走机构权限获取。

产物：

```
papers/
├── 35228754_The_SGLT2_inhibitor_empagliflozin_in_pat.pdf   # tgz 内解出的 PDF（如有）
├── 35228754_The_SGLT2_inhibitor_empagliflozin_in_pat.tgz   # 原始 OA 包（含 JATS XML、图片）
└── manifest.json                                           # 每篇：downloaded / not_oa / error
```

注意：
- **NCBI 2026 年调整**：`oa_package` 已迁至 `ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/`；
  脚本内置 deprecated 路径重写，若你自行实现需注意旧链接会 404。
- OA 服务器带宽有限（实测可能 ~20 KB/s），**批量下载请在后台/批处理中运行**。

---

## 5. 完整工作流示例

课题：*SGLT2 抑制剂对心力衰竭患者心血管结局的影响（RCT）*

```bash
# ① PICOS（人工/AI 完成）→ 生成检索式，存 query.txt
cat > query.txt <<'EOF'
("Heart Failure"[MeSH Terms] OR "heart failure"[Title/Abstract])
AND ("Sodium-Glucose Transporter 2 Inhibitors"[MeSH Terms] OR "SGLT2 inhibitor*"[Title/Abstract])
AND ("Cardiovascular Diseases"[MeSH Terms] OR "cardiovascular death"[Title/Abstract] OR "hospitalization"[Title/Abstract])
AND (randomized controlled trial[Publication Type])
EOF

# ② 校验关键术语
printf 'Heart Failure\nSodium-Glucose Transporter 2 Inhibitors\nCardiovascular Diseases\n' > terms.txt
python scripts/pubmed.py mesh --terms-file terms.txt --out mesh_check.json

# ③ 检索：近 5 年，最多 200 篇
python scripts/pubmed.py search --query-file query.txt --mindate 2021 --retmax 200 --out results.json

# ④ 初筛/复筛（阅读摘要，人工或 AI 完成），把入选 PMID 汇总后刷新元数据
python scripts/pubmed.py fetch --ids 35228754,34711976,36592186 --out selected.json

# ⑤ 导出参考文献与表格
python scripts/export.py --input selected.json --format gbt7714 --out refs_gbt.txt
python scripts/export.py --input selected.json --format ris --out refs.ris
python scripts/export.py --input selected.json --format xlsx \
    --fields title,authors,journal,date,abstract,doi,url --out selected.xlsx

# ⑥ 下载 OA 原文
python scripts/download.py --input selected.json --outdir papers/
```

---

## 6. FAQ

**Q1：如何申请 NCBI API key？**
登录 [NCBI 账户](https://www.ncbi.nlm.nih.gov/account/) → Settings → API Key Management。
之后加 `--api-key` 或设环境变量 `NCBI_API_KEY`，限速从 3 req/s 升至 10 req/s。

**Q2：检索式怎么写？**
PubMed 高级检索语法：字段标签（`[MeSH Terms]`、`[Title/Abstract]`、`[All Fields]`）、
布尔运算（大写 `AND/OR/NOT`）、括号分组、截断 `*`、短语加双引号。
建议先用 `mesh` 子命令校验主题词。详细规则见 [references/mesh_strategy.md](../references/mesh_strategy.md)。

**Q3：检索式里有引号/空格，命令行报错？**
用 `--query-file`：把检索式写入 UTF-8 文本文件传入。Windows 的 PowerShell / cmd 对引号处理尤其容易踩坑。

**Q4：下载一直 404？**
NCBI 已把 OA 包迁入 `deprecated/` 目录（2026-08 起旧路径下线）。本脚本 ≥v1.0 已自动兼容；
若仍失败，看 manifest.json 里的 `message`——很多记录只提供 tgz 或不在 OA Subset。

**Q5：报错 429 / 被限速？**
脚本已内置 3 req/s 限流与指数退避重试。若你并行跑多个实例仍可能触发 NCBI 限制——串行执行或配置 API key。

**Q6：XLSX 导出报 "缺少 openpyxl"？**
`pip install openpyxl`（装到运行脚本所用的 Python 环境）。不想装可用 `csv` 格式代替。

**Q7：可以下载非 OA 的付费全文吗？**
不可以，也不会做。manifest 会给出 DOI/PubMed 落地页，请通过所在机构的数据库权限获取。

**Q8：数据可以商用吗？**
本工具代码 MIT。PubMed/PMC 数据本身的使用条款见 NCBI/NLM 官方说明；OA Subset 文献遵循其各自的 CC 许可。
