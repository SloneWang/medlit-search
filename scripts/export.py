#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export.py — 文献导出器

输入：pubmed.py 产出的 JSON（{"papers": [...]}），或纯论文数组，或筛选 JSON。
支持格式：
  数据交换 : ris | bibtex | enxml(EndNote XML) | medline(PubMed 格式)
  引用格式 : apa | harvard | mla | chicago | ieee | vancouver | gbt7714
  电子表格 : csv | xlsx（字段可选，ID 列自动挂超链接）

字段（csv/xlsx --fields 可选值）：
  title, authors, journal, date, abstract, keywords, mesh, pub_types,
  volume, issue, pages, pmid, doi, pmc, arxiv, url
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from xml.sax.saxutils import escape as xml_escape

# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

FIELDS = [
    "title", "authors", "journal", "date", "abstract", "keywords", "mesh",
    "pub_types", "volume", "issue", "pages", "pmid", "doi", "pmc", "arxiv", "url",
]

FIELD_LABELS = {
    "title": "题目", "authors": "作者", "journal": "期刊", "date": "发表日期",
    "abstract": "摘要", "keywords": "关键词", "mesh": "MeSH主题词",
    "pub_types": "文献类型", "volume": "卷", "issue": "期", "pages": "页码",
    "pmid": "PMID", "doi": "DOI", "pmc": "PMC编号", "arxiv": "arXiv编号", "url": "网址",
}

ID_URL = {
    "pmid": lambda v: f"https://pubmed.ncbi.nlm.nih.gov/{v}/",
    "doi": lambda v: f"https://doi.org/{v}",
    "pmc": lambda v: f"https://pmc.ncbi.nlm.nih.gov/articles/{v}/",
    "arxiv": lambda v: f"https://arxiv.org/abs/{v}",
}


