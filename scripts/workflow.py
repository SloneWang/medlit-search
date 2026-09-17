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
from typing import Any, TextIO

# 复用 export.py 的字段标签与取值逻辑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export import FIELD_LABELS, FIELDS, cell_value  # type: ignore

# 导出 CSV 的基础字段序列（顺序即列顺序）
_CSV_FIELDS: list[str] = [
    "pmid", "title", "authors", "journal", "date",
    "keywords", "mesh", "abstract", "doi", "url",
]

# 初筛/复筛追加的决策字段（paper['decision'] 中的键）
_EXTRA_FIELDS: list[str] = ["round1", "reason", "round2", "reason2"]


def _load_json(path: str) -> dict[str, Any] | list[Any] | None:
    """容错读取 JSON 文件。

    参数:
        path: 文件路径。

    返回:
        解析后的对象；文件不存在或解析失败返回 None。
    """
    if not os.path.exists(path):
        return None
    try:
        f: TextIO
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _extract_papers(data: Any) -> list[dict[str, Any]]:
    """从 JSON 数据中取出论文数组。

    参数:
        data: 解析后的 JSON 对象；支持纯数组或含 papers/included/results 键的对象。

    返回:
        论文字典列表；均不符合时返回空列表。
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("papers", "included", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _decision_text(paper: dict[str, Any]) -> dict[str, str]:
    """从 paper['decision'] 提取可读的决策字段。

    参数:
        paper: 论文字典；decision 为 dict 时逐项取 round1/reason/round2/reason2，
            否则把 decision 本身当作 round1 的字符串值。

    返回:
        含 round1/reason/round2/reason2 四个键的字典，缺失项为空串。
    """
    decision: Any = paper.get("decision") or {}
    if isinstance(decision, dict):
        return {
            "round1": decision.get("round1", ""),
            "reason": decision.get("reason", ""),
            "round2": decision.get("round2") or "",
            "reason2": decision.get("reason2") or "",
        }
    return {"round1": str(decision), "reason": "", "round2": "", "reason2": ""}


def write_papers_csv(papers: list[dict[str, Any]], out: str, include_decision: bool = False) -> None:
    """导出统一 paper 列表到 CSV（utf-8-sig）。

    参数:
        papers: 论文列表。
        out: 输出文件路径。
        include_decision: 为 True 时追加 round1/reason/round2/reason2 四列。

    返回:
        无。
    """
    fields: list[str] = list(_CSV_FIELDS)
    if include_decision:
        fields.extend(_EXTRA_FIELDS)

    f: TextIO
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w: Any = csv.writer(f)
        header: list[str] = [FIELD_LABELS.get(f, f) for f in fields]
        w.writerow(header)
        p: dict[str, Any]
        for p in papers:
            row: list[str] = [cell_value(p, f) for f in _CSV_FIELDS]
            if include_decision:
                d: dict[str, str] = _decision_text(p)
                row.extend([d.get(f, "") for f in _EXTRA_FIELDS])
            w.writerow(row)


def main(argv: list[str] | None = None) -> int:
    """命令行入口：把任务目录的检索/筛选结果导出为三份审查 CSV。

    三分支回退链：全部文献取 results.json；初筛结果取 screening.json；
    最终纳入优先 selected.json，否则从 screening 中取 round2 include，
    或 round1 include 且无 round2 的论文。

    参数:
        argv: 参数列表；None 时取 sys.argv。

    返回:
        0 成功；1 无任何可导出结果。
    """
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description="检索/初筛/复筛结果导出为审查 CSV")
    ap.add_argument("--dir", required=True, help="任务目录路径（含 results.json / screening.json / selected.json）")
    ap.add_argument("--outdir", default=None, help="CSV 输出目录，默认与 --dir 相同")
    args: argparse.Namespace = ap.parse_args(argv)

    task_dir: str = args.dir
    outdir: str = args.outdir or task_dir
    os.makedirs(outdir, exist_ok=True)

    results_path: str = os.path.join(task_dir, "results.json")
    screening_path: str = os.path.join(task_dir, "screening.json")
    selected_path: str = os.path.join(task_dir, "selected.json")

    # 1) 全部文献
    results_data: dict[str, Any] | list[Any] | None = _load_json(results_path)
    all_papers: list[dict[str, Any]] = _extract_papers(results_data) if results_data else []
    if all_papers:
        out_all: str = os.path.join(outdir, "all_papers.csv")
        write_papers_csv(all_papers, out_all)
        print(f"[workflow] 全部文献 {len(all_papers)} 篇 -> {out_all}", file=sys.stderr)

    # 2) 初筛结果
    screening_data: dict[str, Any] | list[Any] | None = _load_json(screening_path)
    screened_papers: list[dict[str, Any]] = _extract_papers(screening_data) if screening_data else []
    if screened_papers:
        out_screen: str = os.path.join(outdir, "screening_round1.csv")
        write_papers_csv(screened_papers, out_screen, include_decision=True)
        print(f"[workflow] 初筛结果 {len(screened_papers)} 篇 -> {out_screen}", file=sys.stderr)

    # 3) 最终纳入：优先 selected.json，否则从初筛结果回退推导
    selected_papers: list[dict[str, Any]] = []
    selected_data: dict[str, Any] | list[Any] | None = _load_json(selected_path)
    if selected_data:
        selected_papers = _extract_papers(selected_data)
    elif screened_papers:
        # 回退链：round2 判定 include；或 round1 include 且未做 round2
        selected_papers = [
            p for p in screened_papers
            if (_decision_text(p).get("round2") == "include")
            or (_decision_text(p).get("round1") == "include" and not _decision_text(p).get("round2"))
        ]
    if selected_papers:
        out_selected: str = os.path.join(outdir, "selected.csv")
        write_papers_csv(selected_papers, out_selected)
        print(f"[workflow] 最终纳入 {len(selected_papers)} 篇 -> {out_selected}", file=sys.stderr)

    if not any([all_papers, screened_papers, selected_papers]):
        print("[workflow] 未找到可导出的结果文件", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
