#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import.py — 将外部文献列表导入为 skill 统一 JSON（v1.5）

支持格式（--format）：
  - ris     : RIS（Zotero / EndNote / NoteExpress 通用导出）
  - bibtex  : BibTeX（.bib）
  - medline : PubMed / MEDLINE 纯文本（.txt / .nbib / .medline）
  - json    : 本 skill 统一 JSON（papers / included / results），或论文 dict 数组
  - csv     : CNKI / 万方 / 维普 / Scopus / WoS 导出的表格（自动兼容 GBK）
  - xlsx    : 同上表格的 Excel 版本（需要 openpyxl）
  - auto    : 按扩展名 + 内容嗅探自动识别（默认）

输出 Schema 与 pubmed.py 一致，abstract 等字段会被完整保留，便于：
  - 从 SciSearch / Zotero / EndNote 导出的题录重新进入本工作流
  - 中文数据库导出的表格进入 merge.py 与其他来源汇总去重
  - 导出 → 人工增补 → 再导入的 round-trip
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from typing import Any

# 复用 pubmed.py 的 MEDLINE 解析器，避免重复实现
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pubmed import parse_medline, record_to_paper  # type: ignore

Paper = dict[str, Any]


def _first(mapping: dict[str, list[str]], key: str) -> str:
    return " ".join(mapping.get(key, [])) if key in mapping else ""


def _all(mapping: dict[str, list[str]], key: str) -> list[str]:
    return mapping.get(key, [])


# ---------------------------------------------------------------------------
# RIS
# ---------------------------------------------------------------------------

_RIS_JOURNAL_TAGS: set[str] = {"T2", "JF", "JA", "JO"}
_RIS_DATE_TAGS: set[str] = {"PY", "DA", "Y1", "Y2"}


def parse_ris(text: str) -> list[Paper]:
    """解析 RIS 格式；每条记录以 TY 开始、ER 结束。"""
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None

    for raw in text.splitlines():
        line: str = raw.strip()
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
        tag: str = line[:2].upper()
        value: str = line[6:].strip() if len(line) > 6 else ""
        current.setdefault(tag, []).append(value)

    papers: list[Paper] = []
    for rec in records:
        pmid: str = _first(rec, "AN")
        doi: str = _first(rec, "DO")
        url: str = _first(rec, "UR")
        if not url and pmid:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        if not url and doi:
            url = f"https://doi.org/{doi}"

        date: str = _first(rec, "DA") or _first(rec, "PY") or _first(rec, "Y1")
        date = re.sub(r"/(\d{2})/(\d{2})$", r"-\1-\2", date)  # RIS 常见 YYYY/MM/DD
        date = re.sub(r"/", "-", date)

        journal: str = ""
        for tag in _RIS_JOURNAL_TAGS:
            if tag in rec:
                journal = _first(rec, tag)
                break

        # SP/EP 合并为页码
        sp: str = _first(rec, "SP")
        ep: str = _first(rec, "EP")
        pages: str = f"{sp}-{ep}" if sp and ep else (sp or _first(rec, "CP"))

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

_BIB_RE: re.Pattern[str] = re.compile(
    r"@\w+\s*\{\s*[^,]*,\s*(?P<body>.*?)\n\s*\}\s*$",
    re.DOTALL | re.MULTILINE,
)


def _bib_unbrace(s: str) -> str:
    """去掉 BibTeX 值最外层 {} 或 ""，不做 LaTeX 反斜杠展开。"""
    s = s.strip()
    if len(s) >= 2 and ((s[0] == "{" and s[-1] == "}") or (s[0] == '"' and s[-1] == '"')):
        s = s[1:-1].strip()
    return s


def _bib_split_body(body: str) -> dict[str, str]:
    """按顶层逗号拆分字段；大括号可嵌套，引号内不拆分。"""
    fields: dict[str, str] = {}
    depth: int = 0
    in_quote: str | None = None
    start: int = 0
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
            part: str = body[start:i].strip()
            if "=" in part:
                k: str
                v: str
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
    parts: list[str] = re.split(r"\s+and\s+", raw, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def parse_bibtex(text: str) -> list[Paper]:
    """简单 BibTeX 解析：支持 @article / @inproceedings 等，保留 abstract。"""
    papers: list[Paper] = []
    # 规范化条目边界：@xxx { ..., }
    text = re.sub(r"(\n\s*)+\}", "\n}", text)
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,]+),\s*(.*?)\n\s*\}", text, re.DOTALL):
        body: str = m.group(3)
        fields: dict[str, str] = _bib_split_body(body)
        if not fields:
            continue

        authors: list[str] = _bib_authors(fields.get("author", ""))
        year: str = fields.get("year", "")
        month: str = fields.get("month", "")
        date: str = year
        if month:
            # 尝试把英文月名转成 01-12
            try:
                import calendar
                mm: int = list(calendar.month_abbr).index(month[:3].title())
                if mm:
                    date = f"{year}-{mm:02d}"
            except Exception:
                date = f"{year}-{month}"

        doi: str = fields.get("doi", "")
        url: str = fields.get("url", "")
        if not url and doi:
            url = f"https://doi.org/{doi}"

        pages: str = fields.get("pages", "")
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

