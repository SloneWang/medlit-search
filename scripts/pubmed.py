#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pubmed.py — PubMed (NCBI E-utilities) 检索 / 拉取 / MeSH 校验工具

流程移植自 SciSearch 桌面文献检索工具（同一作者）：
  EUtilities.SearchAsync  -> esearch.fcgi (retmode=json, retstart/retmax 分页)
  EUtilities.FetchAsync   -> efetch.fcgi  (rettype=medline, retmode=text 或 xml)
  Medline.fs              -> MEDLINE 文本解析（空行分隔记录，"KEY- value" 定长列，续行拼接）
  NCBIPaperFactory        -> 字段提取；此处修正两处：
      1) 发表日期用 DP（实际发表日期），SciSearch 误用 MHDA（MeSH 标引日期）
      2) F# 解析器 List.pairwise 会丢失每条记录最后一个属性，此处修复

限流：无 API key 3 req/s，有 key 10 req/s（NCBI 官方限制）。
仅依赖 Python 标准库。

参数语义（与 NCBI 文档一致）：
  --retmax  : ESearch 单次返回的 UID 数量（分页页大小），默认 200
  --limit   : 用户希望拉取的文献总量（0=全部）
  --batch   : EFetch 单批拉取的篇数（<=200，建议 100-200）
  --retmode : EFetch 返回模式，medline 或 xml（默认 medline）
  --mindate/--maxdate/--datetype : 日期过滤

子命令：
  search  检索并拉取全文级元数据，输出统一 JSON
  fetch   按 PMID 列表重新拉取（复筛/刷新用）
  mesh    校验术语是否为 MeSH 主题词，并给出官方建议词
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MESH_LOOKUP = "https://id.nlm.nih.gov/mesh/lookup/descriptor"
USER_AGENT = "workbuddy-medlit-skill/1.2 (PubMed literature screening)"

# NCBI 建议 EFetch 单批不超过 200 个 UID（URL 过长会失败）
MAX_EFETCH_BATCH = 200

# ---------------------------------------------------------------------------
# 限流器（移植 EUtilities.ResetTimer：全局串行计时，每次请求至少间隔 Interval）
# ---------------------------------------------------------------------------


class RateLimiter:
    def __init__(self, api_key: str | None):
        self.api_key = api_key or os.environ.get("NCBI_API_KEY")
        self.interval = 1.0 / (10.0 if self.api_key else 3.0)
        self._next = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
        self._next = time.monotonic() + self.interval


_LIMITER: RateLimiter | None = None


def _limiter() -> RateLimiter:
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = RateLimiter(None)
    return _LIMITER


def http_get(url: str, timeout: int = 60, retries: int = 4) -> bytes:
    """带限流 + 指数退避重试的 GET。429/5xx 重试，其余直接抛。"""
    last_err: Exception | None = None
    for attempt in range(retries):
        _limiter().wait()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 or e.code >= 500:
                time.sleep(min(2.0 ** attempt, 16.0))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(min(2.0 ** attempt, 16.0))
    raise RuntimeError(f"请求失败（重试 {retries} 次后仍失败）: {url} -> {last_err}")


def _tool_params(args) -> dict[str, str]:
    p: dict[str, str] = {"tool": "workbuddy_medlit"}
    if getattr(args, "email", None):
        p["email"] = args.email
    api_key = getattr(args, "api_key", None) or os.environ.get("NCBI_API_KEY")
    if api_key:
        p["api_key"] = api_key
    return p


def _urlencode(params: dict[str, str | None]) -> str:
    """使用 urllib.parse.urlencode，仅保留非空值，空格编码为 +。"""
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    return urllib.parse.urlencode(clean, doseq=False, safe="")


# ---------------------------------------------------------------------------
# MEDLINE 解析器（Medline.fs 的 Python 移植，修复末属性丢失问题）
# ---------------------------------------------------------------------------


