# medlit-search

医学文献检索与筛选工作流 · Medical literature search & screening workflow（当前支持 PubMed；可生成知网/万方/维普检索式供手工检索）

一套**既可独立作为命令行工具、也可作为通用 Agent 技能**使用的医学文献工作流：
从课题出发，完成 PICOS 分析 → 检索策略（PubMed + 中文平台，LLM 发散同义词并逐个说明原因）→ 纳入排除标准制定 → PubMed 检索（默认按发表日期降序）→ 多源合并去重 → 摘要初筛 → 全文复筛与证据地图 → 多格式导出的完整闭环。

检索流程移植自作者的桌面文献检索工具 **SciSearch**（C#/F#），并对其中解析与 API 语义问题做了修正。

**v1.8**：在 v1.7 跨 Agent 通用化基础上，增加每步 markdown→docx（用 Agent 自带文档能力）与课题级研究报告（md+docx，复筛后迭代优化）。不绑定任何单一 Agent 产品，可安装到 MiMo Desktop / MiMoCode、WorkBuddy、Claude Code 等支持 SKILL.md 的 Agent。

## 功能与亮点

**六步逐步中断式工作流**（协议见 `SKILL.md`；每步执行完即停止等待用户确认，中间产物可随时人工编辑）：

| 步骤 | 触发 | 产物 |
|---|---|---|
| 1 PICOS 分析 | 课题描述 | `01-PICOS分析/PICOS分析.md` |
| 2 检索策略 | "确认PICOS，继续生成检索式" | `02-检索策略/` 三件套 + 中文平台检索式 |
| 3 执行检索 | "确认检索式，开始检索" | `03-检索结果/检索结果.json` / `.csv` |
| 4 初筛 | "开始初筛"（可附任意来源文献） | `04-初筛/初筛审查表.csv` + `初筛结果.json` |
| 5 复筛（通读全文） | 追加复筛要求 | `05-复筛/复筛报告.md`（含证据地图） |
| 6 迭代更新 | 新检索式 / 新增文献 | 原步骤文件夹内 `_revN` 另存，不覆盖旧版 |

**工作流约束**：

- **一步一停**：每步产物写完立即停止，等用户确认后才推进；
- **执行隔离**：禁止读取历史记忆/项目记忆/用户偏好，参数与输出一律以当前对话和本 SKILL 为准；
- **研究参数每次重新确认**：研究类型、时间范围、检索数量只依据本次题目与对话判断；
- **复筛必须读全文**：凡能获取到全文就必须通读正文再判定，仅全文确实不可得时才可仅基于摘要并显式标注；
- **纳排标准先行**：第 2 步生成 `纳入排除标准.md`，第 4/5 步以此为依据；
- **同义词发散留痕**：每个发散词注明类别与加入原因；
- **默认最新在前**：`--sort pub_date` + 客户端降序双保险。

**脚本能力**：`pubmed.py` 检索/MeSH 校验 · `import.py` 多格式导入 · `merge.py` 多源去重 · `screen.py` 审查表 · `fulltext.py` 全文准备 · `export.py` 13 种导出 · `download.py` PMC OA 下载。

## 用户使用流程