def load_papers(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    for key in ("papers", "included", "results"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    raise ValueError("输入 JSON 中找不到论文数组（papers/included/results）")


def filter_ids(papers: list[dict], ids: str | None, exclude: str | None) -> list[dict]:
    if ids:
        keep = {x.strip() for x in ids.split(",") if x.strip()}
        papers = [p for p in papers if str(p.get("pmid", "")) in keep]
    if exclude:
        drop = {x.strip() for x in exclude.split(",") if x.strip()}
        papers = [p for p in papers if str(p.get("pmid", "")) not in drop]
    return papers


def expand_pages(pages: str) -> tuple[str, str]:
    """MEDLINE 缩写页码展开：'568-74' -> ('568','574')；无法解析返回原样。"""
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", pages or "")
    if not m:
        return pages or "", ""
    start, end = m.group(1), m.group(2)
    if len(end) < len(start):
        end = start[: len(start) - len(end)] + end
    return start, end


def page_range(pages: str, dash: str = "-") -> str:
    sp, ep = expand_pages(pages)
    return f"{sp}{dash}{ep}" if ep else sp


def year_of(p: dict) -> str:
    return (p.get("date") or "")[:4]


def parse_author(fau: str) -> tuple[str, str]:
    """'Voors, Adriaan A' -> ('Voors', 'Adriaan A')；无逗号按 'Last FM' 缩写式处理。"""
    fau = (fau or "").strip()
    if "," in fau:
        last, given = fau.split(",", 1)
        return last.strip(), given.strip()
    parts = fau.split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]{1,4}", parts[-1]):
        return " ".join(parts[:-1]), parts[-1]
    return fau, ""


def initials_of(given: str, dot: bool = True, space: bool = False) -> str:
    """'Adriaan A' -> 'A. A.' / 'AA' / 'A A' 等。连字符名取两段首字母。"""
    if not given:
        return ""
    if re.fullmatch(r"[A-Z]{1,4}", given):  # 已是缩写
        letters = list(given)
    else:
        letters = []
        for tok in re.split(r"\s+", given):
            if not tok:
                continue
            hy = tok.split("-")
            letters.append("-".join(seg[0].upper() for seg in hy if seg))
    sep = ". " if (dot and space) else ("." if dot else "")
    out = sep.join(letters)
    return out + ("." if dot and out else "")


def author_list(p: dict) -> list[tuple[str, str]]:
    src = p.get("authors") or p.get("authors_abbr") or []
    return [parse_author(a) for a in src]


def vancouver_names(p: dict) -> list[str]:
    """'Voors AA' 风格（姓 + 无点缩写）。"""
    out = []
    for last, given in author_list(p):
        ini = initials_of(given, dot=False) if given else ""
        out.append(f"{last} {ini}".strip())
    return out


def doi_url(p: dict) -> str:
    return f"https://doi.org/{p['doi']}" if p.get("doi") else p.get("url", "")


def best_url(p: dict, pref: str = "auto") -> str:
    if pref != "auto" and p.get(pref):
        return ID_URL[pref](p[pref])
    for k in ("doi", "pmc", "pmid", "arxiv"):
        if p.get(k):
            return ID_URL[k](p[k])
    return ""


# ---------------------------------------------------------------------------
# 引用格式
# ---------------------------------------------------------------------------


def cite_apa(p: dict) -> str:
    au = author_list(p)
    names = [f"{last}, {initials_of(given, dot=True, space=True)}".rstrip(", ") for last, given in au]
    if len(names) > 20:
        astr = ", ".join(names[:19]) + ", ... " + names[-1]
    elif len(names) > 1:
        astr = ", ".join(names[:-1]) + ", & " + names[-1]
    else:
        astr = names[0] if names else ""
    bits = f"{astr} ({year_of(p)}). {p.get('title','')} {p.get('journal','')}"
    if p.get("volume"):
        bits += f", {p['volume']}"
        if p.get("issue"):
            bits += f"({p['issue']})"
    if p.get("pages"):
        bits += f", {page_range(p['pages'])}"
    bits += "."
    if p.get("doi"):
        bits += f" https://doi.org/{p['doi']}"
    return bits


def cite_harvard(p: dict) -> str:
    names = []
    for last, given in author_list(p):
        ini = initials_of(given, dot=True, space=False)
        names.append(f"{last}, {ini}".rstrip(", "))
    if len(names) > 3:
        astr = names[0] + " et al."
    elif len(names) > 1:
        astr = ", ".join(names[:-1]) + " and " + names[-1]
    else:
        astr = names[0] if names else ""
    s = f"{astr} ({year_of(p)}) '{p.get('title','').rstrip('.')}', {p.get('journal','')}"
    if p.get("volume"):
        s += f", {p['volume']}"
        if p.get("issue"):
            s += f"({p['issue']})"
    if p.get("pages"):
        s += f", pp. {page_range(p['pages'])}"
    s += "."
    if p.get("doi"):
        s += f" doi: {p['doi']}."
    return s


def cite_mla(p: dict) -> str:
    au = author_list(p)
    if not au:
        astr = ""
    elif len(au) == 1:
        astr = f"{au[0][0]}, {au[0][1]}".rstrip(", ")
    elif len(au) == 2:
        astr = f"{au[0][0]}, {au[0][1]}, and {au[1][1]} {au[1][0]}".replace("  ", " ")
    else:
        astr = f"{au[0][0]}, {au[0][1]}, et al".rstrip(", ")
    s = f'{astr}. "{p.get("title","").rstrip(".")}" {p.get("journal","")}'
    if p.get("volume"):
        s += f", vol. {p['volume']}"
    if p.get("issue"):
        s += f", no. {p['issue']}"
    if year_of(p):
        s += f", {year_of(p)}"
    if p.get("pages"):
        s += f", pp. {page_range(p['pages'])}"
    return s + "."


def cite_chicago(p: dict) -> str:
    au = author_list(p)
    if not au:
        astr = ""
    else:
        first = f"{au[0][0]}, {au[0][1]}".rstrip(", ")
        rest = [f"{g} {l}".strip() for l, g in au[1:]]
        names = [first] + rest
        if len(names) > 10:
            astr = ", ".join(names[:7]) + ", et al."
        elif len(names) > 1:
            astr = ", ".join(names[:-1]) + ", and " + names[-1]
        else:
            astr = names[0]
    s = f'{astr}. {year_of(p)}. "{p.get("title","").rstrip(".")}" {p.get("journal","")}'
    if p.get("volume"):
        s += f" {p['volume']}"
        if p.get("issue"):
            s += f" ({p['issue']})"
    if p.get("pages"):
        s += f": {page_range(p['pages'])}"
    s += "."
    if p.get("doi"):
        s += f" https://doi.org/{p['doi']}."
    return s


def cite_ieee(p: dict) -> str:
    names = []
    for last, given in author_list(p):
        ini = initials_of(given, dot=True, space=True)
        names.append(f"{ini} {last}".strip())
    if len(names) > 6:
        astr = names[0] + " et al."
    elif len(names) > 1:
        astr = ", ".join(names[:-1]) + ", and " + names[-1]
    else:
        astr = names[0] if names else ""
    s = f'{astr}, "{p.get("title","").rstrip(".")}," {p.get("journal_abbr") or p.get("journal","")}'
    if p.get("volume"):
        s += f", vol. {p['volume']}"
    if p.get("issue"):
        s += f", no. {p['issue']}"
    if p.get("pages"):
        s += f", pp. {page_range(p['pages'])}"
    if year_of(p):
        s += f", {year_of(p)}"
    s += "."
    if p.get("doi"):
        s += f" doi: {p['doi']}."
    return s


def cite_vancouver(p: dict) -> str:
    names = vancouver_names(p)
    if len(names) > 6:
        astr = ", ".join(names[:6]) + ", et al"
    else:
        astr = ", ".join(names)
    s = f"{astr.rstrip('.')}. {p.get('title','')} {p.get('journal_abbr') or p.get('journal','')}. {year_of(p)}"
    if p.get("volume"):
        s += f";{p['volume']}"
        if p.get("issue"):
            s += f"({p['issue']})"
        if p.get("pages"):
            s += f":{page_range(p['pages'])}"
    return s + "."


def cite_gbt7714(p: dict) -> str:
    names = vancouver_names(p)
    # 西文文献：姓全称 + 名缩写（GB/T 7714-2015）；超过 3 位用 et al.
    if len(names) > 3:
        astr = ", ".join(names[:3]) + ", et al"
    else:
        astr = ", ".join(names)
    s = f"{astr}. {p.get('title','').rstrip('.')}[J]. {p.get('journal','')}, {year_of(p)}"
    if p.get("volume"):
        s += f", {p['volume']}"
        if p.get("issue"):
            s += f"({p['issue']})"
        if p.get("pages"):
            s += f": {page_range(p['pages'])}"
    s += "."
    if p.get("doi"):
        s += f" DOI: {p['doi']}."
    return s


CITERS = {
    "apa": (cite_apa, ""),
    "harvard": (cite_harvard, ""),
    "mla": (cite_mla, ""),
    "chicago": (cite_chicago, ""),
    "ieee": (cite_ieee, "[{n}] "),
    "vancouver": (cite_vancouver, "{n}. "),
    "gbt7714": (cite_gbt7714, "[{n}] "),
}


def write_citations(papers: list[dict], fmt: str, out: str) -> None:
    fn, prefix = CITERS[fmt]
    lines = []
    for i, p in enumerate(papers, 1):
        lines.append(prefix.format(n=i) + fn(p))
    text = "\n\n".join(lines) + "\n"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# 数据交换格式
# ---------------------------------------------------------------------------


def write_ris(papers: list[dict], out: str) -> None:
    blocks = []
    for p in papers:
        lines = ["TY  - JOUR"]
        if p.get("title"):
            lines.append(f"TI  - {p['title']}")
        for a in p.get("authors") or []:
            lines.append(f"AU  - {a}")
        if p.get("journal"):
            lines.append(f"JF  - {p['journal']}")
        if p.get("journal_abbr"):
            lines.append(f"JA  - {p['journal_abbr']}")
        if year_of(p):
            lines.append(f"PY  - {year_of(p)}")
        if p.get("date"):
            lines.append(f"DA  - {p['date'].replace('-', '/')}")
        if p.get("abstract"):
            lines.append(f"AB  - {p['abstract']}")
        kws = (p.get("keywords") or []) + (p.get("mesh_terms") or [])
        for kw in kws:
            lines.append(f"KW  - {kw.lstrip('*')}")
        if p.get("volume"):
            lines.append(f"VL  - {p['volume']}")
        if p.get("issue"):
            lines.append(f"IS  - {p['issue']}")
        sp, ep = expand_pages(p.get("pages", ""))
        if sp:
            lines.append(f"SP  - {sp}")
        if ep:
            lines.append(f"EP  - {ep}")
        if p.get("doi"):
            lines.append(f"DO  - {p['doi']}")
        if p.get("pmid"):
            lines.append(f"AN  - {p['pmid']}")
        if p.get("url"):
            lines.append(f"UR  - {p['url']}")
        lines.append("ER  - ")
        blocks.append("\n".join(lines))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks) + "\n")