def parse_medline(text: str) -> list[list[tuple[str, str]]]:
    """把 MEDLINE 文本解析为 records；每条 record 是 [(KEY, value), ...]。

    规则（与 Medline.fs normalize 一致）：
      - 空行 / 长度 < 6 的行 -> 记录分隔符
      - 第 5 个字符为 '-'（line[4]=='-'）-> 新属性行，KEY 为前 4 字符
      - 其余 -> 上一属性的续行，值以空格拼接
    """
    norm: list[tuple[str | None, str] | None] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip() or len(line) < 6:
            norm.append(None)
        elif line[4] == "-":
            norm.append((line[0:4].rstrip(), line[6:].rstrip()))
        else:
            norm.append((None, line[6:].rstrip() if len(line) > 6 else ""))

    records: list[list[tuple[str, str]]] = []
    current: list[tuple[str | None, str]] = []

    def flush(buf: list[tuple[str | None, str]]) -> None:
        if not buf:
            return
        props: list[tuple[str, str]] = []
        key: str | None = None
        parts: list[str] = []
        for k, v in buf:
            if k is not None:
                if key is not None:
                    props.append((key, " ".join(p for p in parts if p)))
                key, parts = k, [v]
            else:
                parts.append(v)
        if key is not None:  # 修复：F# pairwise 会漏掉最后一个属性
            props.append((key, " ".join(p for p in parts if p)))
        if props:
            records.append(props)

    for item in norm:
        if item is None:
            flush(current)
            current = []
        else:
            current.append(item)
    flush(current)
    return records


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_AID_RE = re.compile(r"^(?P<value>.+?)\s*\[(?P<tag>[A-Za-z -]+)\]\s*$")


def _first(props: list[tuple[str, str]], key: str) -> str:
    for k, v in props:
        if k == key:
            return v
    return ""


def _all(props: list[tuple[str, str]], key: str) -> list[str]:
    return [v for k, v in props if k == key]


def normalize_date(dp: str) -> str:
    """DP 字段 -> 'YYYY-MM-DD' / 'YYYY-MM' / 'YYYY'。例: '2023 May 15'、'2023 May-Jun'。"""
    dp = dp.strip()
    m = re.match(r"^(\d{4})(?:\s+([A-Za-z]{3,9}))?(?:\s+(\d{1,2}))?", dp)
    if not m:
        return dp
    year, mon, day = m.group(1), m.group(2), m.group(3)
    out = year
    if mon:
        mk = mon[:3].lower()
        if mk in _MONTHS:
            out += f"-{_MONTHS[mk]:02d}"
            if day:
                out += f"-{int(day):02d}"
    return out


def record_to_paper(props: list[tuple[str, str]]) -> dict:
    """NCBIPaperFactory.FromMedline 的扩展移植版。"""
    ids: dict[str, str] = {}
    for k in ("AID", "LID"):
        for v in _all(props, k):
            m = _AID_RE.match(v)
            if not m:
                continue
            tag = m.group("tag").strip().lower()
            val = m.group("value").strip()
            if tag == "doi":
                ids.setdefault("doi", val)
            elif tag in ("pmc", "pmcid"):
                ids.setdefault("pmc", val if val.upper().startswith("PMC") else f"PMC{val}")
            elif tag == "arxiv":
                ids.setdefault("arxiv", val.removeprefix("arXiv:").strip())
            elif tag == "pii":
                ids.setdefault("pii", val)
    pmc_field = _first(props, "PMC")
    if pmc_field and "pmc" not in ids:
        ids["pmc"] = pmc_field if pmc_field.upper().startswith("PMC") else f"PMC{pmc_field}"

    pmid = _first(props, "PMID")
    paper = {
        "pmid": pmid,
        "title": _first(props, "TI"),
        "authors": _all(props, "FAU") or _all(props, "AU"),
        "authors_abbr": _all(props, "AU"),
        "date": normalize_date(_first(props, "DP")),
        "journal": _first(props, "JT"),
        "journal_abbr": _first(props, "TA"),
        "abstract": _first(props, "AB"),
        "mesh_terms": _all(props, "MH"),
        "keywords": _all(props, "OT"),
        "volume": _first(props, "VI"),
        "issue": _first(props, "IP"),
        "pages": _first(props, "PG"),
        "pub_types": _all(props, "PT"),
        "language": _first(props, "LA"),
        "doi": ids.get("doi", ""),
        "pmc": ids.get("pmc", ""),
        "arxiv": ids.get("arxiv", ""),
        "pii": ids.get("pii", ""),
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
    }
    return paper