def parse_medline_import(text: str) -> list[Paper]:
    records: list[list[tuple[str, str]]] = parse_medline(text)
    return [record_to_paper(r) for r in records]


# ---------------------------------------------------------------------------
# 统一记录结构（缺键补全）
# ---------------------------------------------------------------------------

CANON_STR_KEYS: tuple[str, ...] = (
    "pmid", "title", "date", "journal", "journal_abbr", "abstract",
    "volume", "issue", "pages", "language", "doi", "pmc", "arxiv", "pii", "url",
)
CANON_LIST_KEYS: tuple[str, ...] = (
    "authors", "authors_abbr", "mesh_terms", "keywords", "pub_types",
)

# 字段模板：list 默认值不得在记录间共享，取值请走 _normalize_paper() 深拷贝
CANON_KEYS: dict[str, Any] = {k: "" for k in CANON_STR_KEYS}
CANON_KEYS.update({k: [] for k in CANON_LIST_KEYS})


def _normalize_paper(p: dict[str, Any]) -> Paper:
    """补齐规范字段（str 缺为 ""，list 缺为 []），并保留额外字段；url 缺失时按 doi/pmid 生成。"""
    out: Paper = {}
    key: str
    val: Any
    for key in CANON_STR_KEYS:
        val = p.get(key, "")
        if val is None:
            val = ""
        out[key] = val.strip() if isinstance(val, str) else str(val)
    for key in CANON_LIST_KEYS:
        raw: Any = p.get(key, [])
        items: list[str] = []
        if isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw if str(x).strip()]
        elif isinstance(raw, str) and raw.strip():
            items = _split_multi(raw, r"[;；,，、]")
        out[key] = items
    # 规范字段之外的自定义字段（如 merge.py 写入的 sources / merged_from）原样保留
    extra_key: str
    for extra_key, val in p.items():
        if extra_key not in out:
            out[extra_key] = val
    if not out.get("url"):
        if out.get("doi"):
            out["url"] = f"https://doi.org/{out['doi']}"
        elif out.get("pmid"):
            out["url"] = f"https://pubmed.ncbi.nlm.nih.gov/{out['pmid']}/"
    return out


# ---------------------------------------------------------------------------
# 格式识别
# ---------------------------------------------------------------------------

_EXT_FORMAT: dict[str, str] = {
    ".json": "json",
    ".jsonl": "json",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".ris": "ris",
    ".bib": "bibtex",
    ".bibtex": "bibtex",
    ".txt": "medline",
    ".nbib": "medline",
    ".medline": "medline",
}

_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk", "gb18030")


def _read_text(path: str) -> str:
    """按 utf-8-sig / utf-8 / gbk / gb18030 顺序容错读取文本。"""
    enc: str
    for enc in _ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                text: str = fh.read()
            return text
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="gb18030", errors="replace") as fh2:
        fallback: str = fh2.read()
    return fallback


def detect_format(path: str) -> str:
    """先按扩展名判断，扩展名不确定时用内容嗅探。"""
    ext: str = os.path.splitext(path)[1].lower()
    if ext in _EXT_FORMAT:
        return _EXT_FORMAT[ext]

    head: str = ""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            head = fh.read(8192)
    except OSError:
        return "medline"

    sample: str = head.lstrip("\ufeff").lstrip()
    if not sample:
        return "medline"
    if sample.startswith("{"):
        return "json"
    if re.search(r"(?m)^\s*TY\s+-\s", sample):
        return "ris"
    if re.search(r"(?i)@\s*(article|inproceedings|incollection|book|misc|phdthesis|mastersthesis)\s*\{", sample):
        return "bibtex"
    if re.search(r"(?m)^(PMID|AU|TI|AB|DP|AID|JT)\s{2}-", sample):
        return "medline"

    lines: list[str] = sample.splitlines()
    first: str = lines[0] if lines else ""
    if "," in first:
        widths: set[int] = set()
        line: str
        for line in lines[:5]:
            try:
                cells: list[str] = next(csv.reader([line]))
            except (StopIteration, csv.Error):
                continue
            if len(cells) > 1:
                widths.add(len(cells))
        if len(widths) == 1:
            return "csv"
    return "medline"