_BIB_SPECIAL = re.compile(r"([&%$#_{}])")


def _bib_escape(s: str) -> str:
    return _BIB_SPECIAL.sub(r"\\\1", s or "")


def write_bibtex(papers: list[dict], out: str) -> None:
    entries = []
    for p in papers:
        au = author_list(p)
        first_last = (au[0][0] if au else "anon").lower()
        first_last = re.sub(r"[^a-z0-9]", "", first_last) or "anon"
        word = re.sub(r"[^A-Za-z0-9 ]", "", p.get("title", "")).split()
        key = f"{first_last}{year_of(p)}{word[0].lower() if word else ''}"
        fields = []
        fields.append(("title", _bib_escape(p.get("title", ""))))
        if au:
            fields.append(("author", " and ".join(f"{l}, {g}".rstrip(", ") for l, g in au)))
        if p.get("journal"):
            fields.append(("journal", _bib_escape(p["journal"])))
        if year_of(p):
            fields.append(("year", year_of(p)))
        if p.get("volume"):
            fields.append(("volume", p["volume"]))
        if p.get("issue"):
            fields.append(("number", p["issue"]))
        if p.get("pages"):
            fields.append(("pages", page_range(p["pages"], dash="--")))
        if p.get("doi"):
            fields.append(("doi", p["doi"]))
        if p.get("abstract"):
            fields.append(("abstract", _bib_escape(p["abstract"])))
        kws = (p.get("keywords") or []) + [m.lstrip("*") for m in (p.get("mesh_terms") or [])]
        if kws:
            fields.append(("keywords", ", ".join(kws)))
        if p.get("pmid"):
            fields.append(("pmid", p["pmid"]))
        if p.get("url"):
            fields.append(("url", p["url"]))
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
        entries.append(f"@article{{{key},\n{body}\n}}")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(entries) + "\n")


