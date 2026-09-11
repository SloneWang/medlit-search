#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge.py — 多来源文献汇总 + 去重（medlit-search v1.5）

把任意多个来源合并成一个统一池：
  - PubMed 检索结果（pubmed.py 产出的统一 JSON）
  - CNKI / 万方 / 维普 导出的表格（csv / xlsx，含 GBK 编码）
  - RIS / BibTeX / MEDLINE 题录
  - 用户手工追加的 JSON / CSV

去重优先级：归一化 DOI → PMID → 归一化标题 + 年份；三者皆无的记录原样保留。
同键合并规则：str 字段取先到的非空值；list 字段取并集去重保序。

用法：
  python merge.py --input <path1> [<path2> ...] [--format auto] \
                  --out pool.json [--csv pool.csv] [--dedupe-log dedupe.log]
  python merge.py --input ./literature_dir --out pool.json

--input 支持目录（递归扫描 .json/.csv/.xlsx/.ris/.bib/.txt/.medline）。
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import re
import sys
import time
from typing import Any

# "import" 是 Python 关键字，不能用普通 import 语句，动态加载同目录的 import.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_imp: Any = importlib.import_module("import")

Paper = dict[str, Any]

_SCAN_EXTS: tuple[str, ...] = (
    ".json", ".jsonl", ".csv", ".xlsx", ".xlsm", ".ris", ".bib", ".bibtex", ".txt", ".medline", ".nbib",
)

_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk", "gb18030")

_DOI_PREFIX_RE: re.Pattern[str] = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_NON_ALNUM_RE: re.Pattern[str] = re.compile(r"[^\w\u4e00-\u9fff]")
_YEAR_RE: re.Pattern[str] = re.compile(r"(?:19|20|21)\d{2}")


# ---------------------------------------------------------------------------
# 输入收集与加载
# ---------------------------------------------------------------------------

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


def collect_inputs(inputs: list[str]) -> list[str]:
    """把 --input 给定的文件/目录展开成去重保序的文件列表。"""
    files: list[str] = []
    item: str
    for item in inputs:
        if os.path.isdir(item):
            root: str
            dirs: list[str]
            names: list[str]
            for root, dirs, names in os.walk(item):
                dirs.sort()
                name: str
                for name in sorted(names):
                    ext: str = os.path.splitext(name)[1].lower()
                    if ext in _SCAN_EXTS:
                        files.append(os.path.join(root, name))
        elif os.path.isfile(item):
            files.append(item)
        else:
            print(f"[merge][warn] 路径不存在，已跳过：{item}", file=sys.stderr)

    seen: set[str] = set()
    out: list[str] = []
    path: str
    for path in files:
        key: str = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def load_file(path: str, fmt: str = "auto") -> list[Paper]:
    """按格式加载单个来源文件，返回规范化后的 Paper 列表。"""
    real_fmt: str = fmt if fmt and fmt != "auto" else str(_imp.detect_format(path))
    if real_fmt == "csv":
        return list(_imp.parse_csv(path))
    if real_fmt == "json":
        return list(_imp.load_json_papers(path))
    if real_fmt == "xlsx":
        return list(_imp.parse_xlsx(path))
    text: str = _read_text(path)
    if real_fmt == "ris":
        return list(_imp.parse_ris(text))
    if real_fmt == "bibtex":
        return list(_imp.parse_bibtex(text))
    return list(_imp.parse_medline_import(text))


# ---------------------------------------------------------------------------
# 去重键
# ---------------------------------------------------------------------------

def _norm_doi(s: str) -> str:
    """归一化 DOI：去空白、转小写、剥离 doi.org / doi: 前缀。"""
    v: str = (s or "").strip().lower()
    v = _DOI_PREFIX_RE.sub("", v)
    return v.strip().rstrip(".")


def _norm_title(s: str) -> str:
    """归一化标题：转小写并删除所有非字母数字字符（保留中文）。"""
    return _NON_ALNUM_RE.sub("", (s or "").lower()).strip()