# ---------------------------------------------------------------------------
# CSV / XLSX：中英文表头兼容
# ---------------------------------------------------------------------------

COLUMN_ALIASES: dict[str, list[str]] = {
    "title": ["title", "题名", "篇名", "题目", "文献标题", "文献题名", "articletitle", "articlename", "文献名称"],
    "authors": ["authors", "author", "作者", "作者姓名", "著者", "全部作者", "creator"],
    "journal": ["journal", "期刊", "期刊名称", "刊名", "文献来源", "来源", "source", "publication",
                "出版物名称", "publicationtitle", "journaltitle"],
    "date": ["date", "year", "发表日期", "出版日期", "日期", "发表年份", "出版年", "publicationyear", "py", "年份"],
    "abstract": ["abstract", "摘要", "文摘", "summary"],
    "keywords": ["keywords", "keyword", "关键词", "主题词", "authorkeywords", "key_words", "kws", "字"],
    "doi": ["doi"],
    "pmid": ["pmid", "pubmedid", "pmid号", "pubmed"],
    "pmc": ["pmc", "pmcid", "pmc编号"],
    "url": ["url", "网址", "链接", "link", "数据库链接", "doi链接"],
    "volume": ["volume", "卷", "vol"],
    "issue": ["issue", "期", "number"],
    "pages": ["pages", "页码", "起止页码", "page", "pp"],
    "pub_types": ["pubtypes", "publicationtype", "文献类型", "类型", "documenttype"],
    "language": ["language", "语种", "语言"],
}

# 列名 → 规范字段（扁平索引，后出现的规范字段在冲突时优先）
_HEADER_INDEX: dict[str, str] = {
    alias: canon
    for canon, aliases in COLUMN_ALIASES.items()
    for alias in aliases
}

_PAREN_RE: re.Pattern[str] = re.compile(r"[\(（][^)）]*[\)）]")


def _map_header(header: str) -> str:
    """表头归一化后匹配规范字段名，未匹配返回 ""。"""
    name: str = (header or "").replace("\ufeff", "").strip().lower()
    name = _PAREN_RE.sub("", name)
    name = re.sub(r"\s+", "", name)
    if not name:
        return ""
    return _HEADER_INDEX.get(name, "")


def _split_multi(raw: str, pattern: str) -> list[str]:
    """按给定分隔集切分并去重保序。"""
    seen: set[str] = set()
    out: list[str] = []
    part: str
    for part in re.split(pattern, raw or ""):
        item: str = part.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _split_authors(raw: str) -> list[str]:
    """作者串切分：优先分号；无分号时才按逗号切，且切出的每段必须非空且长度 > 1。"""
    text: str = (raw or "").strip()
    if not text:
        return []
    parts: list[str]
    if ";" in text or "；" in text:
        parts = re.split(r"[;；]+", text)
    else:
        parts = [p for p in re.split(r"[,，、]+", text) if p.strip() and len(p.strip()) > 1]
        if len(parts) <= 1:
            parts = [text]
    return _split_multi(";".join(parts), r"[;；]")


def _row_to_paper(row: dict[str, str]) -> Paper:
    """一行表格数据（键为原始表头）→ 规范 Paper，csv / xlsx 共用。"""
    canon: dict[str, str] = {}
    raw_key: Any
    raw_val: Any
    for raw_key, raw_val in row.items():
        if raw_key is None:
            continue
        field: str = _map_header(str(raw_key))
        if not field:
            continue
        value: str = "" if raw_val is None else str(raw_val).strip()
        if value and not canon.get(field):
            canon[field] = value

    authors: list[str] = _split_authors(canon.get("authors", ""))
    paper: Paper = {
        "pmid": canon.get("pmid", ""),
        "title": canon.get("title", ""),
        "authors": authors,
        "authors_abbr": authors,
        "date": canon.get("date", ""),
        "journal": canon.get("journal", ""),
        "journal_abbr": canon.get("journal", ""),
        "abstract": canon.get("abstract", ""),
        "mesh_terms": [],
        "keywords": _split_multi(canon.get("keywords", ""), r"[;；,，、]"),
        "volume": canon.get("volume", ""),
        "issue": canon.get("issue", ""),
        "pages": canon.get("pages", ""),
        "pub_types": _split_multi(canon.get("pub_types", ""), r"[;；,，、]"),
        "language": canon.get("language", ""),
        "doi": canon.get("doi", ""),
        "pmc": canon.get("pmc", ""),
        "arxiv": "",
        "pii": "",
        "url": canon.get("url", ""),
    }
    return _normalize_paper(paper)