def attach_raw_medline(papers: list[dict], records: list[list[tuple[str, str]]]) -> None:
    """把每条记录的原始 MEDLINE 文本存进 paper['_medline']，供 PubMed 格式无损导出。"""
    for paper, props in zip(papers, records):
        paper["_medline"] = "\n".join(f"{k:<4}- {v}" for k, v in props)


# ---------------------------------------------------------------------------
# PubMed XML 解析器（EFetch retmode=xml）
# ---------------------------------------------------------------------------


def _xml_text(el: ET.Element | None, default: str = "") -> str:
    if el is None:
        return default
    return (el.text or "").strip()


def _xml_find_text(el: ET.Element, path: str, default: str = "") -> str:
    child = el.find(path)
    return _xml_text(child, default)


def _xml_findall_text(el: ET.Element, path: str) -> list[str]:
    return [_xml_text(c) for c in el.findall(path) if _xml_text(c)]


def _xml_article_ids(article: ET.Element) -> dict[str, str]:
    """从 ArticleIdList / PubmedData 提取 DOI/PMC/PMID/arXiv/PII。"""
    ids: dict[str, str] = {}
    id_list = article.find("PubmedData/ArticleIdList")
    if id_list is not None:
        for aid in id_list.findall("ArticleId"):
            id_type = (aid.get("IdType") or "").lower()
            val = _xml_text(aid)
            if id_type == "doi":
                ids.setdefault("doi", val)
            elif id_type in ("pmc", "pmcid"):
                ids.setdefault("pmc", val if val.upper().startswith("PMC") else f"PMC{val}")
            elif id_type == "arxiv":
                ids.setdefault("arxiv", val.removeprefix("arXiv:").strip())
            elif id_type == "pii":
                ids.setdefault("pii", val)
            elif id_type == "pubmed":
                ids.setdefault("pmid", val)
    return ids


def _xml_authors(article: ET.Element) -> list[str]:
    """优先返回完整姓名 'LastName ForeName'；否则 'CollectiveName' 或缩写。"""
    authors: list[str] = []
    auth_list = article.find("MedlineCitation/Article/AuthorList")
    if auth_list is None:
        return authors
    for author in auth_list.findall("Author"):
        last = _xml_text(author.find("LastName"))
        fore = _xml_text(author.find("ForeName"))
        collective = _xml_text(author.find("CollectiveName"))
        if collective:
            authors.append(collective)
        elif last and fore:
            authors.append(f"{last} {fore}")
        elif last:
            initials = _xml_text(author.find("Initials"))
            authors.append(f"{last} {initials}".strip())
    return authors


def _xml_abstract(article: ET.Element) -> str:
    """拼接 Abstract/AbstractText；带 Label 时保留结构。"""
    abstract_el = article.find("MedlineCitation/Article/Abstract")
    if abstract_el is None:
        return ""
    parts: list[str] = []
    for at in abstract_el.findall("AbstractText"):
        label = at.get("Label", "")
        text = _xml_text(at)
        if label:
            parts.append(f"{label}: {text}")
        else:
            parts.append(text)
    return "\n".join(parts)


