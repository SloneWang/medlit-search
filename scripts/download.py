#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download.py — 开放获取（OA）原文下载

策略（仅合法渠道，不碰盗版源）：
  1. 论文已有 PMCID -> NCBI PMC OA 服务 (oa.fcgi) 取 PDF/tgz 链接下载
  2. 无 PMCID -> idconv API 尝试 PMID -> PMCID 转换，成功则走 1
  3. 非 OA -> manifest 记录 DOI / PubMed 落地页，由用户经机构权限自行获取

产物：
  <outdir>/<PMID>_<标题slug>.pdf （或 .tgz）
  <outdir>/manifest.json  每篇状态：downloaded / not_oa / error

IO 模型（v1.8 aiohttp 原生协程）：每篇下载作为一个 asyncio.Task，
asyncio.Semaphore 限制在途下载数（MAX_DOWNLOAD_CONCURRENCY=2），
AsyncRateLimiter 保证总请求 ≤3 req/s。全部 HTTP 走 aiohttp.ClientSession。
依赖：aiohttp、Python 标准库。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tarfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import IO, Any

import aiohttp

# 版本标识（与 SKILL.md 保持一致）
__version__: str = "1.8"

# HTTP 请求 User-Agent，便于 NCBI 识别工具来源
USER_AGENT: str = "medlit-search/1.8 (PMC OA download)"
# PMC OA 服务：按 PMCID 查 PDF/tgz 下载链接
OA_FCGI: str = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
# ID 转换服务：PMID -> PMCID
IDCONV: str = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

# 在途全文下载并发上限（带宽友好；限流器仍保证总请求 ≤3 req/s）
MAX_DOWNLOAD_CONCURRENCY: int = 2


class AsyncRateLimiter:
    """协程限流器：相邻请求起步间隔 >= 1/3 秒（NCBI 无 key 上限）。"""

    def __init__(self, interval: float = 1.0 / 3.0) -> None:
        """初始化限流器。

        参数:
            interval: 相邻两次请求起步的最小间隔（秒）。
        """
        self.interval: float = interval
        self._next: float = 0.0

    async def acquire(self) -> None:
        """异步等待到下一个允许起步请求的时间点，并推进内部时钟。"""
        now: float = time.monotonic()
        if now < self._next:
            await asyncio.sleep(self._next - now)
        self._next = time.monotonic() + self.interval


async def ahttp_get(
    session: aiohttp.ClientSession,
    url: str,
    limiter: AsyncRateLimiter,
    timeout: int = 120,
    retries: int = 3,
) -> bytes:
    """发起异步 GET 并返回响应字节，带限流与指数退避重试。

    参数:
        session: aiohttp 会话。
        url: 请求 URL。
        limiter: 协程限流器。
        timeout: 单次超时秒数。
        retries: 最大尝试次数。

    返回:
        响应体字节。

    异常:
        RuntimeError: 全部重试均失败。
    """
    last: Exception | None = None
    for attempt in range(retries):
        await limiter.acquire()
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as r:
                if r.status >= 400:
                    last = RuntimeError(f"HTTP {r.status}")
                    await asyncio.sleep(min(2.0**attempt, 8.0))
                    continue
                return await r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(min(2.0**attempt, 8.0))
    raise RuntimeError(f"{url} -> {last}")


def slugify(text: str, n: int = 40) -> str:
    """把标题转成文件名安全 slug。

    参数:
        text: 原始标题。
        n: 最大长度，超出截断。

    返回:
        仅含字母数字下划线的 slug；空结果回退 'untitled'。
    """
    s: str = re.sub(r"[^\w\s-]", "", text or "", flags=re.UNICODE)
    s = re.sub(r"[\s-]+", "_", s).strip("_")
    return s[:n] or "untitled"