<div>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 648" width="1080" height="648" style="width:100%;max-width:1080px;height:auto;display:block;margin:0 auto;font-family:'Segoe UI','Microsoft YaHei','PingFang SC','Hiragino Sans GB','Noto Sans CJK SC',sans-serif;">
<defs>
<marker id="flowArrowA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 Z" fill="#8b97a8"/></marker>
</defs>
<rect x="24" y="16" width="306" height="616" rx="14" fill="#fbfcfe" stroke="#e6ebf2"/>
<rect x="386" y="16" width="306" height="616" rx="14" fill="#fbfcfe" stroke="#e6ebf2"/>
<rect x="748" y="16" width="306" height="616" rx="14" fill="#fbfcfe" stroke="#e6ebf2"/>
<g font-size="12" font-weight="600" fill="#3d5a80" text-anchor="middle">
<rect x="38" y="30" width="164" height="24" rx="6" fill="#eef3fa" stroke="#d9e3f0"/>
<text x="120" y="46">第 1–2 步 · 分析与策略</text>
<rect x="400" y="30" width="164" height="24" rx="6" fill="#eef3fa" stroke="#d9e3f0"/>
<text x="482" y="46">第 3–4 步 · 检索与初筛</text>
<rect x="762" y="30" width="164" height="24" rx="6" fill="#eef3fa" stroke="#d9e3f0"/>
<text x="844" y="46">第 5–6 步 · 复筛与导出</text>
</g>
<g stroke="#8b97a8" stroke-width="1.5" fill="none" marker-end="url(#flowArrowA)" stroke-linejoin="round">
<line x1="177" y1="100" x2="177" y2="128"/>
<line x1="177" y1="166" x2="177" y2="192"/>
<line x1="177" y1="254" x2="177" y2="280"/>
<line x1="177" y1="314" x2="177" y2="370"/>
<line x1="177" y1="406" x2="177" y2="432"/>
<line x1="177" y1="512" x2="177" y2="538"/>
<polyline points="177,572 177,596 358,596 358,85 400,85"/>
<line x1="539" y1="102" x2="539" y2="128"/>
<line x1="539" y1="190" x2="539" y2="216"/>
<line x1="539" y1="250" x2="539" y2="308"/>
<line x1="539" y1="344" x2="539" y2="370"/>
<line x1="539" y1="450" x2="539" y2="476"/>
<polyline points="539,510 539,596 720,596 720,85 762,85"/>
<line x1="901" y1="102" x2="901" y2="122"/>
<line x1="901" y1="202" x2="901" y2="222"/>
<line x1="901" y1="256" x2="901" y2="308"/>
<line x1="901" y1="344" x2="901" y2="364"/>
<line x1="901" y1="426" x2="901" y2="446"/>
<line x1="901" y1="480" x2="901" y2="516"/>
<line x1="901" y1="558" x2="901" y2="578"/>
</g>
<rect x="38" y="68" width="278" height="32" rx="16" fill="#e9f7ee" stroke="#3d9a5f" stroke-width="1.2"/>
<text x="177" y="89" text-anchor="middle" font-size="13" fill="#1b5e20">用户描述课题</text>
<rect x="38" y="132" width="278" height="34" rx="8" fill="#e9f1fd" stroke="#2f6fd0" stroke-width="1.2"/>
<circle cx="62" cy="149" r="11" fill="#2f6fd0"/>
<text x="62" y="153" text-anchor="middle" font-size="12" font-weight="700" fill="#ffffff">1</text>
<text x="82" y="154" font-size="14" font-weight="700" fill="#0d47a1">第 1 步 · PICOS 分析</text>
<rect x="38" y="194" width="278" height="60" rx="8" fill="#ffffff" stroke="#d8e0ea" stroke-width="1.1"/>
<g font-size="12.5" fill="#3f4b5c">
<text x="54" y="220">写 01-PICOS分析/PICOS分析.md</text>
<text x="54" y="238">强制确认：研究类型 / 时间范围 / 数量上限</text>
</g>
<rect x="38" y="282" width="278" height="32" rx="8" fill="#fff5e8" stroke="#e07b39" stroke-width="1.2"/>
<circle cx="62" cy="298" r="9" fill="#e07b39"/>
<text x="62" y="302" text-anchor="middle" font-size="11" font-weight="700" fill="#ffffff">!</text>
<text x="80" y="303" font-size="12.5" fill="#8a4b16">停止 · 等待用户确认 PICOS</text>
<rect x="38" y="372" width="278" height="34" rx="8" fill="#e9f1fd" stroke="#2f6fd0" stroke-width="1.2"/>
<circle cx="62" cy="389" r="11" fill="#2f6fd0"/>
<text x="62" y="393" text-anchor="middle" font-size="12" font-weight="700" fill="#ffffff">2</text>
<text x="82" y="394" font-size="14" font-weight="700" fill="#0d47a1">第 2 步 · 检索式 + 纳排标准</text>
<rect x="38" y="434" width="278" height="78" rx="8" fill="#ffffff" stroke="#d8e0ea" stroke-width="1.1"/>
<g font-size="12.5" fill="#3f4b5c">
<text x="54" y="460">MeSH 校验 + 同义词发散</text>
<text x="54" y="478">PubMed / 知网 / 万方 / 维普检索式</text>
<text x="54" y="496">纳入排除标准.md</text>
</g>
<rect x="38" y="540" width="278" height="32" rx="8" fill="#fff5e8" stroke="#e07b39" stroke-width="1.2"/>
<circle cx="62" cy="556" r="9" fill="#e07b39"/>
<text x="62" y="560" text-anchor="middle" font-size="11" font-weight="700" fill="#ffffff">!</text>
<text x="80" y="561" font-size="12.5" fill="#8a4b16">停止 · 等待用户确认检索式</text>
<rect x="400" y="68" width="278" height="34" rx="8" fill="#e9f1fd" stroke="#2f6fd0" stroke-width="1.2"/>
<circle cx="424" cy="85" r="11" fill="#2f6fd0"/>
<text x="424" y="89" text-anchor="middle" font-size="12" font-weight="700" fill="#ffffff">3</text>
<text x="444" y="90" font-size="14" font-weight="700" fill="#0d47a1">第 3 步 · 执行 PubMed 检索</text>
<rect x="400" y="130" width="278" height="60" rx="8" fill="#ffffff" stroke="#d8e0ea" stroke-width="1.1"/>
<g font-size="12.5" fill="#3f4b5c">
<text x="416" y="156">pubmed.py search</text>
<text x="416" y="174">检索结果.json / .csv</text>
</g>
<rect x="400" y="218" width="278" height="32" rx="8" fill="#fff5e8" stroke="#e07b39" stroke-width="1.2"/>
<circle cx="424" cy="234" r="9" fill="#e07b39"/>
<text x="424" y="238" text-anchor="middle" font-size="11" font-weight="700" fill="#ffffff">!</text>
<text x="442" y="239" font-size="12.5" fill="#8a4b16">停止 · 汇报命中数与日期分布</text>
<rect x="400" y="310" width="278" height="34" rx="8" fill="#e9f1fd" stroke="#2f6fd0" stroke-width="1.2"/>
<circle cx="424" cy="327" r="11" fill="#2f6fd0"/>
<text x="424" y="331" text-anchor="middle" font-size="12" font-weight="700" fill="#ffffff">4</text>
<text x="444" y="332" font-size="14" font-weight="700" fill="#0d47a1">第 4 步 · 摘要初筛</text>
<rect x="400" y="372" width="278" height="78" rx="8" fill="#ffffff" stroke="#d8e0ea" stroke-width="1.1"/>
<g font-size="12.5" fill="#3f4b5c">
<text x="416" y="398">merge.py 合并去重</text>
<text x="416" y="416">screen.py 逐篇判定</text>
<text x="416" y="434">初筛报告.md</text>
</g>
<rect x="400" y="478" width="278" height="32" rx="8" fill="#fff5e8" stroke="#e07b39" stroke-width="1.2"/>
<circle cx="424" cy="494" r="9" fill="#e07b39"/>
<text x="424" y="498" text-anchor="middle" font-size="11" font-weight="700" fill="#ffffff">!</text>
<text x="442" y="499" font-size="12.5" fill="#8a4b16">停止 · 可改审查表后重跑 apply</text>
<rect x="762" y="68" width="278" height="34" rx="8" fill="#e9f1fd" stroke="#2f6fd0" stroke-width="1.2"/>
<circle cx="786" cy="85" r="11" fill="#2f6fd0"/>
<text x="786" y="89" text-anchor="middle" font-size="12" font-weight="700" fill="#ffffff">5</text>
<text x="806" y="90" font-size="14" font-weight="700" fill="#0d47a1">第 5 步 · 全文复筛</text>
<rect x="762" y="124" width="278" height="78" rx="8" fill="#ffffff" stroke="#d8e0ea" stroke-width="1.1"/>
<g font-size="12.5" fill="#3f4b5c">
<text x="778" y="150">fulltext.py 准备全文</text>
<text x="778" y="168">通读正文后再判定</text>
<text x="778" y="186">复筛报告.md + 证据地图</text>
</g>
<rect x="762" y="224" width="278" height="32" rx="8" fill="#fff5e8" stroke="#e07b39" stroke-width="1.2"/>
<circle cx="786" cy="240" r="9" fill="#e07b39"/>
<text x="786" y="244" text-anchor="middle" font-size="11" font-weight="700" fill="#ffffff">!</text>
<text x="804" y="245" font-size="12.5" fill="#8a4b16">停止</text>
<rect x="762" y="310" width="278" height="34" rx="8" fill="#e9f1fd" stroke="#2f6fd0" stroke-width="1.2"/>
<circle cx="786" cy="327" r="11" fill="#2f6fd0"/>
<text x="786" y="331" text-anchor="middle" font-size="12" font-weight="700" fill="#ffffff">6</text>
<text x="806" y="332" font-size="14" font-weight="700" fill="#0d47a1">第 6 步 · 迭代更新</text>
<rect x="762" y="366" width="278" height="60" rx="8" fill="#ffffff" stroke="#d8e0ea" stroke-width="1.1"/>
<g font-size="12.5" fill="#3f4b5c">
<text x="778" y="392">原步骤文件夹 _revN 另存</text>
<text x="778" y="410">旧版保留不覆盖</text>
</g>
<rect x="762" y="448" width="278" height="32" rx="8" fill="#fff5e8" stroke="#e07b39" stroke-width="1.2"/>
<circle cx="786" cy="464" r="9" fill="#e07b39"/>
<text x="786" y="468" text-anchor="middle" font-size="11" font-weight="700" fill="#ffffff">!</text>
<text x="804" y="469" font-size="12.5" fill="#8a4b16">停止</text>
<rect x="762" y="518" width="278" height="40" rx="8" fill="#f5ecfb" stroke="#7b4fc0" stroke-width="1.2"/>
<text x="901" y="543" text-anchor="middle" font-size="13" fill="#4a148c">导出：RIS / BibTeX / 引用格式 / 表格</text>
<rect x="762" y="580" width="278" height="32" rx="16" fill="#e9f7ee" stroke="#3d9a5f" stroke-width="1.2"/>
<text x="901" y="601" text-anchor="middle" font-size="13" fill="#1b5e20">结束 或 继续迭代</text>
<g font-size="10.5" fill="#64748b">
<text x="185" y="338">确认 PICOS</text>
<text x="185" y="353">继续生成检索式</text>
<text x="585" y="275">开始初筛</text>
<text x="585" y="290">可附 CNKI 题录</text>
<text x="921" y="287">新检索式 / 新文献</text>
</g>
<rect x="210" y="587" width="113" height="16" rx="3" fill="#fbfcfe"/>
<text x="266" y="599" text-anchor="middle" font-size="10.5" fill="#64748b">确认检索式，开始检索</text>
<rect x="597" y="587" width="66" height="16" rx="3" fill="#fbfcfe"/>
<text x="630" y="599" text-anchor="middle" font-size="10.5" fill="#64748b">追加复筛要求</text>
</svg>
</div>

