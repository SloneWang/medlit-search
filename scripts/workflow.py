#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
workflow.py — 把检索/初筛/复筛结果一键导出为可审查 CSV

输入一个任务目录（含 results.json、screening.json、selected.json 等），
输出三个 CSV 文件：
  all_papers.csv        检索到的全部文献
  screening_round1.csv  初筛结果（含 decision 与 reason）
  selected.csv          最终纳入文献（优先用 selected.json，否则取 screening 中 include）

字段：PMID、题目、作者、期刊、发表日期、关键词、MeSH主题词、摘要、DOI、网址、
      初筛决策、初筛理由、复筛决策、复筛理由。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any

# 复用 export.py 的字段标签与取值逻辑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export import FIELD_LABELS, FIELDS, cell_value  # type: ignore

_CSV_FIELDS = [
    "pmid", "title", "authors", "journal", "date",
    "keywords", "mesh", "abstract", "doi", "url",
]

_EXTRA_FIELDS = ["round1", "reason", "round2", "reason2"]


def _load_json(path: str) -> dict[str, Any] | list[Any] | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _extract_papers(data: Any) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("papers", "included", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _decision_text(paper: dict) -> dict[str, str]:
    """从 paper['decision'] 提取可读的决策字段。"""
    decision = paper.get("decision") or {}
    if isinstance(decision, dict):
        return {
            "round1": decision.get("round1", ""),
            "reason": decision.get("reason", ""),
            "round2": decision.get("round2") or "",
            "reason2": decision.get("reason2") or "",
        }
    return {"round1": str(decision), "reason": "", "round2": "", "reason2": ""}


def write_papers_csv(papers: list[dict], out: str, include_decision: bool = False) -> None:
    """导出统一 paper 列表到 CSV。"""
    fields = list(_CSV_FIELDS)
    if include_decision:
        fields.extend(_EXTRA_FIELDS)

    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        header = [FIELD_LABELS.get(f, f) for f in fields]
        w.writerow(header)
        for p in papers:
            row = [cell_value(p, f) for f in _CSV_FIELDS]
            if include_decision:
                d = _decision_text(p)
                row.extend([d.get(f, "") for f in _EXTRA_FIELDS])
            w.writerow(row)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="检索/初筛/复筛结果导出为审查 CSV")
    ap.add_argument("--dir", required=True, help="任务目录路径（含 results.json / screening.json / selected.json）")
    ap.add_argument("--outdir", default=None, help="CSV 输出目录，默认与 --dir 相同")
    args = ap.parse_args(argv)

    task_dir = args.dir
    outdir = args.outdir or task_dir
    os.makedirs(outdir, exist_ok=True)

    results_path = os.path.join(task_dir, "results.json")
    screening_path = os.path.join(task_dir, "screening.json")
    selected_path = os.path.join(task_dir, "selected.json")

    # 1) 全部文献
    results_data = _load_json(results_path)
    all_papers = _extract_papers(results_data) if results_data else []
    if all_papers:
        out_all = os.path.join(outdir, "all_papers.csv")
        write_papers_csv(all_papers, out_all)
        print(f"[workflow] 全部文献 {len(all_papers)} 篇 -> {out_all}", file=sys.stderr)

    # 2) 初筛结果
    screening_data = _load_json(screening_path)
    screened_papers = _extract_papers(screening_data) if screening_data else []
    if screened_papers:
        out_screen = os.path.join(outdir, "screening_round1.csv")
        write_papers_csv(screened_papers, out_screen, include_decision=True)
        print(f"[workflow] 初筛结果 {len(screened_papers)} 篇 -> {out_screen}", file=sys.stderr)

    # 3) 最终纳入
    selected_papers: list[dict] = []
    selected_data = _load_json(selected_path)
    if selected_data:
        selected_papers = _extract_papers(selected_data)
    elif screened_papers:
        selected_papers = [
            p for p in screened_papers
            if (_decision_text(p).get("round2") == "include")
            or (_decision_text(p).get("round1") == "include" and not _decision_text(p).get("round2"))
        ]
    if selected_papers:
        out_selected = os.path.join(outdir, "selected.csv")
        write_papers_csv(selected_papers, out_selected)
        print(f"[workflow] 最终纳入 {len(selected_papers)} 篇 -> {out_selected}", file=sys.stderr)

    if not any([all_papers, screened_papers, selected_papers]):
        print("[workflow] 未找到可导出的结果文件", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
