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

# 统一文献记录类型：规范字段 + 任意自定义字段
Paper = dict[str, Any]

# 递归扫描目录时接受的文献文件扩展名（小写）
_SCAN_EXTS: tuple[str, ...] = (
    ".json", ".jsonl", ".csv", ".xlsx", ".xlsm", ".ris", ".bib", ".bibtex", ".txt", ".medline", ".nbib",
)

# 文本读取编码尝试顺序：优先带 BOM 的 UTF-8，兼容中文数据库常见的 GBK 系列
_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk", "gb18030")

# DOI 归一化：剥离 https://doi.org/ 等 URL 前缀与 "doi:" 前缀（忽略大小写）
_DOI_PREFIX_RE: re.Pattern[str] = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
# 标题归一化：删除除中英文文字、数字、下划线外的所有字符（含空白与标点）
_NON_ALNUM_RE: re.Pattern[str] = re.compile(r"[^\w\u4e00-\u9fff]")
# 从日期串中提取 4 位年份（19xx / 20xx / 21xx）
_YEAR_RE: re.Pattern[str] = re.compile(r"(?:19|20|21)\d{2}")


# ---------------------------------------------------------------------------
# 输入收集与加载
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str:
    """按编码优先级顺序容错读取文本文件。

    参数:
        path: 待读取文件路径。

    返回:
        首次解码成功的全文；全部失败时按 gb18030 + errors=replace 兜底，不抛异常。
    """
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
    """把 --input 给定的文件/目录展开为去重保序的文件列表。

    参数:
        inputs: 用户给出的路径列表，元素可为文件或目录。

    返回:
        展开后的文件路径列表；目录递归扫描（仅收 _SCAN_EXTS 扩展名），
        经规范化绝对路径去重并保持首次出现顺序；不存在的路径打印警告后跳过。
    """
    files: list[str] = []
    item: str
    for item in inputs:
        if os.path.isdir(item):
            # 目录：递归遍历，目录名与文件名均排序，保证扫描顺序稳定可复现
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

    # 用规范化绝对路径去重：同一文件经不同写法或大小写传入时只保留首次出现
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
    """按格式加载单个来源文件为规范化 Paper 列表。

    参数:
        path: 文件路径。
        fmt: 格式名；为 "auto" 或空时调用 import.py 的 detect_format 自动识别。

    返回:
        Paper 列表；medline 为其余格式的兜底。
    """
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
    """归一化 DOI 以便跨来源比较。

    参数:
        s: 原始 DOI，可能带 doi.org URL 或 "doi:" 前缀、大小写不一。

    返回:
        去空白、转小写、剥离前缀并去掉尾部句点后的 DOI。
    """
    v: str = (s or "").strip().lower()
    v = _DOI_PREFIX_RE.sub("", v)
    return v.strip().rstrip(".")


def _norm_title(s: str) -> str:
    """归一化标题以便跨来源比较。

    参数:
        s: 原始标题。

    返回:
        转小写并删除所有非字母数字字符（保留中文）后的标题。
    """
    return _NON_ALNUM_RE.sub("", (s or "").lower()).strip()


def _year_of(date: str) -> str:
    """从任意日期串中提取 4 位年份。

    参数:
        date: 日期文本，如 "2023-05-01"、"2023/05" 或仅 "2023"。

    返回:
        首个匹配的年份（19xx / 20xx / 21xx）；取不到返回 ""。
    """
    m: re.Match[str] | None = _YEAR_RE.search(date or "")
    return m.group(0) if m else ""


def dedup_key(p: Paper) -> tuple[str, str]:
    """取该记录优先级最高的主键。

    参数:
        p: 待取键的 Paper。

    返回:
        (主键类型, 主键值) 二元组，如 ("doi", "10.xxxx")；无可用主键返回 ("", "")。
    """
    keys: list[str] = candidate_keys(p)
    if not keys:
        return ("", "")
    kind: str
    value: str
    kind, value = keys[0].split(":", 1)
    return (kind, value)


def candidate_keys(p: Paper) -> list[str]:
    """为一条记录生成候选主键列表，优先级从高到低为 DOI → PMID → 标题+年份。

    一条记录可能同时具备多种标识（如 CNKI 记录只有 DOI、PubMed 记录只有 PMID），
    因此命中任一候选键即视为同一篇文献，匹配时仍按上述优先级顺序尝试。

    参数:
        p: 待取键的 Paper。

    返回:
        "kind:value" 形式的候选键列表；DOI / PMID / 标题三者皆无时为空列表。
    """
    keys: list[str] = []
    # 第一级候选键：归一化后的 DOI
    doi: str = _norm_doi(str(p.get("doi", "") or ""))
    if doi:
        keys.append(f"doi:{doi}")
    # 第二级候选键：纯数字的 PMID
    pmid: str = str(p.get("pmid", "") or "").strip()
    if pmid.isdigit():
        keys.append(f"pmid:{pmid}")
    # 第三级候选键：归一化标题 + 年份（年份取不到时为空串，仍可参与匹配）
    title: str = _norm_title(str(p.get("title", "") or ""))
    if title:
        keys.append(f"title:{title}|{_year_of(str(p.get('date', '') or ''))}")
    return keys


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------