## 内部执行流程

<div>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 514" width="1080" height="514" style="width:100%;max-width:1080px;height:auto;display:block;margin:0 auto;font-family:'Segoe UI','Microsoft YaHei','PingFang SC','Hiragino Sans GB','Noto Sans CJK SC',sans-serif;">
<defs>
<marker id="flowArrowB" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 Z" fill="#7a8899"/></marker>
</defs>
<rect x="20" y="18" width="1040" height="152" rx="12" fill="#f7fbff" stroke="#cfe0f2" stroke-width="1.2"/>
<rect x="20" y="188" width="1040" height="146" rx="12" fill="#f6fbf6" stroke="#cfe6d0" stroke-width="1.2"/>
<rect x="20" y="350" width="1040" height="144" rx="12" fill="#fffdf5" stroke="#ecdfb8" stroke-width="1.2"/>
<g font-size="13" font-weight="700">
<text x="36" y="46" fill="#1565c0">Agent / LLM 层</text>
<text x="36" y="216" fill="#2e7d32">脚本工具层 scripts/</text>
<text x="36" y="378" fill="#b8860b">外部服务</text>
</g>
<g stroke="#7a8899" stroke-width="1.6" fill="none" marker-end="url(#flowArrowB)" stroke-linejoin="round">
<polyline points="222,101 253,101"/>
<polyline points="300,130 300,222 137,222 137,240"/>
<polyline points="540,240 540,130"/>
<polyline points="741,130 741,240"/>
<polyline points="826,101 942,101 942,240"/>
<polyline points="222,269 253,269"/>
<polyline points="423,269 455,269"/>
<polyline points="137,300 137,398"/>
<polyline points="190,300 190,366 338,366 338,398"/>
<polyline points="705,300 705,398"/>
<polyline points="800,300 800,366 885,366 885,398"/>
</g>
<rect x="52" y="72" width="170" height="58" rx="9" fill="#e3f2fd" stroke="#1565c0" stroke-width="1.4"/>
<text x="137" y="95" text-anchor="middle" font-size="13" font-weight="700" fill="#0d47a1">PICOS 拆解</text>
<text x="137" y="113" text-anchor="middle" font-size="11.5" fill="#37536f">参数确认</text>
<rect x="253" y="72" width="170" height="58" rx="9" fill="#e3f2fd" stroke="#1565c0" stroke-width="1.4"/>
<text x="338" y="95" text-anchor="middle" font-size="13" font-weight="700" fill="#0d47a1">同义词发散</text>
<text x="338" y="113" text-anchor="middle" font-size="11.5" fill="#37536f">纳排标准</text>
<rect x="455" y="72" width="371" height="58" rx="9" fill="#e3f2fd" stroke="#1565c0" stroke-width="1.4"/>
<text x="640" y="95" text-anchor="middle" font-size="13" font-weight="700" fill="#0d47a1">初筛 / 复筛判定</text>
<text x="640" y="113" text-anchor="middle" font-size="11.5" fill="#37536f">证据地图</text>
<rect x="52" y="238" width="170" height="62" rx="9" fill="#e8f5e9" stroke="#2e7d32" stroke-width="1.4"/>
<text x="137" y="256" text-anchor="middle" font-size="12.5" font-weight="700" fill="#1b5e20">pubmed.py</text>
<text x="137" y="272" text-anchor="middle" font-size="11" fill="#2f5c36">esearch → efetch</text>
<text x="137" y="288" text-anchor="middle" font-size="11" fill="#2f5c36">aiohttp 异步</text>
<rect x="253" y="238" width="170" height="62" rx="9" fill="#e8f5e9" stroke="#2e7d32" stroke-width="1.4"/>
<text x="338" y="256" text-anchor="middle" font-size="12.5" font-weight="700" fill="#1b5e20">import.py / merge.py</text>
<text x="338" y="272" text-anchor="middle" font-size="11" fill="#2f5c36">多源导入 + 去重</text>
<text x="338" y="288" text-anchor="middle" font-size="11" fill="#2f5c36">DOI / PMID / 标题</text>
<rect x="455" y="238" width="170" height="62" rx="9" fill="#e8f5e9" stroke="#2e7d32" stroke-width="1.4"/>
<text x="540" y="262" text-anchor="middle" font-size="12.5" font-weight="700" fill="#1b5e20">screen.py</text>
<text x="540" y="280" text-anchor="middle" font-size="11" fill="#2f5c36">prepare / apply 审查表</text>
<rect x="656" y="238" width="170" height="62" rx="9" fill="#e8f5e9" stroke="#2e7d32" stroke-width="1.4"/>
<text x="741" y="262" text-anchor="middle" font-size="12.5" font-weight="700" fill="#1b5e20">fulltext.py / download.py</text>
<text x="741" y="280" text-anchor="middle" font-size="11" fill="#2f5c36">Unpaywall + PMC OA</text>
<rect x="857" y="238" width="170" height="62" rx="9" fill="#e8f5e9" stroke="#2e7d32" stroke-width="1.4"/>
<text x="942" y="262" text-anchor="middle" font-size="12.5" font-weight="700" fill="#1b5e20">export.py</text>
<text x="942" y="280" text-anchor="middle" font-size="11" fill="#2f5c36">RIS / BibTeX / 引用 / 表格</text>
<rect x="52" y="398" width="170" height="58" rx="9" fill="#fff8e1" stroke="#f9a825" stroke-width="1.4"/>
<text x="137" y="421" text-anchor="middle" font-size="12.5" font-weight="700" fill="#8a6a00">NCBI E-utilities</text>
<text x="137" y="439" text-anchor="middle" font-size="11" fill="#6b5a2a">esearch / efetch</text>
<rect x="253" y="398" width="170" height="58" rx="9" fill="#fff8e1" stroke="#f9a825" stroke-width="1.4"/>
<text x="338" y="421" text-anchor="middle" font-size="12.5" font-weight="700" fill="#8a6a00">NLM MeSH RDF</text>
<text x="338" y="439" text-anchor="middle" font-size="11" fill="#6b5a2a">主题词校验</text>
<rect x="620" y="398" width="170" height="58" rx="9" fill="#fff8e1" stroke="#f9a825" stroke-width="1.4"/>
<text x="705" y="421" text-anchor="middle" font-size="12.5" font-weight="700" fill="#8a6a00">Unpaywall API</text>
<text x="705" y="439" text-anchor="middle" font-size="11" fill="#6b5a2a">OA 链接解析</text>
<rect x="800" y="398" width="170" height="58" rx="9" fill="#fff8e1" stroke="#f9a825" stroke-width="1.4"/>
<text x="885" y="421" text-anchor="middle" font-size="12.5" font-weight="700" fill="#8a6a00">PMC OA / idconv</text>
<text x="885" y="439" text-anchor="middle" font-size="11" fill="#6b5a2a">PMC 全文获取</text>
</svg>
</div>