def _xml_date(article: ET.Element) -> str:
    """优先用 ArticleDate，其次 JournalIssue/PubDate。"""
    # PubMed 推荐日期：PubMedPubDate/status=pubmed 或 ArticleDate
    pub_date = article.find("MedlineCitation/Article/ArticleDate")
    if pub_date is not None:
        y = _xml_text(pub_date.find("Year"))
        m = _xml_text(pub_date.find("Month"))
        d = _xml_text(pub_date.find("Day"))
        if y:
            out = y
            if m:
                out += f"-{_MONTHS[m[:3].lower()]:02d}" if m[:3].lower() in _MONTHS else f"-{m}"
            if d and m:
                out += f"-{int(d):02d}"
            return out

    pub_date = article.find("MedlineCitation/Article/Journal/JournalIssue/PubDate")
    if pub_date is None:
        return ""
    y = _xml_text(pub_date.find("Year"))
    m = _xml_text(pub_date.find("Month"))
    d = _xml_text(pub_date.find("Day"))
    medline_date = _xml_text(pub_date.find("MedlineDate"))
    if medline_date:
        return normalize_date(medline_date)
    if not y:
        return ""
    out = y
    if m:
        mk = m[:3].lower()
        if mk in _MONTHS:
            out += f"-{_MONTHS[mk]:02d}"
            if d:
                out += f"-{int(d):02d}"
        else:
            out += f"-{m}"
    return out


def parse_pubmed_xml(text: str) -> list[dict]:
    """解析 EFetch 返回的 PubMed XML（PubmedArticleSet），输出统一 paper dict 列表。"""
    root = ET.fromstring(text)
    papers: list[dict] = []
    for article in root.findall("PubmedArticle"):
        ids = _xml_article_ids(article)
        pmid = ids.get("pmid") or _xml_find_text(article, "MedlineCitation/PMID")
        article_el = article.find("MedlineCitation/Article")
        if article_el is None:
            continue

        mesh_terms: list[str] = []
        mh_list = article.find("MedlineCitation/MeshHeadingList")
        if mh_list is not None:
            for mh in mh_list.findall("MeshHeading"):
                desc = _xml_find_text(mh, "DescriptorName")
                qual = _xml_find_text(mh, "QualifierName")
                if desc:
                    mesh_terms.append(f"{desc} / {qual}" if qual else desc)

        keywords: list[str] = []
        kw_list = article.find("MedlineCitation/KeywordList")
        if kw_list is not None:
            keywords = _xml_findall_text(kw_list, "Keyword")

        pub_types: list[str] = _xml_findall_text(article_el, "PublicationTypeList/PublicationType")
        language = _xml_find_text(article_el, "Language")

        paper = {
            "pmid": pmid,
            "title": _xml_find_text(article_el, "ArticleTitle"),
            "authors": _xml_authors(article),
            "authors_abbr": _xml_authors(article),
            "date": _xml_date(article),
            "journal": _xml_find_text(article_el, "Journal/Title"),
            "journal_abbr": _xml_find_text(article_el, "Journal/ISOAbbreviation"),
            "abstract": _xml_abstract(article),
            "mesh_terms": mesh_terms,
            "keywords": keywords,
            "volume": _xml_find_text(article_el, "Journal/JournalIssue/Volume"),
            "issue": _xml_find_text(article_el, "Journal/JournalIssue/Issue"),
            "pages": _xml_find_text(article_el, "Pagination/MedlinePgn"),
            "pub_types": pub_types,
            "language": language,
            "doi": ids.get("doi", ""),
            "pmc": ids.get("pmc", ""),
            "arxiv": ids.get("arxiv", ""),
            "pii": ids.get("pii", ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        }
        papers.append(paper)
    return papers


# ---------------------------------------------------------------------------
# E-utilities
# ---------------------------------------------------------------------------


def esearch(args, query: str, retstart: int, retmax: int, usehistory: bool = True) -> dict:
    """调用 esearch.fcgi；返回 esearchresult（含 count/idlist，可选 webenv/querykey）。"""
    params: dict[str, str | None] = {
        "db": "pubmed",
        "term": query,
        "retmax": str(retmax),
        "retstart": str(retstart),
        "retmode": "json",
        "sort": args.sort,
    }
    if usehistory:
        params["usehistory"] = "y"
    if args.mindate:
        params["mindate"] = args.mindate
    if args.maxdate:
        params["maxdate"] = args.maxdate
    if getattr(args, "datetype", None):
        params["datetype"] = args.datetype
    params.update(_tool_params(args))
    url = f"{EUTILS}/esearch.fcgi?{_urlencode(params)}"
    data = json.loads(http_get(url).decode("utf-8"))
    return data.get("esearchresult", {})


def efetch_medline(args, pmids: list[str]) -> tuple[list[dict], list[list[tuple[str, str]]]]:
    params: dict[str, str | None] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "text",
        "rettype": "medline",
    }
    params.update(_tool_params(args))
    url = f"{EUTILS}/efetch.fcgi?{_urlencode(params)}"
    text = http_get(url, timeout=120).decode("utf-8", errors="replace")
    records = parse_medline(text)
    papers = [record_to_paper(r) for r in records]
    attach_raw_medline(papers, records)
    return papers, records


