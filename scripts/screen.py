#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screen.py — 基于摘要/标题关键词的文献初筛/复筛脚本

作用：在用户逐篇精读之前，先把明显符合 / 明显不符合的文献筛出来，
生成带 decision 字段的 screening.json，作为 LLM 或人工复筛的输入。

筛选规则（优先级从高到低）：
  1. 命中任一 --exclude 关键词 -> exclude
  2. 命中全部（--mode and）或任一（--mode or）--include 关键词 -> include
  3. 其余 -> uncertain（默认）或 exclude（--uncertain-as exclude）

关键词大小写不敏感；可用 --regex 启用正则；可用 --field title 只看标题。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export import FIELD_LABELS, best_url, cell_value  # type: ignore

MODES: tuple[str, ...] = ("prepare", "apply")


def load_papers(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data: Any = json.load(f)
    if isinstance(data, list):
        return data
    for key in ("papers", "included", "results"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    raise ValueError("输入 JSON 中找不到论文数组（papers/included/results）")


def split_terms(text: str | None) -> list[str]:
    if not text:
        return []
    return [t.strip() for t in text.split(",") if t.strip()]


def build_matcher(term: str, regex: bool, case_sensitive: bool) -> Callable[[str], bool]:
    flags: int = 0 if case_sensitive else re.IGNORECASE
    if regex:
        pattern: re.Pattern[str] = re.compile(term, flags)
        return lambda text: bool(pattern.search(text))
    else:
        target: str = term if case_sensitive else term.lower()
        return lambda text: target in (text if case_sensitive else text.lower())


def get_text(paper: dict[str, Any], field: str) -> str:
    parts: list[str] = []
    if field in ("title", "title+abstract"):
        parts.append(paper.get("title", ""))
    if field in ("abstract", "title+abstract"):
        parts.append(paper.get("abstract", ""))
    return "\n".join(parts)


def screen_paper(
    paper: dict[str, Any],
    includes: list[str],
    excludes: list[str],
    field: str,
    mode: str,
    regex: bool,
    case_sensitive: bool,
) -> tuple[str, str]:
    text: str = get_text(paper, field)
    title: str = paper.get("title", "")

    # 1. exclude 优先
    for term in excludes:
        if build_matcher(term, regex, case_sensitive)(text):
            return "exclude", f"{field} 命中排除词: {term}"

    # 2. include
    if includes:
        hits: list[str] = [term for term in includes if build_matcher(term, regex, case_sensitive)(text)]
        if mode == "and" and len(hits) == len(includes):
            return "include", f"{field} 命中全部纳入词: {', '.join(hits)}"
        if mode == "or" and hits:
            return "include", f"{field} 命中纳入词: {', '.join(hits)}"

    # 3. uncertain
    return "uncertain", f"{field} 未命中任何纳入/排除词"


def main(argv: list[str] | None = None) -> int:
    """分发：screen（默认，关键词预筛）/ prepare（出待填审查表）/ apply（回填决策）。"""
    raw: list[str] = list(sys.argv[1:] if argv is None else argv)
    mode: str = "screen"
    rest: list[str] = raw
    if raw and raw[0] in MODES:
        mode = raw[0]
        rest = raw[1:]
    if mode == "prepare":
        return main_prepare(rest)
    if mode == "apply":
        return main_apply(rest)
    return main_screen(rest)


def main_screen(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="基于摘要/标题关键词的文献初筛/复筛")
    ap.add_argument("--input", required=True, help="pubmed.py / import.py 产出的 JSON 路径")
    ap.add_argument("--include", default=None, help="纳入词，逗号分隔；--mode and 要求全中，--mode or 任一中")
    ap.add_argument("--exclude", default=None, help="排除词，逗号分隔；任一命中即排除")
    ap.add_argument("--field", default="title+abstract", choices=["title", "abstract", "title+abstract"],
                    help="匹配范围（默认 title+abstract）")
    ap.add_argument("--mode", default="and", choices=["and", "or"], help="多个 include 词的关系（默认 and）")
    ap.add_argument("--regex", action="store_true", help="把关键词当正则表达式")
    ap.add_argument("--case-sensitive", action="store_true", help="大小写敏感匹配")
    ap.add_argument("--uncertain-as", default="uncertain", choices=["uncertain", "exclude"],
                    help="未命中纳入/排除的论文标记为 uncertain 还是 exclude（默认 uncertain）")
    ap.add_argument("--reason", default="keyword", choices=["keyword", "picos"],
                    help="理由风格（keyword=命中词；picos=占位，供后续人工补 PICOS 理由）")
    ap.add_argument("--out", required=True, help="输出 JSON 路径（含 decision 字段）")
    ap.add_argument("--no-csv", action="store_true", help="不生成同名 CSV 审查表")
    args: argparse.Namespace = ap.parse_args(argv)

    papers: list[dict[str, Any]] = load_papers(args.input)
    includes: list[str] = split_terms(args.include)
    excludes: list[str] = split_terms(args.exclude)

    if not includes and not excludes:
        print("[screen] 必须至少提供 --include 或 --exclude", file=sys.stderr)
        return 2

    counts: dict[str, int] = {"include": 0, "exclude": 0, "uncertain": 0}
    screened: list[dict[str, Any]] = []

    for p in papers:
        decision, reason = screen_paper(
            p, includes, excludes, args.field, args.mode, args.regex, args.case_sensitive
        )
        if decision == "uncertain" and args.uncertain_as == "exclude":
            decision = "exclude"
            reason = "未命中纳入词，按 --uncertain-as exclude 排除"

        entry: dict[str, Any] = dict(p)
        entry["decision"] = {
            "round1": decision,
            "reason": reason,
            "round2": None,
            "reason2": "",
        }
        screened.append(entry)
        counts[decision] += 1

    result: dict[str, Any] = {
        "source": "screen",
        "input": args.input,
        "criteria": {
            "include": includes,
            "exclude": excludes,
            "field": args.field,
            "mode": args.mode,
            "regex": args.regex,
            "case_sensitive": args.case_sensitive,
            "uncertain_as": args.uncertain_as,
        },
        "count": len(papers),
        "summary": counts,
        "papers": screened,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 默认生成同名 CSV 审查表，便于人工复核
    if not args.no_csv:
        csv_path: str = os.path.splitext(args.out)[0] + ".csv"
        _write_screening_csv(screened, csv_path)
        print(f"[screen] CSV 审查表写入 {csv_path}", file=sys.stderr)

    print(f"[screen] 共 {len(papers)} 篇：include={counts['include']}, exclude={counts['exclude']}, uncertain={counts['uncertain']}", file=sys.stderr)
    print(f"[screen] 结果写入 {args.out}", file=sys.stderr)
    return 0


_CSV_FIELDS: list[str] = ["pmid", "title", "authors", "journal", "date", "keywords", "mesh", "abstract", "doi", "url"]
_EXTRA_FIELDS: list[str] = ["round1", "reason", "round2", "reason2"]


def _decision_text(paper: dict[str, Any]) -> dict[str, str]:
    decision: Any = paper.get("decision") or {}
    if isinstance(decision, dict):
        return {
            "round1": decision.get("round1", ""),
            "reason": decision.get("reason", ""),
            "round2": decision.get("round2") or "",
            "reason2": decision.get("reason2") or "",
        }
    return {"round1": str(decision), "reason": "", "round2": "", "reason2": ""}


def _write_screening_csv(papers: list[dict[str, Any]], out: str) -> None:
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w: Any = csv.writer(f)
        header: list[str] = [FIELD_LABELS.get(f, f) for f in _CSV_FIELDS + _EXTRA_FIELDS]
        w.writerow(header)
        for p in papers:
            row: list[str] = [cell_value(p, f) for f in _CSV_FIELDS]
            d: dict[str, str] = _decision_text(p)
            row.extend([d.get(f, "") for f in _EXTRA_FIELDS])
            w.writerow(row)


# ---------------------------------------------------------------------------
# prepare / apply：可人工复核的审查表（v1.5）
#
#   prepare  生成"决策/理由"留空的 CSV，交给人或 LLM 逐篇填
#   apply    把填好的 CSV 回填成 paper["decision"]，产出带 decision 的 JSON
# ---------------------------------------------------------------------------

_REVIEW_FIELDS: list[str] = ["pmid", "title", "authors", "journal", "date", "pub_types", "keywords", "abstract", "doi"]

_DECISION_ALIASES: dict[str, str] = {
    "include": "include", "inc": "include", "1": "include", "y": "include", "yes": "include", "纳入": "include",
    "exclude": "exclude", "exc": "exclude", "0": "exclude", "n": "exclude", "no": "exclude", "排除": "exclude",
    "uncertain": "uncertain", "unc": "uncertain", "?": "uncertain", "？": "uncertain", "待定": "uncertain",
}


def round_label(round_no: int) -> str:
    if round_no == 1:
        return "初筛"
    if round_no == 2:
        return "复筛"
    return f"第{round_no}轮"


def normalize_decision(raw: str) -> str:
    """中英文/缩写/数字都接受；无法识别返回空串（调用方按未填写处理）。"""
    return _DECISION_ALIASES.get((raw or "").strip().lower(), "")


def write_review_table(papers: list[dict[str, Any]], out: str, round_no: int) -> int:
    """写出待填写的审查表；决策列与理由列留空。返回写入行数。"""
    label: str = round_label(round_no)
    header: list[str] = ["序号", "PMID", "题目", "作者", "期刊", "发表日期", "文献类型", "关键词", "摘要", "DOI", "网址",
                         f"{label}决策", f"{label}理由"]
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w: Any = csv.writer(f)
        w.writerow(header)
        i: int
        p: dict[str, Any]
        for i, p in enumerate(papers, 1):
            row: list[str] = [str(i)]
            fld: str
            for fld in _REVIEW_FIELDS:
                row.append(cell_value(p, fld))
            row.append(best_url(p))
            row.append("")  # 决策：留给人工/LLM 填
            row.append("")  # 理由
            w.writerow(row)
    return len(papers)


def _norm_key(s: str) -> str:
    """标题匹配用的归一化：小写 + 只保留字母数字与汉字。"""
    return re.sub(r"[^\w\u4e00-\u9fff]", "", (s or "").lower())


def _norm_doi_key(s: str) -> str:
    out: str = (s or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if out.startswith(prefix):
            out = out[len(prefix):]
            break
    return out


def pick_column(header: list[str], keyword: str, label: str) -> int:
    """优先取带本轮标签的列（如"复筛决策"），否则退回首个含关键词的列。"""
    i: int
    h: str
    for i, h in enumerate(header):
        if keyword in (h or "") and label in (h or ""):
            return i
    for i, h in enumerate(header):
        if keyword in (h or ""):
            return i
    return -1


def apply_decision(paper: dict[str, Any], round_no: int, value: str, reason: str) -> None:
    """写入 decision，保留其它轮次字段（先 apply round1 再 apply round2 不互相覆盖）。"""
    old: Any = paper.get("decision")
    dec: dict[str, Any] = dict(old) if isinstance(old, dict) else {}
    if not isinstance(old, dict) and old:
        dec = {"round1": str(old)}
    dec.setdefault("round1", "")
    dec.setdefault("reason", "")
    dec.setdefault("round2", None)
    dec.setdefault("reason2", "")
    if round_no == 1:
        dec["round1"] = value
        dec["reason"] = reason
    else:
        dec[f"round{round_no}"] = value
        dec[f"reason{round_no}"] = reason
    paper["decision"] = dec


def find_paper(
    row: dict[str, str],
    header: list[str],
    by_pmid: dict[str, dict[str, Any]],
    by_doi: dict[str, dict[str, Any]],
    by_title: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """按 PMID > DOI > 标题 的顺序匹配回原 paper。"""
    val: str = ""
    h: str
    for h in header:
        if h and "PMID" in h.upper():
            val = (row.get(h) or "").strip()
            break
    if val and val in by_pmid:
        return by_pmid[val]
    for h in header:
        if h and "DOI" in h.upper():
            val = (row.get(h) or "").strip()
            break
    key: str = _norm_doi_key(val)
    if key and key in by_doi:
        return by_doi[key]
    for h in header:
        if h and "题目" in h:
            val = (row.get(h) or "").strip()
            break
    tkey: str = _norm_key(val)
    if tkey and tkey in by_title:
        return by_title[tkey]
    return None


def main_prepare(argv: list[str]) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description="生成待填写的审查表 CSV（决策/理由列留空）"
    )
    ap.add_argument("--input", required=True, help="pool JSON 路径")
    ap.add_argument("--out", required=True, help="审查表 CSV 输出路径")
    ap.add_argument("--round", type=int, default=1, help="轮次：1=初筛，2=复筛（默认 1）")
    args: argparse.Namespace = ap.parse_args(argv)

    papers: list[dict[str, Any]] = load_papers(args.input)
    if not papers:
        print("[screen] prepare: 输入中无论文", file=sys.stderr)
        return 1
    n: int = write_review_table(papers, args.out, args.round)
    print(f"[screen] prepare: 写入 {n} 行（{round_label(args.round)}审查表，决策/理由列留空）-> {args.out}",
          file=sys.stderr)
    return 0


def main_apply(argv: list[str]) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description="把填写好的审查表 CSV 回填为带 decision 的 JSON"
    )
    ap.add_argument("--input", required=True, help="pool JSON 路径（与 prepare 同源）")
    ap.add_argument("--review", required=True, help="填写好的审查表 CSV")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--round", type=int, default=1, help="轮次：1=初筛，2=复筛（默认 1）")
    args: argparse.Namespace = ap.parse_args(argv)

    papers: list[dict[str, Any]] = load_papers(args.input)
    by_pmid: dict[str, dict[str, Any]] = {}
    by_doi: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    p: dict[str, Any]
    for p in papers:
        pmid: str = str(p.get("pmid") or "").strip()
        if pmid:
            by_pmid.setdefault(pmid, p)
        doi: str = _norm_doi_key(str(p.get("doi") or ""))
        if doi:
            by_doi.setdefault(doi, p)
        tk: str = _norm_key(str(p.get("title") or ""))
        if tk:
            by_title.setdefault(tk, p)

    with open(args.review, "r", encoding="utf-8-sig", newline="") as f:
        reader: Any = csv.DictReader(f)
        header: list[str] = list(reader.fieldnames or [])
        rows: list[dict[str, str]] = [dict(r) for r in reader]

    label: str = round_label(args.round)
    dec_i: int = pick_column(header, "决策", label)
    rea_i: int = pick_column(header, "理由", label)
    if dec_i < 0:
        print(f"[screen] apply: 审查表里找不到决策列（表头：{header}）", file=sys.stderr)
        return 2
    dec_col: str = header[dec_i]
    rea_col: str = header[rea_i] if rea_i >= 0 else ""

    counts: dict[str, int] = {"include": 0, "exclude": 0, "uncertain": 0, "unfilled": 0}
    unmatched: int = 0
    row: dict[str, str]
    for row in rows:
        raw: str = (row.get(dec_col) or "").strip()
        if not raw:
            counts["unfilled"] += 1
            continue
        value: str = normalize_decision(raw)
        if not value:
            print(f"[screen] apply: 无法识别的决策值，按未填写跳过：{raw!r}", file=sys.stderr)
            counts["unfilled"] += 1
            continue
        hit: dict[str, Any] | None = find_paper(row, header, by_pmid, by_doi, by_title)
        if hit is None:
            unmatched += 1
            print(f"[screen] apply: 未匹配到原文献，已跳过：{row.get('PMID', '')} {row.get('题目', '')[:30]}",
                  file=sys.stderr)
            continue
        apply_decision(hit, args.round, value, (row.get(rea_col) or "").strip() if rea_col else "")
        counts[value] += 1

    result: dict[str, Any] = {
        "source": "screen",
        "round": args.round,
        "counts": counts,
        "papers": papers,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[screen] apply: round{args.round} include={counts['include']}, exclude={counts['exclude']}, "
          f"uncertain={counts['uncertain']}, unfilled={counts['unfilled']}", file=sys.stderr)
    if counts["unfilled"]:
        print(f"[screen] apply: 有 {counts['unfilled']} 行决策为空或无法识别，已跳过、未改动这些文献的 decision",
              file=sys.stderr)
    if unmatched:
        print(f"[screen] apply: {unmatched} 行未能匹配回原文献", file=sys.stderr)
    print(f"[screen] apply: 结果写入 {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