**数据流**：`检索结果.json` →（merge）→ `文献池.json` →（screen prepare/apply）→ `初筛结果.json` →（fulltext）→ `全文清单.csv` + `全文/` →（screen round 2）→ `复筛结果.json` →（export）→ 各格式产物。各脚本无状态，读写统一 Paper JSON，任一步可中断续跑。

## 安装

**从 `v1.8` 稳定标签安装**（`main` 为开发分支）：

```bash
# 通用：克隆到你的 Agent 技能目录
git clone -b v1.8 https://github.com/SloneWang/medlit-search.git <skills目录>/medlit-search

# 常见 Agent 路径示例
# MiMo Desktop / MiMoCode:  ~/.config/mimocode/skills/medlit-search
# WorkBuddy:                ~/.workbuddy/skills/medlit-search
```

安装依赖（Agent 识别 `requirements.txt` 后安装）：

```bash
pip install -r requirements.txt   # aiohttp + openpyxl
```

## 快速开始（命令行）

```bash
# 1. 检索（检索式写入文件可避免 shell 引号问题）
echo '("Heart Failure"[MeSH Terms]) AND "SGLT2 inhibitor*"[Title/Abstract]' > "02-检索策略/PubMed检索式.txt"
python scripts/pubmed.py search --query-file "02-检索策略/PubMed检索式.txt" \
    --mindate 2023 --maxdate 2026/09/17 --limit 100 --sort pub_date \
    --out "03-检索结果/检索结果.json"

# 2. 校验 MeSH 主题词
python scripts/pubmed.py mesh --terms-file terms.txt --out mesh_check.json

# 3. 多来源汇总 + 去重
python scripts/merge.py --input "03-检索结果/检索结果.json" cnki导出.xlsx 其它题录/ \
    --out "04-初筛/文献池.json" --csv "04-初筛/文献池.csv" --dedupe-log "04-初筛/去重日志.log"

# 4. 审查表 → 填决策 → 回填
python scripts/screen.py prepare --input "04-初筛/文献池.json" --out "04-初筛/初筛审查表.csv" --round 1
python scripts/screen.py apply   --input "04-初筛/文献池.json" --review "04-初筛/初筛审查表.csv" \
    --out "04-初筛/初筛结果.json" --round 1

# 5. 复筛全文准备（必须真实邮箱）
python scripts/fulltext.py --input "04-初筛/初筛结果.json" --dir "05-复筛" \
    --provide 我的全文/ --email you@example.org --out "05-复筛/全文清单.csv"

# 6. 导出
python scripts/export.py --input "04-初筛/初筛结果.json" --format ris     --out refs.ris
python scripts/export.py --input "04-初筛/初筛结果.json" --format gbt7714 --out refs.txt
```

