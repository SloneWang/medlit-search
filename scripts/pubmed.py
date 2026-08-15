#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pubmed.py — PubMed (NCBI E-utilities) 检索 / 拉取 / MeSH 校验工具

流程移植自 SciSearch 桌面文献检索工具（同一作者）：
  EUtilities.SearchAsync  -> esearch.fcgi (retmode=json, retstart/retmax 分页)
  EUtilities.FetchAsync   -> efetch.fcgi  (rettype=medline, retmode=text)
  Medline.fs              -> MEDLINE 文本解析（空行分隔记录，"KEY- value" 定长列，续行拼接）
  NCBIPaperFactory        -> 字段提取；此处修正两处：
      1) 发表日期用 DP（实际发表日期），SciSearch 误用 MHDA（MeSH 标引日期）
      2) F# 解析器 List.pairwise 会丢失每条记录最后一个属性，此处修复

限流：无 API key 3 req/s，有 key 10 req/s（NCBI 官方限制）。
仅依赖 Python 标准库。

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

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MESH_LOOKUP = "https://id.nlm.nih.gov/mesh/lookup/descriptor"
USER_AGENT = "workbuddy-medlit-skill/1.0 (PubMed literature screening)"

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


def _qs(params: dict[str, str | None]) -> str:
    items = []
    for k, v in params.items():
        if v is None:
            continue
        items.append(f"{k}={urllib.parse.quote(v, safe='(),:\\\"/[]:+ ')}")
    # 空格按 SciSearch 的 GetTermString 习惯编码为 +
    return "&".join(items).replace(" ", "+")


def _tool_params(args) -> dict[str, str | None]:
    p: dict[str, str | None] = {"tool": "workbuddy_medlit"}
    if getattr(args, "email", None):
        p["email"] = args.email
    if getattr(args, "api_key", None) or os.environ.get("NCBI_API_KEY"):
        p["api_key"] = getattr(args, "api_key", None) or os.environ.get("NCBI_API_KEY")
    return p


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
# E-utilities
# ---------------------------------------------------------------------------


def esearch(args, query: str, retstart: int, retmax: int) -> dict:
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": str(retmax),
        "retstart": str(retstart),
        "retmode": "json",
        "sort": args.sort,
    }
    if args.mindate or args.maxdate:
        params["datetype"] = "pdat"
        if args.mindate:
            params["mindate"] = args.mindate
        if args.maxdate:
            params["maxdate"] = args.maxdate
    params.update(_tool_params(args))
    url = f"{EUTILS}/esearch.fcgi?{_qs(params)}"
    data = json.loads(http_get(url).decode("utf-8"))
    return data.get("esearchresult", {})


def efetch_medline(args, pmids: list[str]) -> tuple[list[dict], list[list[tuple[str, str]]]]:
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "text",
        "rettype": "medline",
    }
    params.update(_tool_params(args))
    url = f"{EUTILS}/efetch.fcgi?{_qs(params)}"
    text = http_get(url, timeout=120).decode("utf-8", errors="replace")
    records = parse_medline(text)
    papers = [record_to_paper(r) for r in records]
    attach_raw_medline(papers, records)
    return papers, records


def cmd_search(args) -> int:
    global _LIMITER
    _LIMITER = RateLimiter(args.api_key)
    if getattr(args, "query_file", None):
        with open(args.query_file, "r", encoding="utf-8") as f:
            args.query = f.read().strip()

    # 1) esearch 分页收集 PMID（单次最多 10000）
    want = args.retmax
    idlist: list[str] = []
    total = 0
    while True:
        batch = min(10000, want - len(idlist)) if want > 0 else 10000
        if batch <= 0:
            break
        res = esearch(args, args.query, len(idlist), batch)
        total = int(res.get("count", "0"))
        ids = res.get("idlist", [])
        idlist.extend(ids)
        if len(ids) < batch or (want > 0 and len(idlist) >= want):
            break
    if want > 0:
        idlist = idlist[:want]
        total_out = min(total, want)
    else:
        total_out = total
    print(f"[esearch] 命中 {total} 条，计划拉取 {len(idlist)} 条", file=sys.stderr)

    # 2) efetch 分批拉取 MEDLINE（每批 200 条）
    papers: list[dict] = []
    b = args.batch
    for i in range(0, len(idlist), b):
        chunk = idlist[i : i + b]
        try:
            chunk_papers, _ = efetch_medline(args, chunk)
            papers.extend(chunk_papers)
            print(f"[efetch] {min(i + b, len(idlist))}/{len(idlist)}", file=sys.stderr)
        except Exception as e:  # SciSearch 同款容错：单批失败跳过不中断
            print(f"[efetch] 批次 {i}-{i + len(chunk)} 失败，已跳过: {e}", file=sys.stderr)

    result = {
        "source": "pubmed",
        "query": args.query,
        "mindate": args.mindate or "",
        "maxdate": args.maxdate or "",
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
    for i in range(0, len(pmids), args.batch):
        chunk = pmids[i : i + args.batch]
        chunk_papers, _ = efetch_medline(args, chunk)
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
    url = f"{MESH_LOOKUP}?{_qs({'label': term, 'match': match, 'limit': '10'})}"
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
    s.add_argument("--mindate", default="", help="起始日期 YYYY/MM/DD 或 YYYY（datetype=pdat）")
    s.add_argument("--maxdate", default="", help="截止日期 YYYY/MM/DD 或 YYYY")
    s.add_argument("--retmax", type=int, default=100, help="最大拉取篇数；0=全部")
    s.add_argument("--batch", type=int, default=200, help="efetch 每批篇数（<=500）")
    s.add_argument("--sort", default="relevance", choices=["relevance", "pub_date", ""], help="排序")
    s.add_argument("--out", required=True, help="输出 JSON 路径，'-' 输出到 stdout")
    s.set_defaults(func=cmd_search)

    f = sub.add_parser("fetch", help="按 PMID 列表拉取元数据")
    f.add_argument("--ids", required=True, help="逗号分隔的 PMID")
    f.add_argument("--batch", type=int, default=200)
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
