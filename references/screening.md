# 初筛 / 复筛模板（基于摘要的 PICOS 对照）

## 初筛判定规则（每篇必给一句话理由）

- `include`：P、I、O 均明确符合；C、S 符合或未限定但可接受。
- `exclude`：任一要素明确不符。常见排除理由：
  人群不符 / 干预不符 / 结局不符 / 研究类型不符（如要 RCT 却是综述）/
  动物或体外实验 / 非目标语言 / 无摘要且题录明显不相关 / 重复。
- `uncertain`：摘要信息不足（如只有方案、无明确结局指标），需全文判定。

## 程序化预筛（screen.py）

在人工/AI 精读前，可先用 `scripts/screen.py` 批量过滤明显符合/排除的文献：

```bash
python scripts/screen.py --input results.json \
    --include "empagliflozin,cardiovascular death,randomized" \
    --exclude "mice,rat,animal,in vitro" \
    --field title+abstract --mode and --out screening.json
```

- `--field title+abstract`：匹配范围同时覆盖标题与摘要（也可只选 `title` 或 `abstract`）。
- `--mode and`：多个纳入词必须**全部命中**才 include；`or` 则任一中即可。
- `--exclude` 优先级最高：任一命中即 exclude。
- 未命中纳入也未命中排除的论文默认标记 `uncertain`（可用 `--uncertain-as exclude` 改为排除）。
- 预筛结果仍需人工/AI 按 PICOS 复核；`screening.json` 的 `decision.reason` 会记录命中词。

## 输出格式（每批 10-20 篇）

| PMID | 题目 | 判定 | 理由 |
|---|---|---|---|
| 35228754 | The SGLT2 inhibitor empagliflozin... | include | 急性心衰住院患者，RCT，心血管结局 |

## screening.json 结构

在 results.json 每篇 paper 上追加：

```json
"decision": {
  "round1": "include|exclude|uncertain",
  "reason": "一句话理由",
  "round2": "include|exclude",       // 复筛后填
  "reason2": "复筛理由"
}
```

文件其余部分与 results.json 相同，可直接喂给 export.py / download.py
（配合 --ids 或先筛出子集）。

## 汇总（PRISMA 风格，写进 screening.md 给用户过目）

- 检索命中：n（results.json count）
- 去重后：n
- 初筛：排除 n（理由分布：人群 x / 干预 x / 类型 x ...），待定 n，纳入 n
- 复筛：从待定中纳入 n、排除 n
- 最终纳入：n（selected.json）

## 复筛常见追加标准（用户可能提出）

- 只要 RCT / 系统综述 / Meta 分析 → 看 `pub_types`
- 时间窗 → 看 `date`
- 排除特定人群/合并症 → 重读摘要
- 只要英文/只要中文 → 看 `language`
- 只要可获取全文的 → 看 `pmc` 是否非空（PMC 收录≠必然 OA 可下，以 download manifest 为准）