## 目录结构

```
medlit-search/
├── SKILL.md                  # Agent 技能定义（六步逐步中断流程，v1.8）
├── README.md                 # 本文件（含流程图）
├── requirements.txt          # 依赖：aiohttp、openpyxl
├── scripts/
│   ├── pubmed.py             # PubMed 检索 / 拉取 / MeSH 校验（aiohttp 异步）
│   ├── import.py             # RIS/BibTeX/MEDLINE/CSV/XLSX/JSON 导入
│   ├── merge.py              # 多来源汇总 + 去重
│   ├── screen.py             # 关键词预筛 + prepare/apply 审查表
│   ├── fulltext.py           # 全文准备：用户索引 + Unpaywall OA（aiohttp 异步）
│   ├── export.py             # 13 种格式导出
│   ├── download.py           # PMC OA 原文下载（aiohttp 异步）
│   └── workflow.py           # 一键导出 all/screening/selected CSV
├── references/
│   ├── medline_fields.md     # MEDLINE 字段速查
│   ├── mesh_strategy.md      # MeSH 检索策略构造规则
│   ├── screening.md          # 初筛/复筛模板（PRISMA）
│   └── inclusion_exclusion_template.md
├── docs/
│   ├── USAGE.md              # 使用文档
│   └── DEVELOPMENT.md        # 开发文档
└── LICENSE                   # MIT
```

