#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pubmed.py — PubMed (NCBI E-utilities) 检索 / 拉取 / MeSH 校验工具

流程移植自 SciSearch 桌面文献检索工具（同一作者）：
  EUtilities.SearchAsync  -> esearch.fcgi (retmode=json)
  EUtilities.FetchAsync   -> efetch.fcgi  (rettype=medline, retmode=text 或 xml)
  Medline.fs              -> MEDLINE 文本解析（空行分隔记录，"KEY- value" 定长列，续行拼接）
  NCBIPaperFactory        -> 字段提取；此处修正两处：
      1) 发表日期用 DP（实际发表日期），SciSearch 误用 MHDA（MeSH 标引日期）
      2) F# 解析器 List.pairwise 会丢失每条记录最后一个属性，此处修复

IO 模型（v1.6 起协程化）：
  - search / fetch 子命令基于 asyncio：AsyncRateLimiter 保证请求起步间隔
    （无 key 3 req/s，有 key 10 req/s），asyncio.Semaphore 限制在途请求数
    （无 key 3 / 有 key 10），阻塞式 urllib 调用经 asyncio.to_thread 包装，
    不引入任何第三方依赖；
  - EFetch 各批次作为 asyncio.Task 并发拉取并就地解析（CPU 轻量），
    最后统一按发表日期降序重排，与 v1.5 线程版行为一致；
  - mesh 子命令调用量小，保持同步实现。

限流：无 API key 3 req/s，有 key 10 req/s（NCBI 官方限制）。
仅依赖 Python 标准库。

参数语义（与 NCBI 文档一致）：
  --limit   : 用户希望拉取的文献总量（0=全部，即无限）。未设日期范围时须向用户确认。
  --batch   : EFetch 单批拉取的篇数（<=200，建议 100-200）
  --retmode : EFetch 返回模式，medline 或 xml（默认 medline）
  --mindate/--maxdate/--datetype : 日期过滤；仅给 mindate 时 maxdate 默认今天
  --sort    : ESearch 排序方式，默认 pub_date（按发表日期降序，最新在前）。
              可选 pub_date / relevance / most_recent / journal / title；
              usehistory 模式下排序会被保留，EFetch 按该顺序取回（NCBI 官方语义），
              拉取完成后脚本再按解析出的 date 做一次客户端降序，双保险保证最新在前。
  ESearch 分页页大小固定 retmax=20（内部常量 ESearch_PAGE_SIZE，v1.6 起不对用户暴露）。

检索式编码约定（与 Biopython 的 Bio.Entrez 处理方式一致）：
  - 空白（空格/制表符/换行）在检索前先归一化为单个空格，再由 urllib.parse.urlencode
    编码为 '+'（quote_plus 默认行为）。换行绝不编码成 %0A，%2B 只用于字面加号。
  - | [ ] " 等非 ASCII 字符按百分号编码（%7C %5B %5D %22 等），与 Biopython 相同，
    符合 NCBI "special characters must be URL encoded" 的要求，无需特殊处理。
  - 超长检索式：参数编码后超过 POST_CHAR_THRESHOLD（500 字符）时自动改用 HTTP POST
    （NCBI 建议 "For very long queries ... consider using an HTTP POST call"），见 http_post()。

已知 API 限制（NCBI E-utilities In-Depth）：
  - PubMed 的 ESearch 单次查询最多返回前 10000 条匹配记录；
    命中超过 10000 时需用 EDirect 或按日期分段查询。

子命令：
  search  检索并拉取全文级元数据，输出统一 JSON
  fetch   按 PMID 列表重新拉取（复筛/刷新用）
  mesh    校验术语是否为 MeSH 主题词，并给出官方建议词
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

# NCBI E-utilities 各工具的统一入口前缀
EUTILS: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# NLM MeSH RDF lookup API（校验术语用，无需 API key）
MESH_LOOKUP: str = "https://id.nlm.nih.gov/mesh/lookup/descriptor"
# User-Agent：NCBI 要求客户端标识自己，便于其追踪滥用来源
USER_AGENT: str = "workbuddy-medlit-skill/1.2 (PubMed literature screening)"

# NCBI 建议 EFetch 单批不超过 200 个 UID（URL 过长会失败）
MAX_EFETCH_BATCH: int = 200

# ESearch 分页页大小固定 20（内部参数，v1.6 起不再暴露 --retmax，也不询问用户）
ESearch_PAGE_SIZE: int = 20

# 检索式参数编码后超过该字符数即改用 HTTP POST（NCBI：超长查询建议 POST）
POST_CHAR_THRESHOLD: int = 500


def _max_in_flight(api_key: str | None) -> int:
    """按是否有 API key 返回在途请求并发上限（与 NCBI 每秒请求配额一致）。

    参数:
        api_key: NCBI API key；为空按 3 req/s 处理。

    返回:
        并发上限：有 key 10，无 key 3。
    """
    return 10 if (api_key or os.environ.get("NCBI_API_KEY")) else 3


# ---------------------------------------------------------------------------
# 限流器（移植 EUtilities.ResetTimer：全局串行计时，每次请求至少间隔 Interval）
# ---------------------------------------------------------------------------


