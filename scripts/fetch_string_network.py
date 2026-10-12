#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STRING 蛋白-蛋白相互作用（PPI）网络查询模板
用于网络药理学研究中靶点 PPI 网络构建与核心靶点识别

使用前安装：
    pip install requests

使用方法：
    from fetch_string_network_template import *
    # 构建靶点 PPI 网络
    network = build_ppi_network(["TNF", "IL6", "IL10", "VEGFA"], species=9606)
    # 查询交互伙伴
    partners = get_interaction_partners("TNF", species=9606)
    # 功能富集分析
    enrichment = get_functional_enrichment(["TNF", "IL6", "IL10"], species=9606)
"""

import requests
import time
import json
from collections import defaultdict


# ============================================================
# 基础 API 调用
# ============================================================

STRING_API = "https://string-db.org/api/json"
HUMAN_TAXON = 9606  # 人类 NCBI Taxonomy ID


def get_network(gene_list, species=HUMAN_TAXON, required_score=400, limit=500):
    """
    获取蛋白-蛋白相互作用网络

    Parameters:
        gene_list: list[str] - 基因名/蛋白名列表（如 ["TNF", "IL6"]）
        species: int - 物种 NCBI Taxonomy ID（人类=9606, 小鼠=10090, 大鼠=10116）
        required_score: int - 置信度阈值（0-1000，默认400=中等置信度）
        limit: int - 返回最大边数

    Returns:
        list[dict] - PPI 网络边列表，每条含 preferredName_A, preferredName_B, score
    """
    # ⚠️ STRING 用 %0d（URL编码的换行符）分隔多个蛋白名，不是逗号
    identifiers = "%0d".join(gene_list)
    params = {
        "identifiers": identifiers,
        "species": species,
        "required_score": required_score,
        "limit": limit,
    }
    resp = requests.get(f"{STRING_API}/network", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def get_interaction_partners(gene_name, species=HUMAN_TAXON, required_score=400):
    """
    查询指定蛋白的交互伙伴

    Parameters:
        gene_name: str - 基因名/蛋白名（如 "TNF"）
        species: int - 物种 ID
        required_score: int - 置信度阈值

    Returns:
        list[dict] - 交互伙伴列表，每条含 preferredName_B, score
    """
    params = {
        "identifiers": gene_name,
        "species": species,
        "required_score": required_score,
    }
    resp = requests.get(f"{STRING_API}/interaction_partners", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def get_functional_enrichment(gene_list, species=HUMAN_TAXON):
    """
    功能富集分析（KEGG 通路、GO 术语等）

    Parameters:
        gene_list: list[str] - 基因名列表
        species: int - 物种 ID

    Returns:
        list[dict] - 富集结果，每条含 category, term, p_value, fdr
    """
    identifiers = "%0d".join(gene_list)
    params = {
        "identifiers": identifiers,
        "species": species,
    }
    resp = requests.get(f"{STRING_API}/enrichment", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


# ============================================================
# 高级功能
# ============================================================

def build_ppi_network(gene_list, species=HUMAN_TAXON, required_score=400, limit=500):
    """
    构建 PPI 网络邻接表

    Parameters:
        gene_list: list[str] - 基因名列表
        species: int - 物种 ID
        required_score: int - 置信度阈值
        limit: int - 最大边数

    Returns:
        dict - 邻接表 {gene: {partner: score}}
    """
    edges = get_network(gene_list, species, required_score, limit)
    network = defaultdict(dict)
    for edge in edges:
        a = edge.get("preferredName_A", "")
        b = edge.get("preferredName_B", "")
        score = edge.get("score", 0)
        if a and b:
            network[a][b] = score
            network[b][a] = score
    return dict(network)


def get_hub_genes(gene_list, species=HUMAN_TAXON, required_score=400, top_n=20):
    """
    识别 Hub 基因（网络中度最高的节点）

    Parameters:
        gene_list: list[str] - 输入基因列表
        species: int - 物种 ID
        required_score: int - 置信度阈值
        top_n: int - 返回前 N 个 Hub 基因

    Returns:
        list[tuple] - [(gene_name, degree, avg_score), ...] 按度降序排列
    """
    network = build_ppi_network(gene_list, species, required_score)
    degrees = []
    for gene, partners in network.items():
        degree = len(partners)
        avg_score = sum(partners.values()) / degree if degree > 0 else 0
        degrees.append((gene, degree, avg_score))
    degrees.sort(key=lambda x: x[1], reverse=True)
    return degrees[:top_n]


def extend_network_from_seeds(seed_genes, species=HUMAN_TAXON, required_score=700,
                                max_additions=50, exclude_genes=None):
    """
    从种子基因扩展 PPI 网络：先获取种子间 PPI，再获取每个种子的交互伙伴

    Parameters:
        seed_genes: list[str] - 种子基因列表
        species: int - 物种 ID
        required_score: int - 置信度阈值（默认700=高置信度）
        max_additions: int - 最多新增基因数
        exclude_genes: set[str] - 排除的基因列表

    Returns:
        tuple - (all_genes, network_edges)
    """
    if exclude_genes is None:
        exclude_genes = set()
    else:
        exclude_genes = set(exclude_genes)

    # 第一步：种子基因间 PPI
    seed_edges = get_network(seed_genes, species, required_score)

    # 第二步：每个种子的交互伙伴
    all_partners = set(seed_genes)
    all_edges = list(seed_edges)

    for gene in seed_genes:
        if len(all_partners) >= max_additions + len(seed_genes):
            break
        partners = get_interaction_partners(gene, species, required_score)
        for p in partners:
            partner_name = p.get("preferredName_B", "")
            if partner_name and partner_name not in exclude_genes:
                all_partners.add(partner_name)
            all_edges.append(p)
        time.sleep(0.5)  # 避免请求过快

    return list(all_partners), all_edges


# ============================================================
# 批量调用与缓存
# ============================================================

def batch_get_networks(gene_batches, species=HUMAN_TAXON, required_score=400,
                       cache_file=None, delay=1.0):
    """
    批量获取多组基因的 PPI 网络（带断点续跑缓存）

    Parameters:
        gene_batches: list[list[str]] - 多组基因列表
        species: int - 物种 ID
        required_score: int - 置信度阈值
        cache_file: str - 缓存文件路径（JSON），支持断点续跑
        delay: float - 每批间隔秒数

    Returns:
        dict - {batch_key: network_dict}
    """
    cache = {}
    if cache_file:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    results = {}
    for i, batch in enumerate(gene_batches):
        batch_key = "|".join(sorted(batch))
        if batch_key in cache:
            results[batch_key] = cache[batch_key]
            continue

        print(f"  Batch {i+1}/{len(gene_batches)}: {len(batch)} genes...")
        try:
            network = build_ppi_network(batch, species, required_score)
            results[batch_key] = network
            cache[batch_key] = network
        except Exception as e:
            print(f"    Error: {e}")

        # 实时保存缓存
        if cache_file:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)

        time.sleep(delay)

    return results


# ============================================================
# 主程序入口
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="STRING PPI 网络构建 + Hub 识别 + 功能富集")
    parser.add_argument("--genes", nargs="+", required=True, help="种子基因列表（如 TNF IL6 IL10）")
    parser.add_argument("--species", type=int, default=HUMAN_TAXON, help="NCBI 物种 TaxID（默认 9606 人类）")
    parser.add_argument("--score", type=int, default=400, help="STRING 置信度阈值（默认 400）")
    parser.add_argument("--top", type=int, default=10, help="Hub 基因 Top N（默认 10）")
    args = parser.parse_args()

    seed_genes = args.genes
    print("=== 1. 构建 PPI 网络 ===")
    network = build_ppi_network(seed_genes, species=args.species, required_score=args.score)
    print(f"网络节点数: {len(network)}")
    print(f"总边数: {sum(len(v) for v in network.values()) // 2}")

    print("\n=== 2. 识别 Hub 基因 ===")
    hubs = get_hub_genes(seed_genes, species=args.species, required_score=args.score, top_n=args.top)
    print(f"{'Gene':<12} {'Degree':<8} {'Avg Score':<10}")
    for gene, degree, score in hubs:
        print(f"{gene:<12} {degree:<8} {score:.4f}")

    print("\n=== 3. 功能富集分析 ===")
    enrichment = get_functional_enrichment(seed_genes, species=args.species)
    for item in enrichment[:10]:
        category = item.get("category", "")
        term = item.get("term", "")
        pval = item.get("p_value", 1)
        fdr = item.get("fdr", 1)
        print(f"  [{category}] {term} (p={pval:.2e}, FDR={fdr:.2e})")

    print("\nDone.")