def _en_style(text: str) -> str:
    return f'<style face="normal" font="default" size="100%">{xml_escape(text)}</style>'


def write_endnote_xml(papers: list[dict], out: str) -> None:
    recs = []
    for p in papers:
        parts = ['  <record>', '    <ref-type name="Journal Article">17</ref-type>']
        au = p.get("authors") or []
        if au:
            parts.append("    <contributors><authors>")
            for a in au:
                parts.append(f"      <author>{_en_style(a)}</author>")
            parts.append("    </authors></contributors>")
        if p.get("title"):
            parts.append(f"    <titles><title>{_en_style(p['title'])}</title></titles>")
        if p.get("journal"):
            parts.append(f"    <periodical><full-title>{_en_style(p['journal'])}</full-title></periodical>")
        if p.get("abstract"):
            parts.append(f"    <abstract>{_en_style(p['abstract'])}</abstract>")
        kws = (p.get("keywords") or []) + [m.lstrip("*") for m in (p.get("mesh_terms") or [])]
        if kws:
            parts.append("    <keywords>")
            for kw in kws:
                parts.append(f"      <keyword>{_en_style(kw)}</keyword>")
            parts.append("    </keywords>")
        if year_of(p) or p.get("date"):
            parts.append("    <dates>")
            if year_of(p):
                parts.append(f"      <year>{_en_style(year_of(p))}</year>")
            if p.get("date"):
                parts.append(f"      <pub-dates><date>{_en_style(p['date'])}</date></pub-dates>")
            parts.append("    </dates>")
        if p.get("volume"):
            parts.append(f"    <volume>{_en_style(p['volume'])}</volume>")
        if p.get("issue"):
            parts.append(f"    <number>{_en_style(p['issue'])}</number>")
        if p.get("pages"):
            parts.append(f"    <pages>{_en_style(page_range(p['pages']))}</pages>")
        if p.get("doi"):
            parts.append(f"    <electronic-resource-num>{_en_style(p['doi'])}</electronic-resource-num>")
        if p.get("url"):
            parts.append(f"    <urls><related-urls><url>{_en_style(p['url'])}</url></related-urls></urls>")
        if p.get("pmid"):
            parts.append(f"    <accession-num>{_en_style(p['pmid'])}</accession-num>")
        parts.append("  </record>")
        recs.append("\n".join(parts))
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<xml>\n<records>\n" + "\n".join(recs) + "\n</records>\n</xml>\n"
    )
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)