class RateLimiter:
    """同步版限流器（供 mesh 等低频同步路径使用）。

    移植自 SciSearch 的 EUtilities.ResetTimer：任意时刻只允许一个"请求间最小间隔"，
    保证对 NCBI 的访问频率不超过官方上限（无 key 3 req/s，有 key 10 req/s）。

    属性:
        api_key: NCBI API key；为空则按 3 req/s 限流。
        interval: 相邻两次请求的最小间隔（秒）。
        _next: 下一次允许发起请求的单调时钟时间点。
    """

    def __init__(self, api_key: str | None) -> None:
        """初始化限流器并按是否有 API key 设定速率。

        参数:
            api_key: NCBI API key；None 或空串表示未配置，使用 3 req/s。
        """
        self.api_key: str | None = api_key or os.environ.get("NCBI_API_KEY")
        self.interval: float = 1.0 / (10.0 if self.api_key else 3.0)
        self._next: float = 0.0

    def wait(self) -> None:
        """阻塞到下一个允许请求的时间点，并推进内部时钟。

        参数:
            无。

        返回:
            None（可能 sleep）。

        异常:
            无显式抛出。
        """
        now: float = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
        self._next = time.monotonic() + self.interval


class AsyncRateLimiter:
    """协程版限流器（search / fetch 主路径使用）。

    与 RateLimiter 同一语义（起步间隔 >= interval），但用 asyncio.sleep 让出事件循环：
    检查与推进 _next 之间没有 await 点，单事件循环内原子执行，无竞态。

    属性:
        interval: 相邻两次请求起步的最小间隔（秒）。
        _next: 下一次允许起步请求的单调时钟时间点。
    """

    def __init__(self, api_key: str | None) -> None:
        """初始化协程限流器。

        参数:
            api_key: NCBI API key；为空按 3 req/s 计。
        """
        key: str | None = api_key or os.environ.get("NCBI_API_KEY")
        self.interval: float = 1.0 / (10.0 if key else 3.0)
        self._next: float = 0.0

    async def acquire(self) -> None:
        """异步等待到下一个允许起步请求的时间点，并推进内部时钟。

        参数:
            无。

        返回:
            None（可能 await asyncio.sleep）。

        异常:
            无显式抛出。
        """
        now: float = time.monotonic()
        if now < self._next:
            await asyncio.sleep(self._next - now)
        self._next = time.monotonic() + self.interval


# 模块级懒加载单例：供同步 http_get 默认限流（mesh 路径）
_LIMITER: RateLimiter | None = None


def _limiter() -> RateLimiter:
    """返回模块级同步 RateLimiter 单例（懒创建）。"""
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = RateLimiter(None)
    return _LIMITER


def http_get(url: str, timeout: int = 60, retries: int = 4, use_limiter: bool = True) -> bytes:
    """带限流 + 指数退避重试的 GET。

    参数:
        url: 完整请求 URL。
        timeout: 单次请求超时（秒）。
        retries: 最大尝试次数；429 或 5xx 以及网络层错误会退避重试。
        use_limiter: 是否走模块级同步限流；协程路径已用 AsyncRateLimiter
            控制起步节奏，应传 False 避免双重等待。

    返回:
        响应体的原始字节。

    异常:
        RuntimeError: 重试耗尽仍失败。
        urllib.error.HTTPError: 非 429/5xx 的 HTTP 错误直接抛出。
    """
    last_err: Exception | None = None
    for attempt in range(retries):
        if use_limiter:
            _limiter().wait()
        req: urllib.request.Request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT}
        )
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