async def pmid_to_pmcid(
    session: aiohttp.ClientSession,
    pmid: str,
    email: str | None,
    api_key: str | None,
    limiter: AsyncRateLimiter,
) -> str:
    """经 idconv API 把 PMID 转成 PMCID。

    参数:
        session: aiohttp 会话。
        pmid: PubMed ID。
        email: 联系邮箱（可选，NCBI 建议提供）。
        api_key: NCBI API key（可选）。
        limiter: 协程限流器。

    返回:
        PMCID 字符串；转换失败或无 PMCID 时返回空串。
    """
    params: dict[str, str] = {"ids": pmid, "format": "json", "tool": "medlit_search"}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    url: str = f"{IDCONV}?{urllib.parse.urlencode(params)}"
    try:
        data: dict[str, Any] = json.loads(
            (await ahttp_get(session, url, limiter)).decode("utf-8")
        )
        rec: dict[str, Any]
        for rec in data.get("records", []):
            if rec.get("pmid") == pmid and rec.get("pmcid"):
                return rec["pmcid"]
    except Exception as e:  # noqa: BLE001
        print(f"  [idconv] {pmid} 转换失败: {e}", file=sys.stderr)
    return ""


async def oa_links(
    session: aiohttp.ClientSession,
    pmcid: str,
    email: str | None,
    api_key: str | None,
    limiter: AsyncRateLimiter,
) -> dict[str, str]:
    """经 oa.fcgi 查询 PMCID 的 OA 下载链接。

    参数:
        session: aiohttp 会话。
        pmcid: PMC 编号。
        email: 联系邮箱（可选）。
        api_key: NCBI API key（可选）。
        limiter: 协程限流器。

    返回:
        {'pdf': url, 'tgz': url}；非 OA 或查询失败返回空 dict。
    """
    params: dict[str, str] = {"id": pmcid}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    url: str = f"{OA_FCGI}?{urllib.parse.urlencode(params)}"
    try:
        raw: str = (await ahttp_get(session, url, limiter)).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"  [oa.fcgi] {pmcid} 查询失败: {e}", file=sys.stderr)
        return {}
    root: ET.Element = ET.fromstring(raw)
    rec: ET.Element | None = root.find(".//record")
    if rec is None:
        return {}
    links: dict[str, str] = {}
    link: ET.Element
    for link in rec.findall(".//link"):
        fmt: str = link.get("format", "")
        href: str = link.get("href", "")
        if not href:
            continue
        # 保留原始 href，下载阶段由 candidate_urls 生成 HTTPS/deprecated 候选
        links[fmt] = href
    return links


def candidate_urls(href: str) -> list[str]:
    """把 oa.fcgi 返回的 ftp:// 链接展开为按优先级排序的候选 URL。

    迁移适配背景（2026-04 NCBI 调整）：/pub/pmc/ 下的 oa_package、oa_pdf、
    oa_bulk 已迁入 /pub/pmc/deprecated/（2026-08 后旧路径彻底下线），
    因此 deprecated 路径作为第一候选优先尝试。v1.8 起 HTTP 层为 aiohttp，
    仅支持 http(s)，ftp 候选改写为等价 https://ftp.ncbi.nlm.nih.gov/ 路径。

    参数:
        href: oa.fcgi 给出的原始链接（通常 ftp://）。

    返回:
        候选 URL 列表：deprecated https > 原路径 https；
        非 NCBI ftp 链接时原样返回单元素列表。
    """
    cands: list[str] = []
    if href.startswith("ftp://ftp.ncbi.nlm.nih.gov/") or href.startswith(
        "https://ftp.ncbi.nlm.nih.gov/"
    ):
        path: str = href.split("ftp.ncbi.nlm.nih.gov/", 1)[-1]
        https: str = "https://ftp.ncbi.nlm.nih.gov/" + path
        legacy_dir: str
        for legacy_dir in ("pub/pmc/oa_package/", "pub/pmc/oa_pdf/", "pub/pmc/oa_bulk/"):
            if legacy_dir in path:
                # 旧目录映射到 deprecated/<类别>/，命中一个即停止（目录互斥）
                dep: str = path.replace(
                    legacy_dir, "pub/pmc/deprecated/" + legacy_dir.split("/", 2)[-1], 1
                )
                cands.append("https://ftp.ncbi.nlm.nih.gov/" + dep)
                break
        cands.append(https)
    else:
        cands.append(href)
    return cands


async def fetch_first(
    session: aiohttp.ClientSession,
    cands: list[str],
    limiter: AsyncRateLimiter,
    timeout: int = 300,
) -> tuple[bytes, str]:
    """按顺序尝试候选 URL，返回第一个成功的响应。

    参数:
        session: aiohttp 会话。
        cands: 候选 URL 列表。
        limiter: 协程限流器。
        timeout: 单次请求超时秒数。

    返回:
        (响应字节, 实际命中的 URL)。

    异常:
        RuntimeError: 全部候选均失败。
    """
    last: Exception | None = None
    url: str
    for url in cands:
        try:
            return await ahttp_get(session, url, limiter, timeout=timeout), url
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"所有候选 URL 均失败: {cands} -> {last}")