def write_medline(papers: list[dict], out: str) -> None:
    """PubMed/MEDLINE 格式：优先输出抓取时保存的原始文本，缺失时按字段重建。"""
    blocks = []
    for p in papers:
        if p.get("_medline"):
            blocks.append(p["_medline"].rstrip())
            continue
        lines = []
        if p.get("pmid"):
            lines.append(f"PMID- {p['pmid']}")
        if p.get("date"):
            lines.append(f"DP  - {p['date']}")
        if p.get("title"):
            lines.append(f"TI  - {p['title']}")
        if p.get("pages"):
            lines.append(f"PG  - {p['pages']}")
        if p.get("abstract"):
            lines.append(f"AB  - {p['abstract']}")
        for a in p.get("authors") or []:
            lines.append(f"FAU - {a}")
        if p.get("journal"):
            lines.append(f"JT  - {p['journal']}")
        if p.get("journal_abbr"):
            lines.append(f"TA  - {p['journal_abbr']}")
        if p.get("volume"):
            lines.append(f"VI  - {p['volume']}")
        if p.get("issue"):
            lines.append(f"IP  - {p['issue']}")
        for m_ in p.get("mesh_terms") or []:
            lines.append(f"MH  - {m_}")
        for kw in p.get("keywords") or []:
            lines.append(f"OT  - {kw}")
        if p.get("doi"):
            lines.append(f"LID - {p['doi']} [doi]")
        blocks.append("\n".join(lines))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks) + "\n")


# ---------------------------------------------------------------------------
# 电子表格
# ---------------------------------------------------------------------------


def cell_value(p: dict, field: str) -> str:
    if field in ("keywords", "mesh", "pub_types"):
        vals = p.get({"keywords": "keywords", "mesh": "mesh_terms", "pub_types": "pub_types"}[field]) or []
        return "; ".join(v.lstrip("*") for v in vals)
    if field == "authors":
        return "; ".join(p.get("authors") or [])
    if field == "date":
        return p.get("date", "")
    return str(p.get(field, "") or "")