def http_post(url_base: str, params: dict[str, str | None], timeout: int = 60, retries: int = 4) -> bytes:
    """带指数退避重试的 POST（参数放请求体，用于超长检索式；限流由调用方负责）。

    NCBI E-utilities 文档：超长查询（编码后数百字符以上）应改用 HTTP POST，
    避免 URL 过长被代理或服务端截断。参数编码规则与 GET 完全一致（见模块 docstring）。

    参数:
        url_base: 工具入口地址（如 .../esearch.fcgi），不带 query string。
        params: 参数字典；None / 空串值会被丢弃。
        timeout: 单次请求超时（秒）。
        retries: 最大尝试次数；429 或 5xx 以及网络层错误会退避重试。

    返回:
        响应体的原始字节。

    异常:
        RuntimeError: 重试耗尽仍失败。
        urllib.error.HTTPError: 非 429/5xx 的 HTTP 错误直接抛出。
    """
    body: bytes = _urlencode(params).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(retries):
        req: urllib.request.Request = urllib.request.Request(
            url_base,
            data=body,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
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
    raise RuntimeError(f"POST 请求失败（重试 {retries} 次后仍失败）: {url_base} -> {last_err}")


def _tool_params(args: argparse.Namespace) -> dict[str, str]:
    """组装 NCBI 建议附加到每个请求上的 tool/email/api_key 参数。

    参数:
        args: 命令行命名空间；读取 email、api_key 属性（可不存在）。

    返回:
        非空的 tool/email/api_key 参数字典。
    """
    p: dict[str, str] = {"tool": "workbuddy_medlit"}
    if getattr(args, "email", None):
        p["email"] = args.email
    api_key: str | None = getattr(args, "api_key", None) or os.environ.get("NCBI_API_KEY")
    if api_key:
        p["api_key"] = api_key
    return p


def _urlencode(params: dict[str, str | None]) -> str:
    """使用 urllib.parse.urlencode，仅保留非空值，空格编码为 +。"""
    clean: dict[str, str | None] = {
        k: v for k, v in params.items() if v is not None and v != ""
    }
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

    参数:
        text: EFetch rettype=medline 返回的完整文本。

    返回:
        记录列表；每条记录是按出现顺序排列的 (KEY, value) 元组列表。
    """
    # 第一步：把原始行归一化为 (key|None, value) 的"逻辑行"序列；
    # None 表示记录分隔（空行），有 key 表示新属性开始，key 为 None 表示续行
    norm: list[tuple[str | None, str] | None] = []
    for raw in text.splitlines():
        line: str = raw.rstrip("\r\n")
        if not line.strip() or len(line) < 6:
            norm.append(None)
        elif line[4] == "-":
            norm.append((line[0:4].rstrip(), line[6:].rstrip()))
        else:
            norm.append((None, line[6:].rstrip() if len(line) > 6 else ""))

    records: list[list[tuple[str, str]]] = []
    current: list[tuple[str | None, str]] = []

    def flush(buf: list[tuple[str | None, str]]) -> None:
        """把缓冲中的一条记录内的逻辑行折叠成 [(KEY, value), ...]。

        核心点：续行的 value 用空格拼到当前 KEY 上；记录结尾必须显式 flush
        最后一个属性——F# 版用 List.pairwise 恰好会漏掉它，此处修复。
        """
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


# MEDLINE 的三字符月份缩写 -> 数字月
_MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# AID/LID 字段后缀标签的解析，如 "10.1000/xyz [doi]"
_AID_RE: re.Pattern[str] = re.compile(
    r"^(?P<value>.+?)\s*\[(?P<tag>[A-Za-z -]+)\]\s*$"
)


def _first(props: list[tuple[str, str]], key: str) -> str:
    """取一条记录中某 KEY 的第一个值；不存在返回空串。"""
    for k, v in props:
        if k == key:
            return v
    return ""


def _all(props: list[tuple[str, str]], key: str) -> list[str]:
    """取一条记录中某 KEY 的全部值（可重复字段，如 FAU/AU/MH）。"""
    return [v for k, v in props if k == key]


def normalize_date(dp: str) -> str:
    """DP 字段 -> 'YYYY-MM-DD' / 'YYYY-MM' / 'YYYY'。

    例: '2023 May 15'、'2023 May-Jun'。

    参数:
        dp: MEDLINE DP 字段原文。

    返回:
        尽量规整化的日期串；无法识别时原样返回。字典序与时间序一致，
        可直接用于按日期排序。
    """
    dp = dp.strip()
    m: re.Match[str] | None = re.match(
        r"^(\d{4})(?:\s+([A-Za-z]{3,9}))?(?:\s+(\d{1,2}))?", dp
    )
    if not m:
        return dp
    year: str = m.group(1)
    mon: str | None = m.group(2)
    day: str | None = m.group(3)
    out: str = year
    if mon:
        mk: str = mon[:3].lower()
        if mk in _MONTHS:
            out += f"-{_MONTHS[mk]:02d}"
            if day:
                out += f"-{int(day):02d}"
    return out


def record_to_paper(props: list[tuple[str, str]]) -> dict[str, Any]:
    """NCBIPaperFactory.FromMedline 的扩展移植版。

    参数:
        props: 单条 MEDLINE 记录的 (KEY, value) 列表。

    返回:
        skill 统一 paper 字典（字段与 PubMed XML 解析输出一致）。
    """
    # AID/LID 里可能同时挂着 doi、pmc、arxiv、pii 等多种文献号，逐个拆解
    ids: dict[str, str] = {}
    for k in ("AID", "LID"):
        for v in _all(props, k):
            m: re.Match[str] | None = _AID_RE.match(v)
            if not m:
                continue
            tag: str = m.group("tag").strip().lower()
            val: str = m.group("value").strip()
            if tag == "doi":
                ids.setdefault("doi", val)
            elif tag in ("pmc", "pmcid"):
                ids.setdefault("pmc", val if val.upper().startswith("PMC") else f"PMC{val}")
            elif tag == "arxiv":
                ids.setdefault("arxiv", val.removeprefix("arXiv:").strip())
            elif tag == "pii":
                ids.setdefault("pii", val)
    pmc_field: str = _first(props, "PMC")
    if pmc_field and "pmc" not in ids:
        ids["pmc"] = pmc_field if pmc_field.upper().startswith("PMC") else f"PMC{pmc_field}"

    pmid: str = _first(props, "PMID")
    paper: dict[str, Any] = {
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


def attach_raw_medline(
    papers: list[dict[str, Any]], records: list[list[tuple[str, str]]]
) -> None:
    """把每条记录的原始 MEDLINE 文本存进 paper['_medline']，供 PubMed 格式无损导出。

    参数:
        papers: 解析出的 paper 列表。
        records: 与之平行对齐的 MEDLINE 记录列表。

    返回:
        None（原地修改 papers）。
    """
    for paper, props in zip(papers, records):
        paper["_medline"] = "\n".join(f"{k:<4}- {v}" for k, v in props)


def parse_chunk_text(text: str, mode: str) -> list[dict[str, Any]]:
    """把单批 EFetch 响应文本解析为 paper 列表（CPU 轻量，可在事件循环内直接跑）。

    参数:
        text: 单批 EFetch 响应体。
        mode: "xml" 走 PubMed XML 解析；其余按 MEDLINE 解析并回带原始文本。

    返回:
        该批解析出的 paper 列表。
    """
    if mode == "xml":
        return parse_pubmed_xml(text)
    records: list[list[tuple[str, str]]] = parse_medline(text)
    papers: list[dict[str, Any]] = [record_to_paper(r) for r in records]
    attach_raw_medline(papers, records)
    return papers


# ---------------------------------------------------------------------------
# PubMed XML 解析器（EFetch retmode=xml）
# ---------------------------------------------------------------------------


def _xml_text(el: ET.Element | None, default: str = "") -> str:
    """取 XML 元素文本（strip 后）；元素为空返回默认值。"""
    if el is None:
        return default
    return (el.text or "").strip()


def _xml_find_text(el: ET.Element, path: str, default: str = "") -> str:
    """find + _xml_text 的便捷组合。"""
    child: ET.Element | None = el.find(path)
    return _xml_text(child, default)


def _xml_findall_text(el: ET.Element, path: str) -> list[str]:
    """findall 后逐元素取文本，并过滤空串。"""
    return [_xml_text(c) for c in el.findall(path) if _xml_text(c)]


def _xml_article_ids(article: ET.Element) -> dict[str, str]:
    """从 ArticleIdList / PubmedData 提取 DOI/PMC/PMID/arXiv/PII。"""
    ids: dict[str, str] = {}
    id_list: ET.Element | None = article.find("PubmedData/ArticleIdList")
    if id_list is not None:
        aid: ET.Element
        for aid in id_list.findall("ArticleId"):
            id_type: str = (aid.get("IdType") or "").lower()
            val: str = _xml_text(aid)
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
    auth_list: ET.Element | None = article.find("MedlineCitation/Article/AuthorList")
    if auth_list is None:
        return authors
    author: ET.Element
    for author in auth_list.findall("Author"):
        last: str = _xml_text(author.find("LastName"))
        fore: str = _xml_text(author.find("ForeName"))
        collective: str = _xml_text(author.find("CollectiveName"))
        if collective:
            authors.append(collective)
        elif last and fore:
            authors.append(f"{last} {fore}")
        elif last:
            initials: str = _xml_text(author.find("Initials"))
            authors.append(f"{last} {initials}".strip())
    return authors


def _xml_abstract(article: ET.Element) -> str:
    """拼接 Abstract/AbstractText；带 Label 时保留结构。"""
    abstract_el: ET.Element | None = article.find("MedlineCitation/Article/Abstract")
    if abstract_el is None:
        return ""
    parts: list[str] = []
    at: ET.Element
    for at in abstract_el.findall("AbstractText"):
        label: str = at.get("Label", "")
        text: str = _xml_text(at)
        if label:
            parts.append(f"{label}: {text}")
        else:
            parts.append(text)
    return "\n".join(parts)


def _xml_date(article: ET.Element) -> str:
    """优先用 ArticleDate，其次 JournalIssue/PubDate。"""
    pub_date: ET.Element | None = article.find("MedlineCitation/Article/ArticleDate")
    if pub_date is not None:
        y: str = _xml_text(pub_date.find("Year"))
        m: str = _xml_text(pub_date.find("Month"))
        d: str = _xml_text(pub_date.find("Day"))
        if y:
            out: str = y
            if m:
                out += (
                    f"-{_MONTHS[m[:3].lower()]:02d}"
                    if m[:3].lower() in _MONTHS
                    else f"-{m}"
                )
            if d and m:
                out += f"-{int(d):02d}"
            return out

    pub_date = article.find("MedlineCitation/Article/Journal/JournalIssue/PubDate")
    if pub_date is None:
        return ""
    y = _xml_text(pub_date.find("Year"))
    m = _xml_text(pub_date.find("Month"))
    d = _xml_text(pub_date.find("Day"))
    medline_date: str = _xml_text(pub_date.find("MedlineDate"))
    if medline_date:
        return normalize_date(medline_date)
    if not y:
        return ""
    out = y
    if m:
        mk: str = m[:3].lower()
        if mk in _MONTHS:
            out += f"-{_MONTHS[mk]:02d}"
            if d:
                out += f"-{int(d):02d}"
        else:
            out += f"-{m}"
    return out


def parse_pubmed_xml(text: str) -> list[dict[str, Any]]:
    """解析 EFetch 返回的 PubMed XML（PubmedArticleSet），输出统一 paper dict 列表。

    参数:
        text: EFetch retmode=xml 返回的 XML 文本。

    返回:
        统一 paper 字典列表，字段与 record_to_paper 输出一致。
    """
    root: ET.Element = ET.fromstring(text)
    papers: list[dict[str, Any]] = []
    article: ET.Element
    for article in root.findall("PubmedArticle"):
        ids: dict[str, str] = _xml_article_ids(article)
        pmid: str = ids.get("pmid") or _xml_find_text(article, "MedlineCitation/PMID")
        article_el: ET.Element | None = article.find("MedlineCitation/Article")
        if article_el is None:
            continue

        mesh_terms: list[str] = []
        mh_list: ET.Element | None = article.find("MedlineCitation/MeshHeadingList")
        if mh_list is not None:
            mh: ET.Element
            for mh in mh_list.findall("MeshHeading"):
                desc: str = _xml_find_text(mh, "DescriptorName")
                qual: str = _xml_find_text(mh, "QualifierName")
                if desc:
                    mesh_terms.append(f"{desc} / {qual}" if qual else desc)

        keywords: list[str] = []
        kw_list: ET.Element | None = article.find("MedlineCitation/KeywordList")
        if kw_list is not None:
            keywords = _xml_findall_text(kw_list, "Keyword")

        pub_types: list[str] = _xml_findall_text(
            article_el, "PublicationTypeList/PublicationType"
        )
        language: str = _xml_find_text(article_el, "Language")

        paper: dict[str, Any] = {
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
# E-utilities（协程封装：限流 acquire + to_thread 包装阻塞 HTTP）
# ---------------------------------------------------------------------------


async def _ahttp_get(url: str, limiter: AsyncRateLimiter, timeout: int = 60, retries: int = 4) -> bytes:
    """协程版 GET：先按限流器节奏排队，再在线程池执行阻塞请求。

    参数:
        url: 完整请求 URL。
        limiter: 协程限流器（控制请求起步间隔）。
        timeout: 单次请求超时（秒）。
        retries: 重试次数（透传 http_get）。

    返回:
        响应体字节。
    """
    await limiter.acquire()
    return await asyncio.to_thread(http_get, url, timeout, retries, False)


async def esearch(
    args: argparse.Namespace,
    query: str,
    retstart: int,
    retmax: int,
    usehistory: bool,
    limiter: AsyncRateLimiter,
) -> dict[str, Any]:
    """调用 esearch.fcgi（协程）；返回 esearchresult（含 count/idlist，可选 webenv/querykey）。

    参数编码后超过 POST_CHAR_THRESHOLD（500 字符）时自动改用 HTTP POST，
    参数编码规则不变（见模块 docstring 的编码约定）。

    参数:
        args: 命令行命名空间；读取 sort/mindate/maxdate/datetype/api_key/email。
        query: 完整 PubMed 检索式（空白已归一化）。
        retstart: 本页起始偏移（0 基）。
        retmax: 本页返回 UID 数。
        usehistory: 是否把结果挂到 History 服务器（返回 WebEnv/query_key）。
        limiter: 协程限流器。

    返回:
        esearchresult 字典；至少含 count 与 idlist，usehistory 时另有 webenv/querykey。

    异常:
        RuntimeError: 经由 http_get / http_post 在重试耗尽时抛出。
    """
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
    await limiter.acquire()
    # NCBI 建议：编码后超长的查询改用 HTTP POST，避免 URL 被代理/服务端截断
    encoded: str = _urlencode(params)
    raw: bytes
    if len(encoded) > POST_CHAR_THRESHOLD:
        raw = await asyncio.to_thread(http_post, f"{EUTILS}/esearch.fcgi", params)
    else:
        raw = await asyncio.to_thread(http_get, f"{EUTILS}/esearch.fcgi?{encoded}", 60, 4, False)
    data: dict[str, Any] = json.loads(raw.decode("utf-8"))
    return data.get("esearchresult", {})


async def efetch_medline(
    args: argparse.Namespace, pmids: list[str], limiter: AsyncRateLimiter
) -> tuple[list[dict[str, Any]], list[list[tuple[str, str]]]]:
    """按 PMID 列表走 efetch.fcgi 拉 MEDLINE 文本并解析（协程）。

    参数:
        args: 命令行命名空间。
        pmids: PMID 列表（单批建议 <=200）。
        limiter: 协程限流器。

    返回:
        (paper 列表, 平行对齐的 MEDLINE 记录列表)。
    """
    params: dict[str, str | None] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "text",
        "rettype": "medline",
    }
    params.update(_tool_params(args))
    url: str = f"{EUTILS}/efetch.fcgi?{_urlencode(params)}"
    text: str = (await _ahttp_get(url, limiter, timeout=120)).decode("utf-8", errors="replace")
    records: list[list[tuple[str, str]]] = parse_medline(text)
    papers: list[dict[str, Any]] = [record_to_paper(r) for r in records]
    attach_raw_medline(papers, records)
    return papers, records


async def efetch_xml(
    args: argparse.Namespace, pmids: list[str], limiter: AsyncRateLimiter
) -> list[dict[str, Any]]:
    """按 PMID 列表走 efetch.fcgi 拉 PubMed XML 并解析（协程）。

    参数:
        args: 命令行命名空间。
        pmids: PMID 列表（单批建议 <=200）。
        limiter: 协程限流器。

    返回:
        paper 列表（无原始 MEDLINE 记录可回带）。
    """
    params: dict[str, str | None] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    params.update(_tool_params(args))
    url: str = f"{EUTILS}/efetch.fcgi?{_urlencode(params)}"
    text: str = (await _ahttp_get(url, limiter, timeout=120)).decode("utf-8", errors="replace")
    return parse_pubmed_xml(text)


# ---------------------------------------------------------------------------
# Search / Fetch 命令（asyncio 入口）
# ---------------------------------------------------------------------------


def _today_str() -> str:
    """返回 NCBI 接受的 YYYY/MM/DD 格式当前日期。"""
    return time.strftime("%Y/%m/%d")


def _normalize_date_arg(d: str) -> str:
    """把用户输入的 YYYY 或 YYYY/MM/DD 统一为 YYYY/MM/DD（NCBI 接受）。"""
    d = (d or "").strip()
    if not d:
        return ""
    if re.fullmatch(r"\d{4}", d):
        return f"{d}/01/01"
    if re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", d):
        return d
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return d.replace("-", "/")
    if re.fullmatch(r"\d{4}/\d{1,2}", d):
        return f"{d}/01"
    return d


async def _cmd_search_async(args: argparse.Namespace) -> int:
    """执行 search 子命令（协程版）：ESearch 拿句柄 -> EFetch 并发拉取解析 -> 输出 JSON。

    流程要点：
      1. 读取检索式（--query-file 优先），归一化空白（空格/换行 -> 单空格），
         规范化日期参数并补全缺失的 maxdate；
      2. ESearch 拿检索句柄：usehistory 下只需一次调用（count + WebEnv/query_key，
         结果集已按排序挂到 History 服务器）；退化模式按固定页大小 20 分页收集 UID。
         plan = min(total, limit)；
      3. EFetch 每批作为一个 asyncio.Task 并发执行（信号量限在途数、限流器控起步节奏），
         拉完就地解析；超长检索式自动改 POST；
      4. 全部拉完后按发表日期降序重排（双保险，见 --sort 说明），写入结果 JSON。

    参数:
        args: search 子命令的命名空间。

    返回:
        进程退出码（0 成功）。
    """
    limiter: AsyncRateLimiter = AsyncRateLimiter(args.api_key)
    sem: asyncio.Semaphore = asyncio.Semaphore(_max_in_flight(args.api_key))
    if getattr(args, "query_file", None):
        with open(args.query_file, "r", encoding="utf-8") as f:
            args.query = f.read().strip()

    # 检索式空白归一化：把换行/制表符/连续空格折叠成单空格，
    # 使 urlencode 将其编码为 '+' 而不是换行的 %0A（NCBI：spaces may be replaced by '+'）
    args.query = " ".join(args.query.split())

    query: str = args.query
    retmax: int = ESearch_PAGE_SIZE  # ESearch 分页页大小固定 20（内部参数）
    limit: int = args.limit    # 用户希望拉取总量，0=全部
    usehistory: bool = getattr(args, "usehistory", True)

    # 日期自动补全：NCBI 仅给 mindate 时会静默忽略过滤，必须同时提供 maxdate
    args.mindate = _normalize_date_arg(args.mindate)
    args.maxdate = _normalize_date_arg(args.maxdate)
    if args.mindate and not args.maxdate:
        args.maxdate = _today_str()
        print(
            f"[search] 只提供 mindate，自动将 maxdate 设为今天 {args.maxdate}",
            file=sys.stderr,
        )

    # 1) ESearch 收集检索结果句柄
    idlist: list[str] = []
    total: int = 0
    webenv: str = ""
    query_key: str = ""
    plan: int = 0  # 计划拉取篇数 = min(total, limit)（limit>0 时）；usehistory 下驱动 EFetch 窗口
    if usehistory:
        # usehistory 模式：ESearch 会把完整结果集按排序挂到 History 服务器，
        # 一次调用即可拿到 count + WebEnv/query_key；EFetch 用 retstart/retmax 窗口取。
        # 避免 retmax=20 时按页收集 UID 造成大量无效 ESearch 请求（API 调用优化）。
        res: dict[str, Any] = await esearch(args, query, 0, retmax, True, limiter)
        total = int(res.get("count", "0"))
        webenv = str(res.get("webenv", "") or "")
        query_key = str(res.get("querykey", "") or "")
        plan = min(total, limit) if limit > 0 else total
    else:
        # 退化模式：无 History 服务器可用，只能按 retstart/retmax 分页收集 UID
        retstart: int = 0
        while True:
            # 若用户设了 limit，本页大小取 min(页大小, 剩余数量)
            page: int = retmax
            if limit > 0:
                remaining: int = limit - len(idlist)
                if remaining <= 0:
                    break
                page = min(retmax, remaining)

            res = await esearch(args, query, retstart, page, False, limiter)
            total = int(res.get("count", "0"))
            ids: list[str] = res.get("idlist", [])
            returned: int = int(res.get("retmax", len(ids)))
            idlist.extend(ids)
            if limit > 0 and len(idlist) >= limit:
                idlist = idlist[:limit]
                break
            if returned < page or not ids:
                break
            retstart += returned
        plan = len(idlist)

    total_out: int = min(total, limit) if limit > 0 else total
    print(f"[esearch] 命中 {total} 条，计划拉取 {plan} 条", file=sys.stderr)

    # 大结果集提示
    if limit == 0 and total > 200:
        print(
            f"[warn] 未设置 --limit，将拉取全部 {total} 条。"
            "如只需样例，请用 --limit 设置上限以节省时间和带宽。",
            file=sys.stderr,
        )
    # NCBI 硬性限制：PubMed ESearch 单次查询最多返回前 10000 条
    if total > 10000:
        print(
            f"[warn] PubMed ESearch 单次查询最多返回前 10000 条（本查询命中 {total}）。"
            "超出部分需用 EDirect 或按日期分段查询获取。",
            file=sys.stderr,
        )

    # 2) EFetch 拉取元数据：每批一个协程任务，信号量限在途数、限流器控起步节奏
    retmode: str = getattr(args, "retmode", "medline")
    batch: int = args.batch
    papers: list[dict[str, Any]] = []
    fetch_errors: list[str] = []

    async def fetch_window(start: int) -> tuple[list[dict[str, Any]], str]:
        """单个 EFetch 窗口的协程任务：限流 -> 拉取 -> 就地解析。

        参数:
            start: 窗口起始偏移（usehistory 下对应历史会话 retstart；
                退化模式下映射到 idlist 切片）。

        返回:
            (该批 papers, 错误信息)；成功时错误信息为空串。
        """
        async with sem:
            this_batch: int = min(batch, plan - start)
            end: int = start + this_batch
            try:
                if usehistory and webenv and query_key:
                    # 历史会话保存完整排序结果集，retstart/retmax 只是取数窗口
                    params: dict[str, str | None] = {
                        "db": "pubmed",
                        "query_key": query_key,
                        "WebEnv": webenv,
                        "retstart": str(start),
                        "retmax": str(this_batch),
                    }
                    if retmode == "xml":
                        params["retmode"] = "xml"
                    else:
                        params["retmode"] = "text"
                        params["rettype"] = "medline"
                    params.update(_tool_params(args))
                    url: str = f"{EUTILS}/efetch.fcgi?{_urlencode(params)}"
                    text: str = (await _ahttp_get(url, limiter, timeout=120)).decode("utf-8", errors="replace")
                    chunk_papers: list[dict[str, Any]] = parse_chunk_text(text, retmode)
                else:
                    # 退化模式：显式 PMID 列表
                    chunk_ids: list[str] = idlist[start:end]
                    params = {
                        "db": "pubmed",
                        "id": ",".join(chunk_ids),
                    }
                    if retmode == "xml":
                        params["retmode"] = "xml"
                    else:
                        params["retmode"] = "text"
                        params["rettype"] = "medline"
                    params.update(_tool_params(args))
                    url = f"{EUTILS}/efetch.fcgi?{_urlencode(params)}"
                    text = (await _ahttp_get(url, limiter, timeout=120)).decode("utf-8", errors="replace")
                    chunk_papers = parse_chunk_text(text, retmode)
                print(f"[efetch] {end}/{plan}", file=sys.stderr)
                return chunk_papers, ""
            except Exception as e:
                return [], f"批次 {start}-{end} 拉取失败: {e}"

    window_starts: list[int] = list(range(0, plan, batch))
    results: list[tuple[list[dict[str, Any]], str]] = await asyncio.gather(
        *[fetch_window(s) for s in window_starts]
    )
    for chunk_papers, err in results:
        papers.extend(chunk_papers)
        if err:
            fetch_errors.append(err)

    for err in fetch_errors:
        print(f"[efetch] {err}", file=sys.stderr)

    # ------------------------------------------------------------------
    # 客户端二次排序：无论 ESearch / History 服务器返回顺序如何，
    # 统一按解析后的发表日期字符串降序排列，保证"最新发表在前"。
    # normalize_date 产出 "YYYY" / "YYYY-MM" / "YYYY-MM-DD"，字典序即时间序；
    # 缺日期的记录排到最后。
    # ------------------------------------------------------------------
    papers.sort(key=lambda p: str(p.get("date") or ""), reverse=True)

    result: dict[str, Any] = {
        "source": "pubmed",
        "query": query,
        "mindate": args.mindate or "",
        "maxdate": args.maxdate or "",
        "datetype": getattr(args, "datetype", "pdat") or "pdat",
        "sort": args.sort or "pub_date",
        "search_date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": total_out,
        "fetched": len(papers),
        "papers": papers,
    }
    _dump(result, args.out)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """search 子命令的同步入口：在独立事件循环中跑协程版实现。

    参数:
        args: search 子命令的命名空间。

    返回:
        进程退出码（0 成功）。
    """
    return asyncio.run(_cmd_search_async(args))


async def _cmd_fetch_async(args: argparse.Namespace) -> int:
    """执行 fetch 子命令（协程版）：按 PMID 列表分批并发拉取元数据。

    参数:
        args: fetch 子命令的命名空间（--ids/--batch/--retmode/--out）。

    返回:
        进程退出码（0 成功）。
    """
    limiter: AsyncRateLimiter = AsyncRateLimiter(args.api_key)
    sem: asyncio.Semaphore = asyncio.Semaphore(_max_in_flight(args.api_key))
    pmids: list[str] = [p.strip() for p in args.ids.split(",") if p.strip()]
    retmode: str = getattr(args, "retmode", "medline")
    papers: list[dict[str, Any]] = []
    fetch_errors: list[str] = []

    async def fetch_chunk(start: int) -> tuple[list[dict[str, Any]], str]:
        """单批 PMID 拉取任务。

        参数:
            start: 本批在 pmids 中的起始下标。

        返回:
            (该批 papers, 错误信息)；成功时错误信息为空串。
        """
        async with sem:
            chunk: list[str] = pmids[start : start + args.batch]
            end: int = start + len(chunk)
            try:
                if retmode == "xml":
                    chunk_papers: list[dict[str, Any]] = await efetch_xml(args, chunk, limiter)
                else:
                    chunk_papers, _ = await efetch_medline(args, chunk, limiter)
                print(f"[efetch] {end}/{len(pmids)}", file=sys.stderr)
                return chunk_papers, ""
            except Exception as e:
                return [], f"批次 {start}-{end} 拉取失败: {e}"

    results: list[tuple[list[dict[str, Any]], str]] = await asyncio.gather(
        *[fetch_chunk(i) for i in range(0, len(pmids), args.batch)]
    )
    for chunk_papers, err in results:
        papers.extend(chunk_papers)
        if err:
            fetch_errors.append(err)
    for err in fetch_errors:
        print(f"[efetch] {err}", file=sys.stderr)

    # 与 search 保持一致：客户端按发表日期降序重排
    papers.sort(key=lambda p: str(p.get("date") or ""), reverse=True)

    result: dict[str, Any] = {
        "source": "pubmed",
        "query": f"ids:{args.ids}",
        "search_date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(papers),
        "fetched": len(papers),
        "papers": papers,
    }
    _dump(result, args.out)
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """fetch 子命令的同步入口：在独立事件循环中跑协程版实现。

    参数:
        args: fetch 子命令的命名空间。

    返回:
        进程退出码（0 成功）。
    """
    return asyncio.run(_cmd_fetch_async(args))


# ---------------------------------------------------------------------------
# MeSH 校验（NLM MeSH RDF lookup API，无需 key；调用量小，保持同步）
# ---------------------------------------------------------------------------


def _mesh_descriptor_label(descriptor_id: str) -> str:
    """按 descriptor id 取 MeSH 官方标签文本；失败返回空串。"""
    url: str = f"https://id.nlm.nih.gov/mesh/{descriptor_id}.json"
    try:
        data: dict[str, Any] = json.loads(http_get(url).decode("utf-8"))
        label: Any = data.get("label", {})
        if isinstance(label, dict):
            return label.get("@value", "")
        return str(label)
    except Exception:
        return ""


def mesh_lookup(term: str, match: str) -> list[str]:
    """调用 MeSH lookup API，返回匹配的 descriptor id 列表。

    参数:
        term: 待查术语。
        match: 匹配模式，'exact' 精确或 'contains' 包含。

    返回:
        descriptor id（D/C 开头）列表；无命中为空列表。
    """
    url: str = (
        f"{MESH_LOOKUP}?{_urlencode({'label': term, 'match': match, 'limit': '10'})}"
    )
    data: Any = json.loads(http_get(url).decode("utf-8"))
    out: list[str] = []
    uri: Any
    for uri in data if isinstance(data, list) else []:
        m: re.Match[str] | None = re.search(r"/mesh/(D\d+|C\d+)", str(uri))
        if m:
            out.append(m.group(1))
    return out


def cmd_mesh(args: argparse.Namespace) -> int:
    """执行 mesh 子命令：逐个校验术语是否为 MeSH 主题词。

    对每个术语先 exact 匹配；未命中再 contains 匹配取前 5 个建议词，
    并取每个 descriptor 的官方标签，输出结构化 JSON 供检索式构造参考。

    参数:
        args: mesh 子命令的命名空间。

    返回:
        进程退出码（0 成功，1 表示未提供任何术语）。
    """
    terms: list[str] = list(args.terms or [])
    if getattr(args, "terms_file", None):
        with open(args.terms_file, "r", encoding="utf-8") as f:
            terms.extend(ln.strip() for ln in f if ln.strip())
    if not terms:
        print("[mesh] 未提供任何术语", file=sys.stderr)
        return 1
    results: list[dict[str, Any]] = []
    for term in terms:
        entry: dict[str, Any] = {
            "term": term,
            "status": "not_found",
            "descriptor_id": "",
            "label": "",
            "suggestions": [],
        }
        try:
            hits: list[str] = mesh_lookup(term, "exact")
            if hits:
                entry["status"] = "exact"
                entry["descriptor_id"] = hits[0]
                entry["label"] = _mesh_descriptor_label(hits[0])
            else:
                sugg: list[str] = mesh_lookup(term, "contains")[:5]
                if sugg:
                    entry["status"] = "suggest"
                    entry["suggestions"] = [
                        {"descriptor_id": s, "label": _mesh_descriptor_label(s)}
                        for s in sugg
                    ]
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
        results.append(entry)
        print(f"[mesh] {term}: {entry['status']}", file=sys.stderr)
    _dump({"mesh_check": results}, args.out)
    return 0


# ---------------------------------------------------------------------------


def _dump(obj: dict[str, Any], out: str) -> None:
    """把结果对象写出为 JSON（ensure_ascii=False 保留中文）。

    参数:
        obj: 待输出的字典。
        out: 输出路径；'-' 或空串表示写到 stdout。

    返回:
        None。
    """
    text: str = json.dumps(obj, ensure_ascii=False, indent=2)
    if out == "-" or not out:
        sys.stdout.write(text + "\n")
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[out] 已写入 {out}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """构建参数解析器并分发到各子命令。

    参数:
        argv: 参数列表；None 时取 sys.argv[1:]。

    返回:
        子命令的退出码。
    """
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description="PubMed 检索 / 拉取 / MeSH 校验"
    )
    ap.add_argument(
        "--api-key", default=None, help="NCBI API key（或设环境变量 NCBI_API_KEY）"
    )
    ap.add_argument("--email", default=None, help="联系邮箱（NCBI 建议提供）")
    sub: argparse._SubParsersAction = ap.add_subparsers(dest="cmd", required=True)

    s: argparse.ArgumentParser = sub.add_parser("search", help="检索 PubMed 并拉取元数据")
    q: argparse._MutuallyExclusiveGroup = s.add_mutually_exclusive_group(required=True)
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
        "--limit",
        type=int,
        default=0,
        help="最多拉取文献篇数；0=全部/无限（默认）。未设日期范围时应向用户确认；命中量大时建议设上限",
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
    s.add_argument(
        "--sort",
        default="pub_date",
        choices=["pub_date", "relevance", "most_recent", "journal", "title", ""],
        help=(
            "ESearch 排序方式（默认 pub_date = 按发表日期降序，最新在前）。"
            "可选：pub_date / relevance / most_recent / journal / title；"
            "usehistory 模式下该顺序会被保留并随 EFetch 按序取回"
        ),
    )
    s.add_argument("--out", required=True, help="输出 JSON 路径，'-' 输出到 stdout")
    s.set_defaults(func=cmd_search)

    f: argparse.ArgumentParser = sub.add_parser("fetch", help="按 PMID 列表拉取元数据")
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

    m: argparse.ArgumentParser = sub.add_parser("mesh", help="校验术语是否为 MeSH 主题词")
    m.add_argument("terms", nargs="*", help="待校验术语（英文，含空格的词加引号）")
    m.add_argument("--terms-file", default=None, help="从 UTF-8 文件读取术语（每行一个）")
    m.add_argument("--out", default="-", help="输出 JSON 路径，默认 stdout")
    m.set_defaults(func=cmd_mesh)

    args: argparse.Namespace = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