def _union(a: list[Any], b: list[Any]) -> list[str]:
    """合并两个列表为并集，去重保序，忽略空项。

    参数:
        a: 第一个列表，其元素在结果中排前面。
        b: 第二个列表，其元素排在 a 之后。

    返回:
        元素转 str 去空白后，去空串、去重且保持先后顺序的列表。
    """
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
    """生成供去重日志使用的一句话文献摘要。

    参数:
        p: 文献 Paper。

    返回:
        《标题前 40 字》(PMID=xx,DOI=xx) 形式的短描述；无标识时只有书名号部分。
    """
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
    """把 extra 的字段就地合并进 base。

    参数:
        base: 合并目标（先到的记录），会被原地修改。
        extra: 后到的重复记录，仅作为字段来源。

    返回:
        None；合并结果直接写回 base。
    """
    # str 规范字段：先到先用——base 已有非空值时不覆盖
    key: str
    for key in _imp.CANON_STR_KEYS:
        old: str = str(base.get(key, "") or "").strip()
        new: str = str(extra.get(key, "") or "").strip()
        if new and not old:
            base[key] = new
    # list 规范字段：取并集去重保序
    for key in _imp.CANON_LIST_KEYS:
        merged: list[str] = _union(list(base.get(key, []) or []), list(extra.get(key, []) or []))
        base[key] = merged
    base["sources"] = _union(list(base.get("sources", []) or []), list(extra.get("sources", []) or []))
    # merged_from 累计被合并的记录数（缺省按 1 计），base 中原有值需保留累加
    base["merged_from"] = int(base.get("merged_from", 1) or 1) + int(extra.get("merged_from", 1) or 1)
    # decision 是筛选流程自定义字段（不在规范字段表里）：迭代更新时避免丢掉已有决策
    dec: Any = extra.get("decision")
    if dec and not base.get("decision"):
        base["decision"] = dec


def dedupe(papers: list[Paper]) -> tuple[list[Paper], int, list[str]]:
    """对文献池按候选主键去重合并。

    参数:
        papers: 待去重 Paper 列表；列表顺序即来源优先级，先出现者优先保留。

    返回:
        三元组：(合并后的列表, 被移除的重复记录数, 供人工核查的去重日志行列表)。

    异常:
        无显式抛出；字段缺失时按空值处理。
    """
    # 候选键 → 已保留记录 的索引；一条记录会把它的全部候选键都登记进来
    index: dict[str, Paper] = {}
    kept_list: list[Paper] = []
    unkeyed: list[Paper] = []
    removed: int = 0
    log: list[str] = []

    p: Paper
    for p in papers:
        keys: list[str] = candidate_keys(p)
        if not keys:
            # 无任何候选键：无法判断是否重复，原样保留（置于结果列表末尾）
            unkeyed.append(p)
            continue

        # 按 DOI → PMID → 标题 的优先级顺序在索引中查找已保留记录
        target: Paper | None = None
        hit: str = ""
        key: str
        for key in keys:  # 按 DOI → PMID → 标题 的优先级顺序尝试命中
            if key in index:
                target = index[key]
                hit = key
                break

        if target is None:
            # 未命中：作为新记录保留，并把它的全部候选键登记进索引
            p["merged_from"] = 1
            for key in keys:
                index[key] = p
            kept_list.append(p)
            continue

        # 命中：记录日志后把当前记录字段并入已保留的目标记录
        kind: str = hit.split(":", 1)[0]
        log.append(f"[{kind}] key={hit.split(':', 1)[1]} 合并：保留 {_brief(target)} <- 丢弃 {_brief(p)}")
        merge_into(target, p)
        # 补充登记新主键（不覆盖已有映射），便于后续记录经其他键命中同一目标
        for key in keys:  # 补充登记新主键，便于后续记录命中
            index.setdefault(key, target)
        removed += 1

    # 无键记录排在有键记录之后，保证有键记录的相对顺序即来源优先级
    result: list[Paper] = kept_list + unkeyed
    return result, removed, log


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

# 导出 CSV 的中文表头（列顺序与 write_csv 中 writerow 一一对应）
_CSV_HEADER: list[str] = ["序号", "PMID", "题目", "作者", "期刊", "发表日期", "关键词", "摘要", "DOI", "网址", "来源文件"]


def write_csv(papers: list[Paper], path: str) -> None:
    """把文献池导出为 Excel 可直接打开的 CSV（utf-8-sig）。

    参数:
        papers: 待导出的 Paper 列表。
        path: 输出 CSV 路径。
    """
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
    """把统一池结果写出为 JSON 文件。

    参数:
        result: 含元信息与 papers 列表的结果 dict。
        path: 输出路径。
    """
    fh: Any
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """命令行入口：收集输入、加载合并去重并写出结果。

    参数:
        argv: 命令行参数列表；None 时取 sys.argv。

    返回:
        进程退出码；未找到任何输入文件时为 1，成功为 0。
    """
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