def efetch_xml(args, pmids: list[str]) -> list[dict]:
    params: dict[str, str | None] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    params.update(_tool_params(args))
    url = f"{EUTILS}/efetch.fcgi?{_urlencode(params)}"
    text = http_get(url, timeout=120).decode("utf-8", errors="replace")
    return parse_pubmed_xml(text)


def efetch_history(args, query_key: str, webenv: str, retstart: int, retmax: int) -> tuple[list[dict], list[list[tuple[str, str]]] | None]:
    """通过 ESearch 历史会话拉取；仅支持 XML 或 MEDLINE。"""
    retmode = getattr(args, "retmode", "medline")
    params: dict[str, str | None] = {
        "db": "pubmed",
        "query_key": query_key,
        "WebEnv": webenv,
        "retstart": str(retstart),
        "retmax": str(retmax),
    }
    if retmode == "xml":
        params["retmode"] = "xml"
    else:
        params["retmode"] = "text"
        params["rettype"] = "medline"
    params.update(_tool_params(args))
    url = f"{EUTILS}/efetch.fcgi?{_urlencode(params)}"
    text = http_get(url, timeout=120).decode("utf-8", errors="replace")
    if retmode == "xml":
        return parse_pubmed_xml(text), None
    records = parse_medline(text)
    papers = [record_to_paper(r) for r in records]
    attach_raw_medline(papers, records)
    return papers, records


# ---------------------------------------------------------------------------
# Search / Fetch / MeSH 命令
# ---------------------------------------------------------------------------


