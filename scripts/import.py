#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import.py — 将 RIS / BibTeX / PubMed(MEDLINE) 文献列表导入为 skill 统一 JSON

输出 Schema 与 pubmed.py 一致，abstract 等字段会被完整保留，便于：
  - 从 SciSearch / Zotero / EndNote 导出的题录重新进入本工作流
  - 导出 → 人工增补 → 再导入的 round-trip
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

# 复用 pubmed.py 的 MEDLINE 解析器，避免重复实现
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pubmed import parse_medline, record_to_paper  # type: ignore


def _first(mapping: dict[str, list[str]], key: str) -> str:
    return " ".join(mapping.get(key, [])) if key in mapping else ""


def _all(mapping: dict[str, list[str]], key: str) -> list[str]:
    return mapping.get(key, [])


# ---------------------------------------------------------------------------
# RIS
# ---------------------------------------------------------------------------

_RIS_JOURNAL_TAGS = {"T2", "JF", "JA", "JO"}
_RIS_DATE_TAGS = {"PY", "DA", "Y1", "Y2"}


def parse_ris(text: str) -> list[dict]:
    """解析 RIS 格式；每条记录以 TY 开始、ER 结束。"""
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 4 or line.startswith("ER"):
            current = None
            continue
        if line[:2] == "TY":
            current = {"TY": [line[6:].strip() if len(line) > 6 else ""]}
            records.append(current)
            continue
        if current is None:
            continue
        # tag 为前 2 个大写字母；值从第 6 列起
        tag = line[:2].upper()
        value = line[6:].strip() if len(line) > 6 else ""
        current.setdefault(tag, []).append(value)

    papers: list[dict] = []
    for rec in records:
        pmid = _first(rec, "AN")
        doi = _first(rec, "DO")
        url = _first(rec, "UR")
        if not url and pmid:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        if not url and doi:
            url = f"https://doi.org/{doi}"

        date = _first(rec, "DA") or _first(rec, "PY") or _first(rec, "Y1")
        date = re.sub(r"/(\d{2})/(\d{2})$", r"-\1-\2", date)  # RIS 常见 YYYY/MM/DD
        date = re.sub(r"/", "-", date)

        journal = ""
        for tag in _RIS_JOURNAL_TAGS:
            if tag in rec:
                journal = _first(rec, tag)
                break

        # SP/EP 合并为页码
        sp, ep = _first(rec, "SP"), _first(rec, "EP")
        pages = f"{sp}-{ep}" if sp and ep else (sp or _first(rec, "CP"))

        papers.append({
            "pmid": pmid,
            "title": _first(rec, "TI"),
            "authors": _all(rec, "AU") or _all(rec, "A1"),
            "authors_abbr": _all(rec, "AU") or _all(rec, "A1"),
            "date": date,
            "journal": journal,
            "journal_abbr": _first(rec, "J2") or _first(rec, "JA"),
            "abstract": _first(rec, "AB") or _first(rec, "N2"),
            "mesh_terms": [],
            "keywords": [k.strip() for k in ("; ".join(_all(rec, "KW"))).split(";") if k.strip()],
            "volume": _first(rec, "VL"),
            "issue": _first(rec, "IS"),
            "pages": pages,
            "pub_types": [],
            "language": _first(rec, "LA"),
            "doi": doi,
            "pmc": "",
            "arxiv": "",
            "pii": "",
            "url": url,
        })
    return papers


# ---------------------------------------------------------------------------
# BibTeX
# ---------------------------------------------------------------------------


def _bib_unbrace(s: str) -> str:
    """去掉 BibTeX 值最外层 {} 或 ""，不做 LaTeX 反斜杠展开。"""
    s = s.strip()
    if len(s) >= 2 and ((s[0] == "{" and s[-1] == "}") or (s[0] == '"' and s[-1] == '"')):
        s = s[1:-1].strip()
    return s