def _year_of(date: str) -> str:
    """从任意日期串中取出 4 位年份，取不到返回 ""。"""
    m: re.Match[str] | None = _YEAR_RE.search(date or "")
    return m.group(0) if m else ""


def dedup_key(p: Paper) -> tuple[str, str]:
    """返回优先级最高的主键 (主键类型, 主键值)；无可用主键返回 ("", "")。"""
    keys: list[str] = candidate_keys(p)
    if not keys:
        return ("", "")
    kind: str
    value: str
    kind, value = keys[0].split(":", 1)
    return (kind, value)


def candidate_keys(p: Paper) -> list[str]:
    """按 DOI → PMID → 标题+年份 生成候选主键列表（优先级从高到低）。

    一条记录可能同时具备多种标识（如 CNKI 记录只有 DOI、PubMed 记录只有 PMID），
    因此命中任一候选键即视为同一篇文献，匹配时仍按上述优先级顺序尝试。
    """
    keys: list[str] = []
    doi: str = _norm_doi(str(p.get("doi", "") or ""))
    if doi:
        keys.append(f"doi:{doi}")
    pmid: str = str(p.get("pmid", "") or "").strip()
    if pmid.isdigit():
        keys.append(f"pmid:{pmid}")
    title: str = _norm_title(str(p.get("title", "") or ""))
    if title:
        keys.append(f"title:{title}|{_year_of(str(p.get('date', '') or ''))}")
    return keys


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------

def _union(a: list[Any], b: list[Any]) -> list[str]:
    """两个 list 取并集，去重保序，忽略空串。"""
    seen: set[str] = set()
    out: list[str] = []
    item: Any
    for item in list(a) + list(b):
        s: str = str(item).strip() if item is not None else ""
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _brief(p: Paper) -> str:
    """日志用的一句话摘要：标题前 40 字 + PMID/DOI。"""
    title: str = str(p.get("title", "") or "").strip()[:40]
    pmid: str = str(p.get("pmid", "") or "").strip()
    doi: str = str(p.get("doi", "") or "").strip()
    tails: list[str] = []
    if pmid:
        tails.append(f"PMID={pmid}")
    if doi:
        tails.append(f"DOI={doi}")
    tail: str = ",".join(tails)
    return f"《{title}》" + (f"({tail})" if tail else "")


def merge_into(base: Paper, extra: Paper) -> None:
    """把 extra 就地合并进 base：str 字段先到先用，list 字段取并集。"""
    key: str
    for key in _imp.CANON_STR_KEYS:
        old: str = str(base.get(key, "") or "").strip()
        new: str = str(extra.get(key, "") or "").strip()
        if new and not old:
            base[key] = new
    for key in _imp.CANON_LIST_KEYS:
        merged: list[str] = _union(list(base.get(key, []) or []), list(extra.get(key, []) or []))
        base[key] = merged
    base["sources"] = _union(list(base.get("sources", []) or []), list(extra.get("sources", []) or []))
    base["merged_from"] = int(base.get("merged_from", 1) or 1) + int(extra.get("merged_from", 1) or 1)
    # decision 是筛选流程自定义字段（不在规范字段表里）：迭代更新时避免丢掉已有决策
    dec: Any = extra.get("decision")
    if dec and not base.get("decision"):
        base["decision"] = dec


def dedupe(papers: list[Paper]) -> tuple[list[Paper], int, list[str]]:
    """去重。返回 (合并后列表, 被移除数量, 去重日志行)。"""
    index: dict[str, Paper] = {}
    kept_list: list[Paper] = []
    unkeyed: list[Paper] = []
    removed: int = 0
    log: list[str] = []

    p: Paper
    for p in papers:
        keys: list[str] = candidate_keys(p)
        if not keys:
            unkeyed.append(p)
            continue

        target: Paper | None = None
        hit: str = ""
        key: str
        for key in keys:  # 按 DOI → PMID → 标题 的优先级顺序尝试命中
            if key in index:
                target = index[key]
                hit = key
                break

        if target is None:
            p["merged_from"] = 1
            for key in keys:
                index[key] = p
            kept_list.append(p)
            continue

        kind: str = hit.split(":", 1)[0]
        log.append(f"[{kind}] key={hit.split(':', 1)[1]} 合并：保留 {_brief(target)} <- 丢弃 {_brief(p)}")
        merge_into(target, p)
        for key in keys:  # 补充登记新主键，便于后续记录命中
            index.setdefault(key, target)
        removed += 1

    result: list[Paper] = kept_list + unkeyed
    return result, removed, log


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