def extract_pdf_from_tgz(tgz_path: str, outdir: str, base: str) -> str:
    """从 OA tgz 包中抽出 PDF 单独落盘。

    OA tgz 内通常含 <article>.pdf 及 XML/图片；只取第一个 PDF 成员，
    以 base 命名写到 outdir，便于直接阅读。

    参数:
        tgz_path: tgz 包路径。
        outdir: PDF 输出目录。
        base: 输出文件名（不含扩展名）。

    返回:
        抽出的 PDF 路径；失败返回空串（原始 tgz 保留）。
    """
    member: tarfile.TarInfo
    data: tarfile.ExFileObject | None
    pdf_path: str
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar.getmembers():
                # 只挑普通文件且以 .pdf 结尾的成员（大小写不敏感）
                if member.isfile() and member.name.lower().endswith(".pdf"):
                    data = tar.extractfile(member)
                    if data is None:
                        continue
                    pdf_path = os.path.join(outdir, base + ".pdf")
                    f: IO[bytes]
                    with open(pdf_path, "wb") as f:
                        f.write(data.read())
                    return pdf_path
    except Exception as e:  # noqa: BLE001
        print(f"  [tgz] 解包失败（保留原始 tgz）: {e}", file=sys.stderr)
    return ""


async def download_one(
    session: aiohttp.ClientSession,
    paper: dict[str, Any],
    outdir: str,
    email: str | None,
    api_key: str | None,
    limiter: AsyncRateLimiter,
) -> dict[str, Any]:
    """下载单篇论文的 OA 全文（状态机式流程）。

    流程：取/换 PMCID -> oa.fcgi 查链接 -> 生成候选 URL 下载 ->
    tgz 则解包抽 PDF；任一步不可达则记录状态与落地页。

    参数:
        session: aiohttp 会话。
        paper: 论文字典（至少含 pmid；title/doi/url/pmc 可选）。
        outdir: 文件保存目录。
        email: 联系邮箱（可选）。
        api_key: NCBI API key（可选）。
        limiter: 协程限流器。

    返回:
        manifest 条目：pmid/title/status/file/message/landing_url；
        status 取值 downloaded / not_oa / error。
    """
    pmid: str = str(paper.get("pmid", ""))
    entry: dict[str, Any] = {
        "pmid": pmid,
        "title": paper.get("title", ""),
        "status": "error",
        "file": "",
        "message": "",
        "landing_url": "",
    }
    # 状态 1：拿到 PMCID（论文自带，或经 idconv 由 PMID 转换）；拿不到即非 OA
    pmcid: str = paper.get("pmc") or await pmid_to_pmcid(
        session, pmid, email, api_key, limiter
    )
    if not pmcid:
        entry["status"] = "not_oa"
        entry["message"] = "未收录于 PMC，非开放获取或暂无免费全文"
        entry["landing_url"] = (
            f"https://doi.org/{paper['doi']}" if paper.get("doi") else paper.get("url", "")
        )
        return entry

    # 状态 2：oa.fcgi 查 OA 链接，优先直链 PDF，否则取 tgz 包
    links: dict[str, str] = await oa_links(session, pmcid, email, api_key, limiter)
    href: str = ""
    ext: str = ""
    if links.get("pdf"):
        href, ext = links["pdf"], ".pdf"
    elif links.get("tgz"):
        href, ext = links["tgz"], ".tgz"
    if not href:
        entry["status"] = "not_oa"
        entry["message"] = f"{pmcid} 不在 PMC OA Subset（可能仅 PMC 站内可读）"
        entry["landing_url"] = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
        return entry

    # 状态 3：按候选优先级下载并落盘；tgz 再解包抽 PDF
    fname: str = f"{pmid}_{slugify(paper.get('title', ''))}{ext}"
    fpath: str = os.path.join(outdir, fname)
    try:
        data: bytes
        used_url: str
        data, used_url = await fetch_first(
            session, candidate_urls(href), limiter, timeout=300
        )
        f2: IO[bytes]
        with open(fpath, "wb") as f2:
            f2.write(data)
        entry["status"] = "downloaded"
        entry["file"] = fpath
        entry["message"] = f"{pmcid} -> {fname} ({len(data) // 1024} KB)"
        if ext == ".tgz":
            pdf: str = extract_pdf_from_tgz(fpath, outdir, fname[:-4])
            if pdf:
                entry["file"] = pdf
                entry["message"] += f"；已解出 PDF: {os.path.basename(pdf)}"
    except Exception as e:  # noqa: BLE001
        entry["status"] = "error"
        entry["message"] = f"下载失败: {e}"
        entry["landing_url"] = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
    return entry


