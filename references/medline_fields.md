# MEDLINE 字段速查（PubMed efetch rettype=medline）

格式：每行 `KEY - value`（KEY 占前 4 列，第 5 列为 `-`），续行从第 7 列开始；
空行分隔记录。本 skill 的 JSON 字段映射如下。

| MEDLINE | 含义 | JSON 字段 | 备注 |
|---|---|---|---|
| PMID | PubMed 编号 | `pmid` | |
| TI | 题目 | `title` | |
| FAU | 作者全称 "Last, First M" | `authors` | 每作者一行 |
| AU | 作者缩写 "Last FM" | `authors_abbr` | 引用格式备用 |
| DP | **发表日期** | `date` | 已归一化为 YYYY[-MM[-DD]]。**注意：SciSearch 旧代码误用 MHDA（MeSH 标引完成日期），本 skill 已修正为 DP** |
| JT | 期刊全称 | `journal` | |
| TA | 期刊缩写 | `journal_abbr` | Vancouver/IEEE 引用用 |
| AB | 摘要 | `abstract` | 初筛依据 |
| MH | MeSH 主题词（含限定词，`*` 表主要主题词） | `mesh_terms` | 导出时去 `*` |
| OT | 作者关键词 | `keywords` | |
| VI / IP / PG | 卷 / 期 / 页码 | `volume` / `issue` / `pages` | 页码可能缩写如 `568-74`，导出时已展开 |
| PT | 文献类型 | `pub_types` | 如 Randomized Controlled Trial、Review、Meta-Analysis |
| LA | 语言 | `language` | |
| AID / LID | 文章标识，带 `[doi]`/`[pii]`/`[pmc]`/`[arxiv]` 标签 | `doi` / `pii` / `pmc` / `arxiv` | DOI 提取自 `[doi]` 标签（SciSearch 同款正则的通用化） |
| PMC | PMC 编号 | `pmc` | OA 下载凭证 |

其他常见但未入 JSON 的字段：OWN/STAT/DCOM/LR（NLM 内部状态）、IS（ISSN）、
AD（作者单位）、AUID（ORCID）、CI（版权）、RN（物质登记号）、SB（子集）、
SO（来源拼串）、MHDA/CRDT（NLM 处理日期）、EDAT（入 PubMed 日期）。

原始记录全文存于每篇的 `_medline` 字段，PubMed 格式导出时原样输出（无损）。

## 解析要点（移植自 SciSearch Medline.fs 并修复）

1. 空行或长度 <6 的行 → 记录分隔。
2. `line[4] == '-'` → 新属性；否则为上一属性续行（值用空格拼接）。
3. **修复**：F# 原版用 `List.pairwise` 圈定属性范围，会丢失每条记录的
   最后一个属性（通常是 SO 或最后一个 OT）；Python 版已正确处理末尾属性。
