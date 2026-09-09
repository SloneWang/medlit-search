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
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import IO, Any

USER_AGENT: str = "workbuddy-medlit-skill/1.0 (PMC OA download)"
OA_FCGI: str = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
IDCONV: str = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

_next: float = 0.0


def _wait() -> None:
    global _next
    now: float = time.monotonic()
    if now < _next:
        time.sleep(_next - now)
    _next = time.monotonic() + 1.0 / 3.0  # 3 req/s


def http_get(url: str, timeout: int = 120, retries: int = 3) -> bytes:
    last: Exception | None = None
    req: urllib.request.Request
    for attempt in range(retries):
        _wait()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(2.0 ** attempt, 8.0))
    raise RuntimeError(f"{url} -> {last}")


def slugify(text: str, n: int = 40) -> str:
    s: str = re.sub(r"[^\w\s-]", "", text or "", flags=re.UNICODE)
    s = re.sub(r"[\s-]+", "_", s).strip("_")
    return s[:n] or "untitled"


def pmid_to_pmcid(pmid: str, email: str | None, api_key: str | None) -> str:
    params: dict[str, str] = {"ids": pmid, "format": "json", "tool": "workbuddy_medlit"}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    url: str = f"{IDCONV}?{urllib.parse.urlencode(params)}"
    try:
        data: dict[str, Any] = json.loads(http_get(url).decode("utf-8"))
        rec: dict[str, Any]
        for rec in data.get("records", []):
            if rec.get("pmid") == pmid and rec.get("pmcid"):
                return rec["pmcid"]
    except Exception as e:  # noqa: BLE001
        print(f"  [idconv] {pmid} 转换失败: {e}", file=sys.stderr)
    return ""


def oa_links(pmcid: str, email: str | None, api_key: str | None) -> dict[str, str]:
    """oa.fcgi -> {'pdf': url, 'tgz': url}；非 OA 返回空 dict。"""
    params: dict[str, str] = {"id": pmcid}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    url: str = f"{OA_FCGI}?{urllib.parse.urlencode(params)}"
    try:
        raw: str = http_get(url).decode("utf-8", errors="replace")
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
        # 保留原始 ftp:// href，下载阶段由 candidate_urls 生成 HTTPS/deprecated 候选
        links[fmt] = href
    return links


def candidate_urls(href: str) -> list[str]:
    """oa.fcgi 返回 ftp:// 链接；生成按优先级排序的候选 URL。

    背景（2026-04 NCBI 调整）：/pub/pmc/ 下的 oa_package、oa_pdf 等已迁入
    /pub/pmc/deprecated/（2026-08 后旧路径将彻底下线）。故 deprecated 路径优先。
    """
    cands: list[str] = []
    if href.startswith("ftp://ftp.ncbi.nlm.nih.gov/"):
        path: str = href[len("ftp://ftp.ncbi.nlm.nih.gov/"):]
        https: str = "https://ftp.ncbi.nlm.nih.gov/" + path
        legacy_dir: str
        for legacy_dir in ("pub/pmc/oa_package/", "pub/pmc/oa_pdf/", "pub/pmc/oa_bulk/"):
            if legacy_dir in path:
                dep: str = path.replace(legacy_dir, "pub/pmc/deprecated/" + legacy_dir.split("/", 2)[-1], 1)
                cands.append("https://ftp.ncbi.nlm.nih.gov/" + dep)
                break
        cands.append(https)
        cands.append(href)  # urllib 支持 ftp://，作为最后兜底
    else:
        cands.append(href)
    return cands


def fetch_first(cands: list[str], timeout: int = 300) -> tuple[bytes, str]:
    last: Exception | None = None
    url: str
    for url in cands:
        try:
            return http_get(url, timeout=timeout), url
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"所有候选 URL 均失败: {cands} -> {last}")


def extract_pdf_from_tgz(tgz_path: str, outdir: str, base: str) -> str:
    """OA tgz 内通常含 <article>.pdf（及 XML/图片）；抽出 PDF 便于直接阅读。"""
    import tarfile

    member: tarfile.TarInfo
    data: tarfile.ExFileObject | None
    pdf_path: str
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar.getmembers():
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


def download_one(paper: dict[str, Any], outdir: str, email: str | None, api_key: str | None) -> dict[str, Any]:
    pmid: str = str(paper.get("pmid", ""))
    entry: dict[str, Any] = {
        "pmid": pmid,
        "title": paper.get("title", ""),
        "status": "error",
        "file": "",
        "message": "",
        "landing_url": "",
    }
    pmcid: str = paper.get("pmc") or pmid_to_pmcid(pmid, email, api_key)
    if not pmcid:
        entry["status"] = "not_oa"
        entry["message"] = "未收录于 PMC，非开放获取或暂无免费全文"
        entry["landing_url"] = (
            f"https://doi.org/{paper['doi']}" if paper.get("doi") else paper.get("url", "")
        )
        return entry

    links: dict[str, str] = oa_links(pmcid, email, api_key)
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

    fname: str = f"{pmid}_{slugify(paper.get('title', ''))}{ext}"
    fpath: str = os.path.join(outdir, fname)
    try:
        data: bytes
        used_url: str
        data, used_url = fetch_first(candidate_urls(href), timeout=300)
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


def main(argv: list[str] | None = None) -> int:
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

    manifest: list[dict[str, Any]] = []
    ok: int = 0
    i: int
    p: dict[str, Any]
    entry: dict[str, Any]
    for i, p in enumerate(papers, 1):
        entry = download_one(p, args.outdir, args.email, args.api_key)
        manifest.append(entry)
        ok += entry["status"] == "downloaded"
        print(f"[{i}/{len(papers)}] {entry['pmid']}: {entry['status']} {entry['message']}", file=sys.stderr)

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
