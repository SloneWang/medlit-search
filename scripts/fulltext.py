#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fulltext.py — 复筛全文准备器（v1.5）

用途：为"全文层面复筛"把全文文件凑齐，再由 LLM 直接读文件判定。
  1. 优先索引用户自己提供的 PDF/DOCX/TXT/...（按 DOI > PMID > PMC > 标题 匹配）
  2. 仍未匹配且有 DOI 的，走合法 OA 渠道 Unpaywall 尝试补全
  3. 产出 manifest CSV（utf-8-sig，Excel 友好），标出每篇"是否可判全文"

产物不替代 download.py（后者走 PMC OA Subset / oa.fcgi）；
本脚本的 Unpaywall 分支对应 SKILL.md 里记录的"PMC oa.fcgi 404 时的回退方案"，
并按实测补充做了加强：url_for_pdf 为 null 时 fallback 到落地页 url、
PMC 落地页追加 /pdf 变体、带 UA+Referer 下载、落盘前魔数与大小双重校验。

CLI:
  python fulltext.py --input pool.json --dir <任务目录> [--provide <文件或目录> ...]
                     [--outdir papers/] [--email <邮箱>] [--no-download]
                     --out fulltext_manifest.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # 复用 download.py 的既有实现（slugify），不重复造轮子
    import download as _dl  # type: ignore
except Exception:  # noqa: BLE001
    _dl = None  # type: ignore

Paper = dict[str, Any]

UA: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
PLACEHOLDER_EMAIL: str = "medlit.search@example.com"
DEFAULT_EMAIL: str = (os.environ.get("UNPAYWALL_EMAIL") or "").strip() or PLACEHOLDER_EMAIL
UNPAYWALL_API: str = "https://api.unpaywall.org/v2/"
FULLTEXT_EXT: set[str] = {".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm"}
MIN_PDF_BYTES: int = 20000
MAX_CANDIDATES: int = 6
MANIFEST_HEADER: list[str] = [
    "序号", "PMID", "题目", "DOI", "全文文件名", "全文路径", "是否可判全文", "获取方式", "说明",
]


# ---------------------------------------------------------------------------
# 载入与归一化
# ---------------------------------------------------------------------------