def cmd_search(args) -> int:
    global _LIMITER
    _LIMITER = RateLimiter(args.api_key)
    if getattr(args, "query_file", None):
        with open(args.query_file, "r", encoding="utf-8") as f:
            args.query = f.read().strip()

    query = args.query
    retmax = args.retmax  # ESearch 分页页大小
    limit = args.limit    # 用户希望拉取总量，0=全部
    usehistory = getattr(args, "usehistory", True)

    # 1) ESearch 分页收集 UID
    idlist: list[str] = []
    total = 0
    webenv = ""
    query_key = ""
    retstart = 0
    while True:
        # 若用户设了 limit，控制 ESearch 返回总量
        remaining = None
        if limit > 0:
            remaining = limit - len(idlist)
            if remaining <= 0:
                break
            if retmax > remaining:
                retmax = remaining

        res = esearch(args, query, retstart, retmax, usehistory=usehistory)
        total = int(res.get("count", "0"))
        ids = res.get("idlist", [])
        returned = int(res.get("retmax", len(ids)))
        if not webenv and res.get("webenv"):
            webenv = res["webenv"]
        if not query_key and res.get("querykey"):
            query_key = res["querykey"]

        idlist.extend(ids)
        if limit > 0 and len(idlist) >= limit:
            idlist = idlist[:limit]
            break
        if returned < retmax or not ids:
            break
        retstart += returned

    total_out = min(total, limit) if limit > 0 else total
    print(f"[esearch] 命中 {total} 条，计划拉取 {len(idlist)} 条", file=sys.stderr)

    # 2) EFetch 拉取元数据
    papers: list[dict] = []
    retmode = getattr(args, "retmode", "medline")

    if usehistory and webenv and query_key:
        # 通过历史会话批量拉取，避免 URL 过长
        batch = args.batch
        for start in range(0, len(idlist), batch):
            try:
                chunk_papers, _ = efetch_history(args, query_key, webenv, start, batch)
                papers.extend(chunk_papers)
                print(f"[efetch] {min(start + batch, len(idlist))}/{len(idlist)}", file=sys.stderr)
            except Exception as e:
                print(f"[efetch] 批次 {start}-{min(start + batch, len(idlist))} 失败，已跳过: {e}", file=sys.stderr)
    else:
        # 退化为 ID 列表模式（旧行为，兼容无 usehistory 的场景）
        efetch_fn = efetch_xml if retmode == "xml" else efetch_medline
        for i in range(0, len(idlist), args.batch):
            chunk = idlist[i : i + args.batch]
            try:
                chunk_papers, _ = efetch_fn(args, chunk)
                papers.extend(chunk_papers)
                print(f"[efetch] {min(i + args.batch, len(idlist))}/{len(idlist)}", file=sys.stderr)
            except Exception as e:
                print(f"[efetch] 批次 {i}-{i + len(chunk)} 失败，已跳过: {e}", file=sys.stderr)

    result = {
        "source": "pubmed",
        "query": query,
        "mindate": args.mindate or "",
        "maxdate": args.maxdate or "",
        "datetype": getattr(args, "datetype", "pdat") or "pdat",
        "search_date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": total_out,
        "fetched": len(papers),
        "papers": papers,
    }
    _dump(result, args.out)
    return 0


def cmd_fetch(args) -> int:
    global _LIMITER
    _LIMITER = RateLimiter(args.api_key)
    pmids = [p.strip() for p in args.ids.split(",") if p.strip()]
    papers: list[dict] = []
    retmode = getattr(args, "retmode", "medline")
    efetch_fn = efetch_xml if retmode == "xml" else efetch_medline
    for i in range(0, len(pmids), args.batch):
        chunk = pmids[i : i + args.batch]
        chunk_papers, _ = efetch_fn(args, chunk)
        papers.extend(chunk_papers)
        print(f"[efetch] {min(i + args.batch, len(pmids))}/{len(pmids)}", file=sys.stderr)
    result = {
        "source": "pubmed",
        "query": f"ids:{args.ids}",
        "search_date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(papers),
        "fetched": len(papers),
        "papers": papers,
    }
    _dump(result, args.out)
    return 0


# ---------------------------------------------------------------------------
# MeSH 校验（NLM MeSH RDF lookup API，无需 key）
# ---------------------------------------------------------------------------


def _mesh_descriptor_label(descriptor_id: str) -> str:
    url = f"https://id.nlm.nih.gov/mesh/{descriptor_id}.json"
    try:
        data = json.loads(http_get(url).decode("utf-8"))
        label = data.get("label", {})
        if isinstance(label, dict):
            return label.get("@value", "")
        return str(label)
    except Exception:
        return ""


def mesh_lookup(term: str, match: str) -> list[str]:
    url = f"{MESH_LOOKUP}?{_urlencode({'label': term, 'match': match, 'limit': '10'})}"
    data = json.loads(http_get(url).decode("utf-8"))
    out = []
    for uri in data if isinstance(data, list) else []:
        m = re.search(r"/mesh/(D\d+|C\d+)", str(uri))
        if m:
            out.append(m.group(1))
    return out


