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
import json
import re
import sys


def load_papers(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
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


def build_matcher(term: str, regex: bool, case_sensitive: bool):
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        pattern = re.compile(term, flags)
        return lambda text: bool(pattern.search(text))
    else:
        target = term if case_sensitive else term.lower()
        return lambda text: target in (text if case_sensitive else text.lower())


def get_text(paper: dict, field: str) -> str:
    parts = []
    if field in ("title", "title+abstract"):
        parts.append(paper.get("title", ""))
    if field in ("abstract", "title+abstract"):
        parts.append(paper.get("abstract", ""))
    return "\n".join(parts)


def screen_paper(
    paper: dict,
    includes: list[str],
    excludes: list[str],
    field: str,
    mode: str,
    regex: bool,
    case_sensitive: bool,
) -> tuple[str, str]:
    text = get_text(paper, field)

    # 1. exclude 优先
    for term in excludes:
        if build_matcher(term, regex, case_sensitive)(text):
            return "exclude", f"{field} 命中排除词: {term}"

    # 2. include
    if includes:
        hits = [term for term in includes if build_matcher(term, regex, case_sensitive)(text)]
        if mode == "and" and len(hits) == len(includes):
            return "include", f"{field} 命中全部纳入词: {', '.join(hits)}"
        if mode == "or" and hits:
            return "include", f"{field} 命中纳入词: {', '.join(hits)}"

    # 3. uncertain
    return "uncertain", f"{field} 未命中任何纳入/排除词"


def main(argv: list[str] | None = None) -> int:
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
    args = ap.parse_args(argv)

    papers = load_papers(args.input)
    includes = split_terms(args.include)
    excludes = split_terms(args.exclude)

    if not includes and not excludes:
        print("[screen] 必须至少提供 --include 或 --exclude", file=sys.stderr)
        return 2

    counts = {"include": 0, "exclude": 0, "uncertain": 0}
    screened: list[dict] = []

    for p in papers:
        decision, reason = screen_paper(
            p, includes, excludes, args.field, args.mode, args.regex, args.case_sensitive
        )
        if decision == "uncertain" and args.uncertain_as == "exclude":
            decision = "exclude"
            reason = "未命中纳入词，按 --uncertain-as exclude 排除"

        entry = dict(p)
        entry["decision"] = {
            "round1": decision,
            "reason": reason,
            "round2": None,
            "reason2": "",
        }
        screened.append(entry)
        counts[decision] += 1

    result = {
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

    print(f"[screen] 共 {len(papers)} 篇：include={counts['include']}, exclude={counts['exclude']}, uncertain={counts['uncertain']}", file=sys.stderr)
    print(f"[screen] 结果写入 {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