def _bib_split_body(body: str) -> dict[str, str]:
    """按顶层逗号拆分字段；大括号可嵌套，引号内不拆分。"""
    fields: dict[str, str] = {}
    depth = 0
    in_quote: str | None = None
    start = 0
    for i, ch in enumerate(body):
        if ch == "{":
            if not in_quote:
                depth += 1
        elif ch == "}":
            if not in_quote:
                depth -= 1
        elif ch in "\"'":
            if depth == 0:
                if in_quote is None:
                    in_quote = ch
                elif in_quote == ch:
                    in_quote = None
        elif ch == "," and depth == 0 and in_quote is None:
            part = body[start:i].strip()
            if "=" in part:
                k, v = part.split("=", 1)
                fields[k.strip().lower()] = _bib_unbrace(v.strip())
            start = i + 1
    part = body[start:].strip().rstrip(",")
    if "=" in part:
        k, v = part.split("=", 1)
        fields[k.strip().lower()] = _bib_unbrace(v.strip())
    return fields


def _bib_authors(raw: str) -> list[str]:
    """BibTeX author: 'Voors, Adriaan A and Doe, John' -> ['Voors, Adriaan A', 'Doe, John']。"""
    parts = re.split(r"\s+and\s+", raw, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def parse_bibtex(text: str) -> list[dict]:
    """简单 BibTeX 解析：支持 @article / @inproceedings 等，保留 abstract。"""
    papers: list[dict] = []
    # 规范化条目边界：@xxx { ..., }
    text = re.sub(r"(\n\s*)+\}", "\n}", text)
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,]+),\s*(.*?)\n\s*\}", text, re.DOTALL):
        body = m.group(3)
        fields = _bib_split_body(body)
        if not fields:
            continue

        authors = _bib_authors(fields.get("author", ""))
        year = fields.get("year", "")
        month = fields.get("month", "")
        date = year
        if month:
            # 尝试把英文月名转成 01-12
            try:
                import calendar
                mm = list(calendar.month_abbr).index(month[:3].title())
                if mm:
                    date = f"{year}-{mm:02d}"
            except Exception:
                date = f"{year}-{month}"

        doi = fields.get("doi", "")
        url = fields.get("url", "")
        if not url and doi:
            url = f"https://doi.org/{doi}"

        pages = fields.get("pages", "")
        pages = pages.replace("--", "-")

        papers.append({
            "pmid": "",
            "title": fields.get("title", ""),
            "authors": authors,
            "authors_abbr": authors,
            "date": date,
            "journal": fields.get("journal", ""),
            "journal_abbr": fields.get("journal", ""),
            "abstract": fields.get("abstract", ""),
            "mesh_terms": [],
            "keywords": [k.strip() for k in fields.get("keywords", "").split(",") if k.strip()],
            "volume": fields.get("volume", ""),
            "issue": fields.get("number", ""),
            "pages": pages,
            "pub_types": [],
            "language": "",
            "doi": doi,
            "pmc": "",
            "arxiv": "",
            "pii": "",
            "url": url,
        })
    return papers


# ---------------------------------------------------------------------------
# MEDLINE (复用 pubmed.py)
# ---------------------------------------------------------------------------

def parse_medline_import(text: str) -> list[dict]:
    records = parse_medline(text)
    return [record_to_paper(r) for r in records]


# ---------------------------------------------------------------------------

def _dump(obj: dict, out: str) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if out == "-" or not out:
        sys.stdout.write(text + "\n")
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[out] 已写入 {out}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RIS / BibTeX / PubMed 文献导入为统一 JSON")
    ap.add_argument("--input", required=True, help="输入文件（.ris / .bib / .txt MEDLINE）")
    ap.add_argument(
        "--format",
        required=True,
        choices=["ris", "bibtex", "medline"],
        help="输入格式（根据扩展名自动推断失败时显式指定）",
    )
    ap.add_argument("--out", required=True, help="输出 JSON 路径，'-' 输出到 stdout")
    args = ap.parse_args(argv)

    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    fmt = args.format
    if fmt == "ris":
        papers = parse_ris(text)
    elif fmt == "bibtex":
        papers = parse_bibtex(text)
    else:
        papers = parse_medline_import(text)

    result = {
        "source": "import",
        "format": fmt,
        "import_date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(papers),
        "fetched": len(papers),
        "papers": papers,
    }
    _dump(result, args.out)
    print(f"[import] {len(papers)} 篇 {fmt} -> 统一 JSON", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