def cmd_mesh(args) -> int:
    global _LIMITER
    _LIMITER = RateLimiter(args.api_key)
    terms = list(args.terms or [])
    if getattr(args, "terms_file", None):
        with open(args.terms_file, "r", encoding="utf-8") as f:
            terms.extend(ln.strip() for ln in f if ln.strip())
    if not terms:
        print("[mesh] 未提供任何术语", file=sys.stderr)
        return 1
    results = []
    for term in terms:
        entry: dict = {"term": term, "status": "not_found", "descriptor_id": "", "label": "", "suggestions": []}
        try:
            hits = mesh_lookup(term, "exact")
            if hits:
                entry["status"] = "exact"
                entry["descriptor_id"] = hits[0]
                entry["label"] = _mesh_descriptor_label(hits[0])
            else:
                sugg = mesh_lookup(term, "contains")[:5]
                if sugg:
                    entry["status"] = "suggest"
                    entry["suggestions"] = [
                        {"descriptor_id": s, "label": _mesh_descriptor_label(s)} for s in sugg
                    ]
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
        results.append(entry)
        print(f"[mesh] {term}: {entry['status']}", file=sys.stderr)
    _dump({"mesh_check": results}, args.out)
    return 0


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
    ap = argparse.ArgumentParser(description="PubMed 检索 / 拉取 / MeSH 校验")
    ap.add_argument("--api-key", default=None, help="NCBI API key（或设环境变量 NCBI_API_KEY）")
    ap.add_argument("--email", default=None, help="联系邮箱（NCBI 建议提供）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="检索 PubMed 并拉取元数据")
    q = s.add_mutually_exclusive_group(required=True)
    q.add_argument("--query", help="PubMed 检索式（英文）")
    q.add_argument("--query-file", help="从 UTF-8 文本文件读取检索式（避免 shell 引号问题）")
    s.add_argument("--mindate", default="", help="起始日期 YYYY/MM/DD 或 YYYY")
    s.add_argument("--maxdate", default="", help="截止日期 YYYY/MM/DD 或 YYYY")
    s.add_argument(
        "--datetype",
        default="pdat",
        choices=["pdat", "edat", "mdat"],
        help="日期类型：pdat=发表日期，edat=Entrez 录入日期，mdat=MeSH 日期（默认 pdat）",
    )
    s.add_argument(
        "--retmax",
        type=int,
        default=200,
        help="ESearch 每批返回 UID 数（分页页大小，默认 200，最大 10000）",
    )
    s.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多拉取文献篇数；0=全部（默认）",
    )
    s.add_argument(
        "--batch",
        type=int,
        default=200,
        help=f"EFetch 每批篇数（<={MAX_EFETCH_BATCH}，默认 200）",
    )
    s.add_argument(
        "--retmode",
        default="medline",
        choices=["medline", "xml"],
        help="EFetch 返回格式：medline 文本（默认）或 PubMed XML",
    )
    s.add_argument(
        "--usehistory",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="使用 ESearch 历史服务器拉取记录（默认开启）",
    )
    s.add_argument("--sort", default="relevance", choices=["relevance", "pub_date", ""], help="排序")
    s.add_argument("--out", required=True, help="输出 JSON 路径，'-' 输出到 stdout")
    s.set_defaults(func=cmd_search)

    f = sub.add_parser("fetch", help="按 PMID 列表拉取元数据")
    f.add_argument("--ids", required=True, help="逗号分隔的 PMID")
    f.add_argument("--batch", type=int, default=200)
    f.add_argument(
        "--retmode",
        default="medline",
        choices=["medline", "xml"],
        help="EFetch 返回格式：medline 文本（默认）或 PubMed XML",
    )
    f.add_argument("--out", required=True)
    f.set_defaults(func=cmd_fetch)

    m = sub.add_parser("mesh", help="校验术语是否为 MeSH 主题词")
    m.add_argument("terms", nargs="*", help="待校验术语（英文，含空格的词加引号）")
    m.add_argument("--terms-file", default=None, help="从 UTF-8 文件读取术语（每行一个）")
    m.add_argument("--out", default="-", help="输出 JSON 路径，默认 stdout")
    m.set_defaults(func=cmd_mesh)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