## 技术栈

- Python ≥ 3.10
- **`aiohttp`**：全部 HTTP 调用（PubMed E-utilities、MeSH lookup、Unpaywall、PMC OA）
- **`openpyxl`**：XLSX 导出
- 其余功能仅用 Python 标准库
- 全部脚本含类型注解与中文 Google 风格 docstring
- IO 密集路径为 `asyncio` 协程：`AsyncRateLimiter` 控请求起步节奏（无 key 3 req/s、有 key 10 req/s）、`Semaphore` 限在途并发

## 版本与标签

| 版本 | 说明 |
|---|---|
| `main` | 开发分支 |
| `v1.8` | **当前开发版**：每步 md→docx（Agent 自带能力）、研究报告 md+docx 与复筛迭代优化 |
| `v1.7` | 跨 Agent 通用化、aiohttp 异步 HTTP、复筛强制读全文、提示词精简、版本统一；已锁定 |
| `v1.6` | 强制参数确认、中文分步文件夹、`_revN` 修订、IO 协程化；已锁定 |
| `v1.5` | 逐步中断六步工作流 + 多源合并去重；已锁定 |
| `v1.4` | 全量类型注解；已锁定 |

## 更新日志

### v1.8（2026-10-20）

- **每步 docx**：各步主 markdown 完成后，用 **Agent 自带文档技能**另存同名 `.docx`；本 Skill 不实现/不依赖 docx 转换库；
- **研究报告**：第 5 步完成或流程走完后生成 `研究报告/研究报告.md` + `.docx`（题目背景、PICO、方法、结果表、讨论、结论、局限、参考文献）；**每次复筛或完整重跑后优化该报告**并更新版本说明；
- 版本号全库对齐 v1.8。