def load_papers(path: str) -> list[dict[str, Any]]:
    """加载 JSON 文件并取出论文数组。

    参数:
        path: JSON 文件路径。支持纯数组，或含 papers/included/results 键的对象。

    返回:
        论文字典列表。

    异常:
        ValueError: JSON 中找不到论文数组。
    """
    data: Any
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    key: str
    for key in ("papers", "included", "results"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    raise ValueError("输入 JSON 中找不到论文数组")


async def _run_all(
    papers: list[dict[str, Any]],
    outdir: str,
    email: str | None,
    api_key: str | None,
) -> tuple[list[dict[str, Any]], int]:
    """并发执行全部下载任务并汇总 manifest。

    每篇一个 asyncio.Task；信号量限制在途下载数，AsyncRateLimiter 控起步节奏。

    参数:
        papers: 待下载论文列表。
        outdir: 保存目录。
        email: 联系邮箱（可选）。
        api_key: NCBI API key（可选）。

    返回:
        (manifest 条目列表（与 papers 顺序一致）, 成功下载篇数)。
    """
    sem: asyncio.Semaphore = asyncio.Semaphore(MAX_DOWNLOAD_CONCURRENCY)
    limiter: AsyncRateLimiter = AsyncRateLimiter()
    total: int = len(papers)

    async with aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT}
    ) as session:

        async def one(idx: int, paper: dict[str, Any]) -> dict[str, Any]:
            """单篇下载任务。

            参数:
                idx: 论文序号（0 基，用于进度日志）。
                paper: 论文字典。

            返回:
                该篇 manifest 条目。
            """
            async with sem:
                entry: dict[str, Any] = await download_one(
                    session, paper, outdir, email, api_key, limiter
                )
                print(
                    f"[{idx + 1}/{total}] {entry['pmid']}: {entry['status']} {entry['message']}",
                    file=sys.stderr,
                )
                return entry

        entries: list[dict[str, Any]] = await asyncio.gather(
            *[one(i, p) for i, p in enumerate(papers)]
        )
    ok: int = sum(1 for e in entries if e["status"] == "downloaded")
    return list(entries), ok


def main(argv: list[str] | None = None) -> int:
    """命令行入口：协程并发下载并写 manifest.json。

    参数:
        argv: 参数列表；None 时取 sys.argv。

    返回:
        0 成功（含部分失败，详见 manifest）。
    """
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description="PMC 开放获取原文下载")
    ap.add_argument("--input", required=True, help="pubmed.py 产出的 JSON / 筛选结果 JSON")
    ap.add_argument("--ids", default=None, help="只下载这些 PMID（逗号分隔），默认全部")
    ap.add_argument("--outdir", required=True, help="保存目录")
    ap.add_argument("--email", default=None)
    ap.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY"))
    args: argparse.Namespace = ap.parse_args(argv)

    papers: list[dict[str, Any]] = load_papers(args.input)
    if args.ids:
        keep: set[str] = {x.strip() for x in args.ids.split(",") if x.strip()}
        papers = [p for p in papers if str(p.get("pmid", "")) in keep]
    os.makedirs(args.outdir, exist_ok=True)

    manifest: list[dict[str, Any]]
    ok: int
    manifest, ok = asyncio.run(_run_all(papers, args.outdir, args.email, args.api_key))

    mpath: str = os.path.join(args.outdir, "manifest.json")
    f3: IO[str]
    with open(mpath, "w", encoding="utf-8") as f3:
        json.dump(
            {"downloaded": ok, "total": len(papers), "items": manifest},
            f3, ensure_ascii=False, indent=2,
        )
    print(f"[download] {ok}/{len(papers)} 篇已下载，manifest: {mpath}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