def write_csv_file(papers: list[dict], fields: list[str], out: str) -> None:
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([FIELD_LABELS[f] for f in fields])
        for p in papers:
            w.writerow([cell_value(p, f) for f in fields])


def write_xlsx_file(papers: list[dict], fields: list[str], out: str, url_source: str) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        print(
            "[xlsx] 缺少 openpyxl，请先安装：pip install openpyxl",
            file=sys.stderr,
        )
        raise SystemExit(2)

    wb = Workbook()
    ws = wb.active
    ws.title = "Papers"
    bold = Font(bold=True)
    for c, f in enumerate(fields, 1):
        cell = ws.cell(row=1, column=c, value=FIELD_LABELS[f])
        cell.font = bold
    for r, p in enumerate(papers, 2):
        for c, f in enumerate(fields, 1):
            val = cell_value(p, f)
            cell = ws.cell(row=r, column=c, value=val)
            link = ""
            if f in ID_URL and val:
                link = ID_URL[f](val)
            elif f == "url":
                link = best_url(p, url_source)
                cell.value = link or val
            if link:
                cell.hyperlink = link
                cell.style = "Hyperlink"
            if f == "abstract":
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    widths = {
        "title": 50, "authors": 35, "journal": 30, "date": 12, "abstract": 80,
        "keywords": 35, "mesh": 40, "pub_types": 25, "volume": 8, "issue": 8,
        "pages": 12, "pmid": 12, "doi": 28, "pmc": 14, "arxiv": 14, "url": 40,
    }
    for c, f in enumerate(fields, 1):
        ws.column_dimensions[get_column_letter(c)].width = widths.get(f, 18)
    ws.freeze_panes = "A2"
    wb.save(out)


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="文献导出器（RIS/BibTeX/EndNote/MEDLINE/引用格式/CSV/XLSX）")
    ap.add_argument("--input", required=True, help="pubmed.py 产出的 JSON 路径")
    ap.add_argument(
        "--format",
        required=True,
        choices=["ris", "bibtex", "enxml", "medline", "apa", "harvard", "mla",
                 "chicago", "ieee", "vancouver", "gbt7714", "csv", "xlsx"],
    )
    ap.add_argument("--out", required=True, help="输出文件路径")
    ap.add_argument("--fields", default="title,authors,journal,date,abstract,doi,url",
                    help=f"csv/xlsx 导出字段，逗号分隔。可选：{', '.join(FIELDS)}")
    ap.add_argument("--url-source", default="auto", choices=["auto", "doi", "pmc", "pmid", "arxiv"],
                    help="网址列取值优先级（默认 auto：doi>pmc>pmid>arxiv）")
    ap.add_argument("--ids", default=None, help="只导出这些 PMID（逗号分隔）")
    ap.add_argument("--exclude", default=None, help="排除这些 PMID（逗号分隔）")
    args = ap.parse_args(argv)

    papers = filter_ids(load_papers(args.input), args.ids, args.exclude)
    if not papers:
        print("[export] 筛选后无论文可导出", file=sys.stderr)
        return 1

    fmt = args.format
    if fmt in CITERS:
        write_citations(papers, fmt, args.out)
    elif fmt == "ris":
        write_ris(papers, args.out)
    elif fmt == "bibtex":
        write_bibtex(papers, args.out)
    elif fmt == "enxml":
        write_endnote_xml(papers, args.out)
    elif fmt == "medline":
        write_medline(papers, args.out)
    elif fmt in ("csv", "xlsx"):
        fields = [f.strip() for f in args.fields.split(",") if f.strip()]
        bad = [f for f in fields if f not in FIELDS]
        if bad:
            print(f"[export] 未知字段: {bad}；可选：{FIELDS}", file=sys.stderr)
            return 2
        if fmt == "csv":
            write_csv_file(papers, fields, args.out)
        else:
            write_xlsx_file(papers, fields, args.out, args.url_source)
    print(f"[export] {len(papers)} 篇 -> {args.out} ({fmt})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
