#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KEGG REST API 查询模板
用于化合物-通路映射、疾病-通路关联、化合物结构验证

使用前安装：
    pip install requests

使用方法：
    from fetch_kegg_compound_template import *
    # 搜索化合物
    results = search_compound("berberine")
    # 获取化合物详情
    info = get_compound_detail("C00757")
    # 查询疾病关联
    diseases = search_disease("colitis")
"""

import requests
import re
import time
import json


# ============================================================
# 基础 API 调用
# ============================================================

KEGG_REST = "https://rest.kegg.jp"


def search_compound(keyword):
    """
    搜索 KEGG Compound 数据库

    Parameters:
        keyword: str - 搜索关键词（英文名或化学名）

    Returns:
        list[dict] - 匹配结果 [{id, name}, ...]
    """
    resp = requests.get(f"{KEGG_REST}/find/compound/{keyword}", timeout=30)
    resp.raise_for_status()
    results = []
    for line in resp.text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            compound_id = parts[0].replace("cpd:", "")
            name = parts[1]
            results.append({"id": compound_id, "name": name})
    return results


def get_compound_detail(compound_id):
    """
    获取化合物详细信息

    Parameters:
        compound_id: str - KEGG Compound ID（如 "C00757"，不含 cpd: 前缀）

    Returns:
        dict - 化合物信息 {field: value}
    """
    resp = requests.get(f"{KEGG_REST}/get/{compound_id}", timeout=30)
    resp.raise_for_status()
    text = resp.text

    data = {}
    current_field = None
    for line in text.split("\n"):
        if not line.strip():
            continue
        # 顶层字段：行首非空格
        if not line.startswith(" "):
            parts = line.split(None, 1)
            field = parts[0]
            value = parts[1] if len(parts) > 1 else ""
            # 多行字段（如 REACTION, PATHWAY, DBLINKS）
            if field in ("REACTION", "PATHWAY", "ENZYME", "DBLINKS",
                         "MOTIF", "COMMENT", "REMARK", "SEQUENCE",
                         "ORGANISM", "REFERENCE"):
                data[field] = [value] if value else []
                current_field = field
            else:
                data[field] = value
                current_field = field
        else:
            # 续行：追加到当前字段
            if current_field and isinstance(data.get(current_field), list):
                data[current_field].append(line.strip())
            elif current_field:
                data[current_field] += " " + line.strip()
    return data


def search_pathway(keyword):
    """
    搜索 KEGG Pathway 数据库

    Parameters:
        keyword: str - 搜索关键词

    Returns:
        list[dict] - 匹配结果 [{id, name}, ...]
    """
    resp = requests.get(f"{KEGG_REST}/find/pathway/{keyword}", timeout=30)
    resp.raise_for_status()
    results = []
    for line in resp.text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            pathway_id = parts[0].replace("path:", "")
            name = parts[1]
            results.append({"id": pathway_id, "name": name})
    return results


def search_disease(keyword):
    """
    搜索 KEGG Disease 数据库

    Parameters:
        keyword: str - 搜索关键词

    Returns:
        list[dict] - 匹配结果 [{id, name}, ...]
    """
    resp = requests.get(f"{KEGG_REST}/find/disease/{keyword}", timeout=30)
    resp.raise_for_status()
    results = []
    for line in resp.text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            disease_id = parts[0]
            name = parts[1]
            results.append({"id": disease_id, "name": name})
    return results


def get_disease_detail(disease_id):
    """
    获取疾病详细信息

    Parameters:
        disease_id: str - KEGG Disease ID（如 "H01466"）

    Returns:
        dict - 疾病信息
    """
    resp = requests.get(f"{KEGG_REST}/get/{disease_id}", timeout=30)
    resp.raise_for_status()
    text = resp.text

    data = {}
    current_field = None
    for line in text.split("\n"):
        if not line.strip():
            continue
        if not line.startswith(" "):
            parts = line.split(None, 1)
            field = parts[0]
            value = parts[1] if len(parts) > 1 else ""
            if field in ("PATHWAY", "GENE", "REFERENCE", "COMMENT", "MARKER"):
                data[field] = [value] if value else []
                current_field = field
            else:
                data[field] = value
                current_field = field
        else:
            if current_field and isinstance(data.get(current_field), list):
                data[current_field].append(line.strip())
            elif current_field:
                data[current_field] += " " + line.strip()
    return data


# ============================================================
# 批量调用与缓存
# ============================================================

def batch_search_compounds(keywords, cache_file=None, delay=0.5):
    """
    批量搜索化合物（带缓存）

    Parameters:
        keywords: list[str] - 关键词列表
        cache_file: str - 缓存文件路径（JSON）
        delay: float - 每次请求间隔

    Returns:
        dict - {keyword: [results]}
    """
    cache = {}
    if cache_file:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    for i, kw in enumerate(keywords):
        if kw in cache:
            continue
        print(f"  [{i+1}/{len(keywords)}] Searching: {kw}")
        try:
            results = search_compound(kw)
            cache[kw] = results
        except Exception as e:
            print(f"    Error: {e}")
            cache[kw] = []
        # 实时保存
        if cache_file:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        time.sleep(delay)

    return cache


# ============================================================
# 便捷工具函数
# ============================================================

def get_compound_pubchem_id(compound_id):
    """
    从 KEGG Compound 详情中提取 PubChem CID

    Parameters:
        compound_id: str - KEGG Compound ID

    Returns:
        str or None - PubChem CID
    """
    detail = get_compound_detail(compound_id)
    dblinks = detail.get("DBLINKS", [])
    if isinstance(dblinks, list):
        for link in dblinks:
            if "PubChem:" in link:
                match = re.search(r"PubChem:\s*(\d+)", link)
                if match:
                    return match.group(1)
    elif isinstance(dblinks, str):
        match = re.search(r"PubChem:\s*(\d+)", dblinks)
        if match:
            return match.group(1)
    return None


def get_compound_pathways(compound_id):
    """
    获取化合物关联的代谢通路

    Parameters:
        compound_id: str - KEGG Compound ID

    Returns:
        list[dict] - [{pathway_id, name}, ...]
    """
    detail = get_compound_detail(compound_id)
    pathways = detail.get("PATHWAY", [])
    results = []
    if isinstance(pathways, list):
        for p in pathways:
            # 格式如 "map00380  Tryptophan metabolism"
            parts = p.split(None, 1)
            if len(parts) >= 2:
                results.append({"pathway_id": parts[0], "name": parts[1]})
    return results


# ============================================================
# 主程序入口
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="KEGG 化合物/通路/疾病查询")
    parser.add_argument("--compound", help="搜索化合物关键词（如 berberine），可多次指定")
    parser.add_argument("--disease", help="搜索疾病关键词（如 colitis）")
    parser.add_argument("--detail", help="获取化合物详情（KEGG ID，如 C00757）")
    parser.add_argument("--batch", nargs="+", help="批量搜索化合物（多个关键词）")
    args = parser.parse_args()

    if args.compound:
        print(f"=== 搜索化合物: {args.compound} ===")
        results = search_compound(args.compound)
        for r in results:
            print(f"  {r['id']}: {r['name']}")

        # 自动取第一个结果的详情
        if results:
            cid = results[0]["id"]
            print(f"\n=== 获取 {cid} 详情 ===")
            detail = get_compound_detail(cid)
            for field in ["ENTRY", "NAME", "FORMULA", "MOL_WEIGHT", "EXACT_MASS"]:
                if field in detail:
                    print(f"  {field}: {detail[field]}")
            pubchem = get_compound_pubchem_id(cid)
            if pubchem:
                print(f"  PubChem CID: {pubchem}")
            pathways = get_compound_pathways(cid)
            if pathways:
                print(f"  Pathways:")
                for p in pathways:
                    print(f"    {p['pathway_id']}: {p['name']}")

    if args.detail:
        print(f"\n=== 化合物详情: {args.detail} ===")
        detail = get_compound_detail(args.detail)
        for field in ["ENTRY", "NAME", "FORMULA", "MOL_WEIGHT", "EXACT_MASS"]:
            if field in detail:
                print(f"  {field}: {detail[field]}")

    if args.disease:
        print(f"\n=== 搜索疾病: {args.disease} ===")
        diseases = search_disease(args.disease)
        for d in diseases:
            print(f"  {d['id']}: {d['name']}")
        if diseases:
            did = diseases[0]["id"]
            print(f"\n=== 获取 {did} 详情 ===")
            detail = get_disease_detail(did)
            for field in ["ENTRY", "NAME", "DESCRIPTION"]:
                if field in detail:
                    val = detail[field]
                    if isinstance(val, str):
                        print(f"  {field}: {val[:100]}...")

    if args.batch:
        print(f"\n=== 批量搜索: {args.batch} ===")
        results = batch_search_compounds(args.batch)
        for k, v in results.items():
            print(f"  {k}: {v}")

    if not any([args.compound, args.detail, args.disease, args.batch]):
        parser.print_help()