### v1.7（2026-10-20）

- **通用化**：去除 WorkBuddy 专属路径与工具标识，安装说明覆盖 MiMo Desktop / WorkBuddy / Claude Code 等；`tool=medlit_search`、UA `medlit-search/1.7`；
- **版本统一**：SKILL / README / docs / 脚本注释对齐，脚本增加 `__version__`；
- **提示词精简**：六步工作流压缩冗余；新增「执行隔离」；停止协议强化；复筛强制读全文；
- **代码异步化**：HTTP 全部改为 `aiohttp` 协程；依赖显式声明于 `requirements.txt` 与 SKILL.md；
- **文档**：README 增加用户使用流程与内部执行流程图。

### v1.6（2026-09-17）

强制参数确认、中文分步文件夹、默认日期降序、纳排标准、`_revN` 修订、固定页大小 20 + 超长检索式自动 POST、IO 协程化。

### v1.5（2026-09-11）

逐步中断式六步工作流；新增 `merge.py` / `fulltext.py` / `screen.py` prepare/apply。

### v1.4 及更早

v1.4 全量类型注解；v1.3 参数默认值优化；v1.2 API 语义修正 + usehistory；v1.1 摘要默认导出；v1.0 初始发布（2026-08-15）。

## 合规声明

- 遵循 [NCBI E-utilities 使用政策](https://www.ncbi.nlm.nih.gov/books/NBK25497/)：未配 API key ≤3 req/s，配置后 ≤10 req/s，脚本已内置限流。
- 原文下载仅使用合法开放获取渠道（PMC OA / Unpaywall）；非 OA 文献只保存 DOI/PubMed 落地页链接，不绕付费墙。
- 未取得全文的文献，筛选结论须标注"仅基于摘要"。
- 本工具输出仅供科研参考，不构成医学建议。

## License

[MIT](LICENSE) © 2026 王天乐 (Wang Tianle / SloneWang)