_CSV_HEADER: list[str] = ["序号", "PMID", "题目", "作者", "期刊", "发表日期", "关键词", "摘要", "DOI", "网址", "来源文件"]


def write_csv(papers: list[Paper], path: str) -> None:
    """导出 Excel 可直接打开的 CSV（utf-8-sig）。"""
    fh: Any
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer: Any = csv.writer(fh)
        writer.writerow(_CSV_HEADER)
        idx: int = 0
        p: Paper
        for p in papers:
            idx += 1
            writer.writerow([
                idx,
                str(p.get("pmid", "") or ""),
                str(p.get("title", "") or ""),
                "; ".join(list(p.get("authors", []) or [])),
                str(p.get("journal", "") or ""),
                str(p.get("date", "") or ""),
                "; ".join(list(p.get("keywords", []) or [])),
                str(p.get("abstract", "") or ""),
                str(p.get("doi", "") or ""),
                str(p.get("url", "") or ""),
                "; ".join(list(p.get("sources", []) or [])),
            ])


def write_json(result: dict[str, Any], path: str) -> None:
    """写出统一池 JSON。"""
    fh: Any
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description="多来源文献汇总 + 去重")
    ap.add_argument("--input", nargs="+", required=True, help="输入文件或目录，可给多个")
    ap.add_argument(
        "--format",
        nargs="?",
        default="auto",
        choices=["ris", "bibtex", "medline", "json", "csv", "xlsx", "auto"],
        help="强制指定输入格式，默认 auto 逐个文件自动识别",
    )
    ap.add_argument("--out", required=True, help="输出池 JSON 路径")
    ap.add_argument("--csv", dest="csv_out", default="", help="可选：同时导出 CSV（utf-8-sig，Excel 可直接打开）")
    ap.add_argument("--dedupe-log", dest="dedupe_log", default="", help="可选：去重日志文本路径")
    args: argparse.Namespace = ap.parse_args(argv)

    files: list[str] = collect_inputs(list(args.input))
    if not files:
        print("[merge] 未找到任何输入文件", file=sys.stderr)
        return 1

    papers: list[Paper] = []
    sources_used: list[str] = []
    path: str
    for path in files:
        loaded: list[Paper] = load_file(path, str(args.format))
        base: str = os.path.basename(path)
        p: Paper
        for p in loaded:
            p["sources"] = [base]
            papers.append(p)
        if base not in sources_used:
            sources_used.append(base)
        print(f"[merge] 读入 {base}：{len(loaded)} 篇", file=sys.stderr)

    total: int = len(papers)
    deduped: list[Paper]
    removed: int
    log: list[str]
    deduped, removed, log = dedupe(papers)

    result: dict[str, Any] = {
        "source": "merge",
        "sources": sources_used,
        "count": len(deduped),
        "fetched": total,
        "duplicates_removed": removed,
        "merge_date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "papers": deduped,
    }
    write_json(result, args.out)
    print(f"[out] 已写入 {args.out}", file=sys.stderr)

    if args.csv_out:
        write_csv(deduped, str(args.csv_out))
        print(f"[out] 已写入 {args.csv_out}", file=sys.stderr)

    if args.dedupe_log:
        line: str
        with open(str(args.dedupe_log), "w", encoding="utf-8") as fh:
            for line in log:
                fh.write(line + "\n")
        print(f"[out] 已写入 {args.dedupe_log}", file=sys.stderr)

    print(f"[merge] 读入 {total} 篇 / 去重后 {len(deduped)} 篇 / 移除 {removed} 篇", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