def parse_csv(path: str) -> list[Paper]:
    """解析 CNKI / 万方 / 维普 / Scopus / WoS 等导出的 CSV，编码自动容错。"""
    text: str = _read_text(path)
    reader: "csv.DictReader[str]" = csv.DictReader(io.StringIO(text, newline=""))
    papers: list[Paper] = []
    row: dict[str, Any]
    for row in reader:
        has_value: bool = False
        val: Any
        for val in row.values():
            if isinstance(val, str) and val.strip():
                has_value = True
                break
        if not has_value:
            continue
        papers.append(_row_to_paper(row))
    return papers


def parse_xlsx(path: str) -> list[Paper]:
    """解析 Excel 表格（取第一个 worksheet，首行当表头）。"""
    try:
        import openpyxl  # type: ignore  # 延迟导入：非 xlsx 场景不依赖
    except ImportError as exc:
        raise RuntimeError("读取 xlsx 需要 openpyxl 包，请先安装后再试：pip install openpyxl") from exc

    wb: Any = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws: Any = wb[wb.sheetnames[0]]
    papers: list[Paper] = []
    header: list[str] = []
    raw_row: tuple[Any, ...]
    try:
        for raw_row in ws.iter_rows(values_only=True):
            cells: list[str] = ["" if c is None else str(c).strip() for c in raw_row]
            if not any(cells):
                continue
            if not header:
                header = cells
                continue
            row: dict[str, str] = {}
            i: int = 0
            while i < min(len(header), len(cells)):
                col: str = header[i]
                if col:
                    row[col] = cells[i]
                i += 1
            papers.append(_row_to_paper(row))
    finally:
        wb.close()
    return papers


def load_json_papers(path: str) -> list[Paper]:
    """读取统一 JSON（papers / included / results）或论文 dict 数组，并补齐规范字段。"""
    fh: io.TextIOWrapper
    with open(path, "r", encoding="utf-8-sig") as fh:
        text: str = fh.read()

    raw: Any
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        # 兼容 .jsonl：逐行解析
        records_line: list[Any] = []
        line: str
        for line in text.splitlines():
            s: str = line.strip().rstrip(",")
            if not s or s in ("[", "]"):
                continue
            try:
                records_line.append(json.loads(s))
            except json.JSONDecodeError:
                continue
        raw = records_line

    records: list[Any] = []
    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        key: str
        for key in ("papers", "included", "results"):
            sub: Any = raw.get(key)
            if isinstance(sub, list):
                records = sub
                break
    item: Any
    return [_normalize_paper(item) for item in records if isinstance(item, dict)]


def _load_by_format(path: str, fmt: str) -> list[Paper]:
    """按格式路由到对应解析器。"""
    if fmt == "csv":
        return parse_csv(path)
    if fmt == "json":
        return load_json_papers(path)
    if fmt == "xlsx":
        return parse_xlsx(path)
    text: str = _read_text(path)
    if fmt == "ris":
        return parse_ris(text)
    if fmt == "bibtex":
        return parse_bibtex(text)
    return parse_medline_import(text)


# ---------------------------------------------------------------------------

def _dump(obj: Paper, out: str) -> None:
    text: str = json.dumps(obj, ensure_ascii=False, indent=2)
    if out == "-" or not out:
        sys.stdout.write(text + "\n")
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[out] 已写入 {out}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description="RIS / BibTeX / MEDLINE / JSON / CSV / XLSX 文献导入为统一 JSON",
    )
    ap.add_argument("--input", required=True, help="输入文件（.ris / .bib / .txt / .json / .csv / .xlsx）")
    ap.add_argument(
        "--format",
        nargs="?",
        default="auto",
        choices=["ris", "bibtex", "medline", "json", "csv", "xlsx", "auto"],
        help="输入格式，默认 auto（按扩展名 + 内容自动识别）",
    )
    ap.add_argument("--out", required=True, help="输出 JSON 路径，'-' 输出到 stdout")
    args: argparse.Namespace = ap.parse_args(argv)

    fmt: str = (args.format or "auto").lower()
    if fmt == "auto":
        fmt = detect_format(args.input)

    papers: list[Paper] = _load_by_format(args.input, fmt)

    result: Paper = {
        "source": "import",
        "format": fmt,
        "source_file": os.path.basename(args.input),
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
