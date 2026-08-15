# MeSH 检索策略构造规则

## 校验流程

对每个候选概念词执行：

```
python scripts/pubmed.py mesh --terms-file terms.txt --out mesh_check.json
```

- `status=exact`：是 MeSH 主题词（descriptor），检索式中用 `"<官方label>"[MeSH Terms]`。
- `status=suggest`：不是主题词但有相近词——把 suggestions 列给用户选。
- `status=not_found`：不是 descriptor。两种情况：
  - 新药/新器械（如 Empagliflozin）多为 **MeSH 补充概念词**（Supplementary Concept），
    用 `"Empagliflozin"[Supplementary Concept]`，并同时 OR 自由词；
  - 普通自由词：用 `"xxx"[Title/Abstract]`。
  - 拿不准时可再查 https://meshb.nlm.nih.gov/search 人工确认。

## 检索式语法要点

- 字段标签：`[MeSH Terms]`（自动下位词扩展）、`[MeSH:NoExp]`（不扩展）、
  `[Title/Abstract]`、`[All Fields]`、`[Supplementary Concept]`、`[Author]`。
- 布尔：`AND` `OR` `NOT` 必须大写；用括号分组。
- 截断：`therap*`（最多 600 个扩展词；截断词不能用字段标签混写 MeSH）。
- 短语：加双引号，如 `"heart failure"`。
- 每个概念组内：`MeSH 词 OR 自由词(含常见拼写变体/缩写)`，组间 `AND`。
- S（研究类型）可用过滤器：如 `randomized controlled trial[Publication Type]`、
  `meta-analysis[Publication Type]`、`systematic[sb]`。
- 日期过滤不要写进检索式，用脚本参数 `--mindate/--maxdate`（datetype=pdat）。

## 双语策略说明模板

对每个概念块给用户这样的对照（示例）：

| 概念块 | 中文意图 | MeSH 词 | 自由词 |
|---|---|---|---|
| P | 心力衰竭患者 | "Heart Failure"[MeSH Terms] | "heart failure"[Title/Abstract], HFpEF[Title/Abstract] ... |
| I | SGLT2 抑制剂 | "Sodium-Glucose Transporter 2 Inhibitors"[MeSH Terms] | "SGLT2 inhibitor*"[Title/Abstract], empagliflozin[Title/Abstract] ... |
| O | 心血管结局/住院率 | "Hospitalization"[MeSH Terms] ... | "cardiovascular death"[Title/Abstract] ... |

提醒用户：
- MeSH 词表每年更新（新版一般在年初生效）；校验结果以 NLM 当前词表为准。
- 有 MeSH 词的概念尽量用 MeSH（查全），自由词补充最新未标引文献（查新）。
- 全自由词策略查准率高但会漏；只追最新文献时可考虑。