def load_papers(path: str) -> list[Paper]:
    """接受纯数组，或 {"papers"/"included"/"results": [...]} 包裹。"""
    data: Any
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    key: str
    for key in ("papers", "included", "results"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    raise ValueError("输入 JSON 中找不到论文数组（papers/included/results）")


def _norm_doi(s: str) -> str:
    out: str = (s or "").strip().lower()
    for prefix in ("https://dx.doi.org/", "http://dx.doi.org/", "https://doi.org/", "http://doi.org/", "doi:"):
        if out.startswith(prefix):
            out = out[len(prefix):]
            break
    return out


def _norm_title(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", (s or "").lower())


def _pmc_num(s: str) -> str:
    return re.sub(r"[^0-9]", "", re.sub(r"(?i)^pmc", "", (s or "").strip()))


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.一-龥-]", "_", name or "")[:120]


# ---------------------------------------------------------------------------
# 用户提供的全文
# ---------------------------------------------------------------------------


def collect_files(paths: list[str]) -> list[str]:
    """--provide 可以是文件也可以是目录（递归）；只收全文类扩展名。"""
    out: list[str] = []
    p: str
    root: str
    _dirs: list[str]
    files: list[str]
    fn: str
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    if os.path.splitext(fn)[1].lower() in FULLTEXT_EXT:
                        out.append(os.path.join(root, fn))
        elif os.path.isfile(p):
            if os.path.splitext(p)[1].lower() in FULLTEXT_EXT:
                out.append(p)
        else:
            print(f"[fulltext] --provide 路径不存在，已忽略：{p}", file=sys.stderr)
    return out


def match_score(stem: str, paper: Paper) -> tuple[int, str]:
    """文件名主干 vs 一篇文献的匹配置信度（0 = 不匹配）。"""
    # a) DOI（Windows 文件名不能含 "/"，故再试一次把 "_" 当分隔符的写法）
    doi: str = _norm_doi(str(paper.get("doi") or ""))
    if doi:
        m: re.Match[str] | None = re.search(r"10\.\d{4,9}/[^\s_/]*[^\s_,;()\[\]]", stem)
        if m is None:
            m = re.search(r"10\.\d{4,9}/[^\s_/]*[^\s_,;()\[\]]", stem.replace("_", "/"))
        if m and _norm_doi(m.group(0)) == doi:
            return 100, "DOI 命中"
    # b) PMID
    pmid: str = str(paper.get("pmid") or "").strip()
    if pmid:
        m2: re.Match[str] | None = re.search(r"PMID[\s_-]*(\d+)", stem, re.IGNORECASE)
        if m2 and m2.group(1) == pmid:
            return 90, "PMID 命中"
        if re.fullmatch(r"\d{1,8}", stem) and stem == pmid:
            return 85, "PMID 命中"
    # c) PMC
    pmc: str = _pmc_num(str(paper.get("pmc") or ""))
    if pmc:
        m3: re.Match[str] | None = re.search(r"PMC[\s_-]*(\d+)", stem, re.IGNORECASE)
        if m3 and m3.group(1) == pmc:
            return 70, "PMC 命中"
    # d) 标题
    core: str = _norm_title(stem)
    title: str = _norm_title(str(paper.get("title") or ""))
    if core and title:
        if len(title) >= 40 and title[:40] in core:
            return 60, "标题命中"
        if len(core) >= 15 and core in title:
            return 50, "标题命中"
    return 0, ""


def index_provided(papers: list[Paper], provide: list[str]) -> dict[int, tuple[str, int, str]]:
    """-> {论文下标: (绝对路径, 置信度, 命中方式)}；一篇被多文件命中时取置信度最高的。"""
    best: dict[int, tuple[str, int, str]] = {}
    fpath: str
    for fpath in collect_files(provide):
        stem: str = os.path.splitext(os.path.basename(fpath))[0]
        best_score: int = 0
        best_note: str = ""
        best_idx: int = -1
        i: int
        p: Paper
        for i, p in enumerate(papers):
            score: int
            note: str
            score, note = match_score(stem, p)
            if score > best_score:
                best_score, best_note, best_idx = score, note, i
        if best_idx < 0:
            print(f"[fulltext] 未能匹配到任何文献，已忽略：{fpath}", file=sys.stderr)
            continue
        cur: tuple[str, int, str] | None = best.get(best_idx)
        if cur is None or best_score > cur[1]:
            best[best_idx] = (os.path.abspath(fpath), best_score, best_note)
    return best


# ---------------------------------------------------------------------------
# Unpaywall（合法 OA 渠道）
# ---------------------------------------------------------------------------


def unpaywall_candidates(doi: str, email: str) -> tuple[list[str], dict[str, str], str]:
    """-> (候选 URL 列表, URL->host_type, 错误信息)。"""
    url: str = f"{UNPAYWALL_API}{urllib.parse.quote(doi, safe='/')}?{urllib.parse.urlencode({'email': email or DEFAULT_EMAIL})}"
    req: urllib.request.Request = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    data: dict[str, Any]
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body: str = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        msg: str = ""
        try:
            msg = str(json.loads(body).get("message", ""))
        except Exception:  # noqa: BLE001
            msg = body[:120]
        hint: str = ""
        if e.code == 422 and "email" in msg.lower():
            hint = "（请用 --email 填你自己的真实邮箱，Unpaywall 会拒绝 example.com 之类的占位地址）"
        return [], {}, f"Unpaywall 查询失败: HTTP {e.code} {msg}{hint}"
    except Exception as e:  # noqa: BLE001
        return [], {}, f"Unpaywall 查询失败: {type(e).__name__}: {e}"

    locs: list[dict[str, Any]] = [x for x in (data.get("oa_locations") or []) if isinstance(x, dict)]
    if not locs and isinstance(data.get("best_oa_location"), dict):
        locs = [data["best_oa_location"]]
    locs.sort(key=lambda x: 0 if x.get("host_type") == "publisher" else 1)

    cands: list[str] = []
    hosts: dict[str, str] = {}
    loc: dict[str, Any]
    for loc in locs:
        ht: str = str(loc.get("host_type") or "oa")
        key: str
        for key in ("url_for_pdf", "url"):  # url_for_pdf 经常是 null，必须 fallback 落地页
            u: Any = loc.get(key)
            if not isinstance(u, str) or not u.startswith("http"):
                continue
            if u not in cands:
                cands.append(u)
                hosts[u] = ht
            if "/pmc/articles/PMC" in u:
                v: str = u.rstrip("/") + "/pdf"
                if v not in cands:
                    cands.append(v)
                    hosts[v] = ht
    if not cands:
        return [], hosts, "Unpaywall 未返回任何 OA 候选链接（多半非 OA）"
    return cands[:MAX_CANDIDATES], hosts, ""


def fetch_bytes(url: str, doi: str, timeout: int = 120) -> bytes:
    """出版社直链必须带 UA/Referer，否则 403。"""
    headers: dict[str, str] = {
        "User-Agent": UA,
        "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
        "Referer": "https://doi.org/" + doi,
    }
    req: urllib.request.Request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def try_oa_download(paper: Paper, outdir: str, email: str, budget: float = 150.0) -> tuple[str, str, str]:
    """-> (全文绝对路径, 获取方式 provided/unpaywall/pmc/none, 说明)。

    budget 是单篇总时间预算：单请求 timeout 仍为 120s，但遇到 TLS 握手挂死的站点时
    不再让它独占 2 分钟，避免 N 篇 × 6 候选退化成十几分钟。
    """
    deadline: float = time.monotonic() + budget
    doi: str = str(paper.get("doi") or "").strip()
    if not doi:
        return "", "none", "无 DOI，无法走 Unpaywall；需人工提供全文"

    cands: list[str]
    hosts: dict[str, str]
    err: str
    cands, hosts, err = unpaywall_candidates(doi, email)
    if err:
        return "", "none", err
    if not cands:
        return "", "none", "Unpaywall 无候选链接"

    errs: list[str] = []
    data: bytes = b""
    used: str = ""
    u: str
    for u in cands:
        left: float = deadline - time.monotonic()
        if left < 5.0:
            errs.append("单篇超时预算耗尽")
            break
        raw: bytes
        try:
            raw = fetch_bytes(u, doi, int(min(120, max(10, left))))
        except urllib.error.HTTPError as e:
            errs.append(f"HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)
            continue
        if raw[:4] != b"%PDF":  # PMC 常回 ~1.8KB 反爬 HTML，DOAJ/Cloudflare 可能回几百 KB 拦截页
            errs.append("魔数不符")
            continue
        if len(raw) < MIN_PDF_BYTES:
            errs.append(f"尺寸过小({len(raw)}B)")
            continue
        data, used = raw, u
        break

    if not used:
        detail: str = "/".join(list(dict.fromkeys(errs))) if errs else "无候选"
        return "", "none", f"全部候选失败：{detail}"

    name: str = safe_name(doi or str(paper.get("pmid") or ""))
    if not name:
        name = safe_name(_dl.slugify(str(paper.get("title") or ""), 60) if _dl is not None else "untitled")
    os.makedirs(outdir, exist_ok=True)
    fpath: str = os.path.join(outdir, name + ".pdf")
    with open(fpath, "wb") as f:
        f.write(data)

    source: str = "pmc" if ("/pmc/articles/PMC" in used or "ncbi.nlm.nih.gov" in used) else "unpaywall"
    note: str = f"Unpaywall {hosts.get(used, 'oa')} 直链（{len(data) // 1024} KB）"
    return os.path.abspath(fpath), source, note


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def write_manifest(rows: list[dict[str, str]], out: str) -> None:
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w: Any = csv.DictWriter(f, fieldnames=MANIFEST_HEADER)
        w.writeheader()
        row: dict[str, str]
        for row in rows:
            w.writerow(row)


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description="复筛全文准备器：索引用户提供全文 + Unpaywall OA 补全 + 输出 manifest"
    )
    ap.add_argument("--input", required=True, help="pool JSON（pubmed.py/screen.py 产物）")
    ap.add_argument("--dir", default=".", help="任务目录；--outdir 与 --out 的相对路径基于它解析")
    ap.add_argument("--provide", action="extend", nargs="*", default=[],
                    help="用户提供的全文文件或目录（可多个，目录递归）")
    ap.add_argument("--outdir", default="papers/", help="下载 PDF 的保存目录（相对 --dir）")
    ap.add_argument("--email", default=DEFAULT_EMAIL,
                    help="Unpaywall 要求的真实联系邮箱；也可用环境变量 UNPAYWALL_EMAIL。占位邮箱会被拒(422)")
    ap.add_argument("--no-download", action="store_true", help="只索引已提供全文，不走网络")
    ap.add_argument("--out", required=True, help="manifest CSV 输出路径")
    args: argparse.Namespace = ap.parse_args(argv)

    # Unpaywall 强制要求真实邮箱：占位地址会返回 HTTP 422，白白浪费每篇的超时时间
    email: str = (args.email or "").strip() or (os.environ.get("UNPAYWALL_EMAIL") or "").strip()
    skip_reason: str = ""
    if email == PLACEHOLDER_EMAIL or email.endswith("example.com") or not email:
        skip_reason = "未提供真实邮箱（用 --email 或环境变量 UNPAYWALL_EMAIL）；Unpaywall 拒绝占位邮箱(422)，已跳过网络获取"
        if not args.no_download:
            print(f"[fulltext] 警告：{skip_reason}", file=sys.stderr)

    papers: list[Paper] = load_papers(args.input)
    provided: dict[int, tuple[str, int, str]] = index_provided(papers, list(args.provide or []))
    outdir: str = args.outdir if os.path.isabs(args.outdir) else os.path.join(args.dir, args.outdir)
    out_path: str = args.out if os.path.isabs(args.out) else os.path.join(args.dir, args.out)

    rows: list[dict[str, str]] = []
    n_provided: int = 0
    n_downloaded: int = 0

    i: int
    p: Paper
    for i, p in enumerate(papers):
        path: str = ""
        source: str = "none"
        note: str = ""
        hit: tuple[str, int, str] | None = provided.get(i)
        if hit is not None:
            path, source = hit[0], "provided"
            note = f"用户提供（{hit[2]}）"
            n_provided += 1
        elif args.no_download:
            note = "未提供全文，且本次 --no-download 跳过网络获取"
        elif skip_reason:
            note = skip_reason
        else:
            path, source, note = try_oa_download(p, outdir, email)
            if source != "none":
                n_downloaded += 1
            time.sleep(0.6)  # 温和限速
        rows.append({
            "序号": str(i + 1),
            "PMID": str(p.get("pmid") or ""),
            "题目": str(p.get("title") or ""),
            "DOI": str(p.get("doi") or ""),
            "全文文件名": os.path.basename(path) if path else "",
            "全文路径": path,
            "是否可判全文": "是" if (path and os.path.isfile(path)) else "否",
            "获取方式": source,
            "说明": note,
        })
        print(f"[fulltext] ({i + 1}/{len(papers)}) PMID {p.get('pmid') or '-'}: {source} {note}", file=sys.stderr)

    n_none: int = sum(1 for r in rows if r["是否可判全文"] == "否")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    write_manifest(rows, out_path)

    print(f"[fulltext] 共 {len(papers)} 篇 / 用户全文 {n_provided} / 本次下载 {n_downloaded} / 仍无全文 {n_none}",
          file=sys.stderr)
    print(f"[fulltext] manifest 写入 {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
