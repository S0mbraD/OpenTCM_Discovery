#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 药物靶点筛选工作流模板
==========================================
基于中药成分分子库，通过多源数据库交叉验证，
实现「成分→靶点→疾病→评分→富集」的全流程靶点筛选。

工作流步骤：
1. 成分→靶点映射（ChEMBL + STITCH + SymMap）
2. 疾病-靶点关联（Open Targets）
3. 多源证据加权评分
4. PPI 网络构建与 Hub 靶点识别（STRING）
5. 功能富集分析（g:Profiler + Enrichr）
6. 靶点排序输出（Excel）

使用前需安装：pip install requests openpyxl
"""

import requests
import json
import time
import os
from collections import defaultdict

# ============================================================
# 配置
# ============================================================

# Open Targets GraphQL
OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"

# DGIdb GraphQL
DGIDB_URL = "https://dgidb.org/api/graphql"

# ChEMBL REST
CHEMBL_URL = "https://www.ebi.ac.uk/chembl/api/data"

# STRING REST
STRING_URL = "https://string-db.org/api/json"

# g:Profiler
GPROFILER_URL = "https://biit.cs.ut.ee/gprofiler/api/gost/profile/"

# Enrichr
ENRICHR_ADD_URL = "https://maayanlab.cloud/Enrichr/addList"
ENRICHR_ENRICH_URL = "https://maayanlab.cloud/Enrichr/enrich"

# UniProt
UNIPROT_URL = "https://rest.uniprot.org/uniprotkb"

# 缓存目录：默认脚本同目录下 cache/，可用环境变量 TARGET_SCREENING_CACHE 或 --cache 覆盖
CACHE_DIR = os.environ.get(
    "TARGET_SCREENING_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
)

# UC 疾病 MONDO ID（⚠️ MONDO_0004989 是乳腺癌，MONDO_0005101 才是 UC）
UC_MONDO_ID = "MONDO_0005101"

# 评分权重配置
SCORE_WEIGHTS = {
    "genetic_association": 0.25,  # 遗传学关联
    "clinical": 0.25,            # 临床证据
    "drug_activity": 0.20,       # 药物活性
    "literature": 0.15,          # 文献证据
    "ppi_centrality": 0.15,      # PPI 网络中心性
}

# 请求超时
TIMEOUT = 60


# ============================================================
# 第一步：成分→靶点映射
# ============================================================

def chembl_get_targets_for_compound(chembl_id, activity_threshold=10000):
    """
    从 ChEMBL 获取化合物的生物活性靶点
    :param chembl_id: ChEMBL 化合物 ID（如 CHEMBL704）
    :param activity_threshold: 活性阈值（nM），默认 10μM
    :return: {target_chembl_id: {pref_name, activity_value, activity_type}}
    """
    url = f"{CHEMBL_URL}/activity.json"
    params = {"molecule_chembl_id": chembl_id, "limit": 100}
    headers = {"Accept": "application/json"}
    
    targets = {}
    offset = 0
    while True:
        params["offset"] = offset
        resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        if resp.status_code != 200:
            break
        data = resp.json()
        activities = data.get("activities", [])
        if not activities:
            break
        for act in activities:
            tid = act.get("target_chembl_id")
            if not tid:
                continue
            val = act.get("standard_value")
            if val:
                try:
                    val_float = float(val)
                    if val_float <= activity_threshold:
                        targets[tid] = {
                            "pref_name": act.get("target_pref_name", ""),
                            "activity_value": val_float,
                            "activity_type": act.get("standard_type", ""),
                            "units": act.get("standard_units", ""),
                        }
                except (ValueError, TypeError):
                    pass
        # 分页
        meta = data.get("page_meta", {})
        if meta.get("total_count", 0) <= offset + len(activities):
            break
        offset += len(activities)
        time.sleep(0.5)
    return targets


def chembl_search_target_by_name(name_keyword):
    """
    按名称关键词搜索 ChEMBL 靶点
    :return: [{target_chembl_id, pref_name, organism}]
    """
    url = f"{CHEMBL_URL}/target.json"
    params = {
        "pref_name__icontains": name_keyword,
        "organism": "Homo sapiens",
        "limit": 50,
    }
    headers = {"Accept": "application/json"}
    resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    if resp.status_code != 200:
        return []
    return resp.json().get("targets", [])


def stitch_get_interaction_partners(compound_name, species=9606):
    """
    从 STITCH 获取化合物-蛋白互作
    :param compound_name: 化合物名（如 'mesalazine'）
    :param species: 物种 ID（人类=9606）
    :return: [{preferredName_B, score}]
    """
    url = f"{STRING_URL}/interaction_partners"
    params = {
        "identifiers": compound_name,
        "species": species,
        "limit": 50,
    }
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    if resp.status_code != 200:
        return []
    return resp.json()


def map_compounds_to_targets(compound_list):
    """
    将成分列表映射到靶点
    :param compound_list: [{name, chembl_id, smiles, pubchem_cid}]
    :return: {target_symbol: {sources: [成分名], evidence: {...}}}
    """
    all_targets = defaultdict(lambda: {"sources": [], "evidence": {}})
    
    for compound in compound_list:
        chembl_id = compound.get("chembl_id")
        name = compound.get("name", "")
        
        # ChEMBL 生物活性靶点
        if chembl_id:
            chembl_targets = chembl_get_targets_for_compound(chembl_id)
            for tid, info in chembl_targets.items():
                # 需要将 ChEMBL target ID 转为基因符号（通过 UniProt 交叉引用）
                # 这里简化处理，实际需查 target 详情获取 UniProt ID
                all_targets[tid]["sources"].append(name)
                all_targets[tid]["evidence"]["chembl_activity"] = info
        
        # STITCH 化合物-蛋白互作
        if name:
            stitch_partners = stitch_get_interaction_partners(name)
            for partner in stitch_partners:
                pname = partner.get("preferredName_B", "")
                if pname:
                    all_targets[pname]["sources"].append(name)
                    all_targets[pname]["evidence"]["stitch_score"] = partner.get("score", 0)
        
        time.sleep(0.5)  # 速率限制
    
    return dict(all_targets)


# ============================================================
# 第二步：疾病-靶点关联（Open Targets）
# ============================================================

def ot_query(query_str, timeout=TIMEOUT):
    """执行 Open Targets GraphQL 查询"""
    resp = requests.post(
        OT_URL,
        headers={"Content-Type": "application/json"},
        json={"query": query_str},
        timeout=timeout,
    )
    data = resp.json()
    if "errors" in data:
        print(f"OT GraphQL errors: {data['errors'][:200]}")
        return None
    return data.get("data")


def ot_search_disease(keyword):
    """搜索疾病获取 MONDO ID"""
    query = '''
    {
      search(queryString: "%s", entityNames: ["disease"], page: {index: 0, size: 10}) {
        total
        hits { id name entity }
      }
    }
    ''' % keyword
    data = ot_query(query)
    if data:
        return data["search"]["hits"]
    return []


def ot_disease_to_targets(mondo_id, page_size=100, max_targets=None):
    """
    获取疾病关联靶点（支持分页获取全部）
    :return: [{target_id, approved_symbol, score, datatype_scores: {id: score}}]
    """
    all_rows = []
    page = 0
    while True:
        query = '''
        {
          disease(efoId: "%s") {
            associatedTargets(page: {index: %d, size: %d}) {
              count
              rows {
                target { id approvedSymbol }
                score
                datatypeScores { id score }
              }
            }
          }
        }
        ''' % (mondo_id, page, page_size)
        data = ot_query(query)
        if not data or not data.get("disease"):
            break
        at = data["disease"]["associatedTargets"]
        rows = at.get("rows", [])
        if not rows:
            break
        for row in rows:
            dt_scores = {ds["id"]: ds["score"] for ds in row.get("datatypeScores", [])}
            all_rows.append({
                "target_id": row["target"]["id"],
                "approved_symbol": row["target"]["approvedSymbol"],
                "score": row["score"],
                "datatype_scores": dt_scores,
            })
        if max_targets and len(all_rows) >= max_targets:
            all_rows = all_rows[:max_targets]
            break
        if len(all_rows) >= at.get("count", 0):
            break
        page += 1
        time.sleep(0.5)
    return all_rows


def ot_target_to_diseases(ensembl_id, page_size=100):
    """获取靶点关联疾病"""
    query = '''
    {
      target(ensemblId: "%s") {
        id approvedSymbol
        associatedDiseases(page: {index: 0, size: %d}) {
          count
          rows { disease { id name } score }
        }
      }
    }
    ''' % (ensembl_id, page_size)
    data = ot_query(query)
    if data and data.get("target"):
        return data["target"]["associatedDiseases"]
    return None


def ot_target_interactions(ensembl_id, page_size=100):
    """获取靶点 PPI 互作（Open Targets 内部 PPI 数据）"""
    query = '''
    {
      target(ensemblId: "%s") {
        interactions(page: {index: 0, size: %d}) {
          count
          rows {
            score
            targetA { id approvedSymbol }
            targetB { id approvedSymbol }
            sourceDatabase
          }
        }
      }
    }
    ''' % (ensembl_id, page_size)
    data = ot_query(query)
    if data and data.get("target"):
        return data["target"]["interactions"]
    return None


def ot_disease_drugs(mondo_id):
    """获取疾病关联的临床候选药物"""
    query = '''
    {
      disease(efoId: "%s") {
        drugAndClinicalCandidates {
          count
          rows {
            id maxClinicalStage
            drug { id name drugType }
          }
        }
      }
    }
    ''' % mondo_id
    data = ot_query(query)
    if data and data.get("disease"):
        return data["disease"]["drugAndClinicalCandidates"]
    return None


def ot_drug_mechanisms(chembl_id):
    """获取药物作用机制和适应症"""
    query = '''
    {
      drug(chemblId: "%s") {
        id name drugType
        mechanismsOfAction {
          rows {
            mechanismOfAction
            actionType
            targetName
            targets { id approvedSymbol }
          }
        }
        indications {
          count
          rows { id maxClinicalStage disease { id name } }
        }
      }
    }
    ''' % chembl_id
    data = ot_query(query)
    if data and data.get("drug"):
        return data["drug"]
    return None


def dgidb_gene_drug_interactions(gene_names):
    """
    从 DGIdb 查询基因-药物交互
    :param gene_names: ["TNF", "IL6", "IL10"]
    :return: {gene: [{drug_name, concept_id, interaction_score, publications}]}
    """
    query = '''
    {
      genes(names: %s) {
        nodes {
          name
          interactions {
            drug { name conceptId }
            interactionScore
            interactionTypes { type directionality }
            publications { pmid }
          }
        }
      }
    }
    ''' % json.dumps(gene_names)
    
    resp = requests.post(
        DGIDB_URL,
        headers={"Content-Type": "application/json"},
        json={"query": query},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if "errors" in data or not data.get("data"):
        return {}
    
    result = {}
    nodes = data["data"]["genes"]["nodes"]
    for node in nodes:
        gene = node["name"]
        result[gene] = []
        for inter in node.get("interactions", []):
            result[gene].append({
                "drug_name": inter["drug"]["name"],
                "concept_id": inter["drug"].get("conceptId", ""),
                "interaction_score": inter.get("interactionScore", 0),
                "interaction_types": [
                    t.get("type", "") for t in inter.get("interactionTypes", [])
                ],
                "publications": [p.get("pmid") for p in inter.get("publications", [])],
            })
    return result


# ============================================================
# 第三步：多源证据加权评分
# ============================================================

def score_targets(compound_targets, disease_targets, ppi_degrees=None):
    """
    对靶点进行多源证据加权评分
    
    :param compound_targets: 第一步的成分-靶点映射结果
    :param disease_targets: 第二步的疾病-靶点关联 [{target_id, approved_symbol, score, datatype_scores}]
    :param ppi_degrees: {target_symbol: degree}（可选，来自 STRING PPI 网络）
    :return: [{symbol, ensembl_id, total_score, sub_scores, source_compounds}]
    """
    # 构建疾病靶点查找表
    disease_map = {}
    for dt in disease_targets:
        symbol = dt["approved_symbol"]
        disease_map[symbol] = dt
    
    # PPI degree 归一化
    max_degree = max(ppi_degrees.values()) if ppi_degrees else 1
    if max_degree == 0:
        max_degree = 1
    
    scored = []
    for target_key, info in compound_targets.items():
        # 尝试匹配疾病靶点
        symbol = target_key
        ensembl_id = ""
        dt_info = disease_map.get(symbol)
        
        sub_scores = {}
        source_compounds = info.get("sources", [])
        
        if dt_info:
            ensembl_id = dt_info["target_id"]
            dts = dt_info.get("datatype_scores", {})
            
            # 遗传学关联评分
            sub_scores["genetic_association"] = dts.get("genetic_association", 0)
            
            # 临床证据评分
            sub_scores["clinical"] = dts.get("clinical", 0)
            
            # 文献证据评分
            sub_scores["literature"] = dts.get("literature", 0)
        
        # 药物活性评分（ChEMBL/STITCH 证据）
        chembl_ev = info.get("evidence", {}).get("chembl_activity")
        stitch_ev = info.get("evidence", {}).get("stitch_score")
        drug_score = 0
        if chembl_ev:
            # 活性值越低越好（nM），10μM=0分，1nM=1分
            val = chembl_ev.get("activity_value", 10000)
            drug_score = max(0, 1 - val / 10000)
        if stitch_ev:
            drug_score = max(drug_score, stitch_ev)
        sub_scores["drug_activity"] = drug_score
        
        # PPI 网络中心性评分
        if ppi_degrees and symbol in ppi_degrees:
            sub_scores["ppi_centrality"] = ppi_degrees[symbol] / max_degree
        else:
            sub_scores["ppi_centrality"] = 0
        
        # 加权求和
        total = sum(
            sub_scores.get(k, 0) * w
            for k, w in SCORE_WEIGHTS.items()
        )
        
        scored.append({
            "symbol": symbol,
            "ensembl_id": ensembl_id,
            "total_score": round(total, 4),
            "sub_scores": {k: round(v, 4) for k, v in sub_scores.items()},
            "source_compounds": list(set(source_compounds)),
        })
    
    # 按总分降序排列
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    return scored


# ============================================================
# 第四步：PPI 网络构建与 Hub 靶点识别（STRING）
# ============================================================

def string_build_ppi_network(gene_list, species=9606, score_threshold=400):
    """
    用 STRING 构建 PPI 网络
    :param gene_list: ["TNF", "IL6", "IL10", ...]
    :param score_threshold: 置信度阈值（400=中等，700=高，900=最高）
    :return: {gene: degree}, edges [{A, B, score}]
    """
    # STRING 要求 identifiers 用 %0d 分隔（URL 编码的回车符）
    identifiers = "%0d".join(gene_list)
    url = f"{STRING_URL}/network"
    params = {
        "identifiers": identifiers,
        "species": species,
        "required_score": score_threshold,
    }
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    if resp.status_code != 200:
        return {}, []
    
    edges = resp.json()
    degrees = defaultdict(int)
    for edge in edges:
        a = edge.get("preferredName_A", "")
        b = edge.get("preferredName_B", "")
        if a:
            degrees[a] += 1
        if b:
            degrees[b] += 1
    
    return dict(degrees), edges


def identify_hub_targets(degrees, top_percent=10):
    """
    识别 Hub 靶点（degree 排名前 N%）
    :return: [(gene, degree)]
    """
    sorted_targets = sorted(degrees.items(), key=lambda x: x[1], reverse=True)
    n = max(1, int(len(sorted_targets) * top_percent / 100))
    return sorted_targets[:n]


# ============================================================
# 第五步：功能富集分析
# ============================================================

def gprofiler_enrichment(gene_list, organism="hsapiens"):
    """
    用 g:Profiler 做功能富集分析
    :param gene_list: ["TNF", "IL6", ...]
    :return: [{description, native, p_value, source, intersection_size}]
    """
    resp = requests.post(
        GPROFILER_URL,
        headers={"Content-Type": "application/json"},
        json={
            "organism": organism,
            "query": gene_list,
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        return []
    result = resp.json().get("result", [])
    enriched = []
    for r in result:
        enriched.append({
            "description": r.get("description", ""),
            "native": r.get("native", ""),
            "p_value": r.get("p_value", 1),
            "source": r.get("source", ""),
            "intersection_size": r.get("intersection_size", 0),
            "term_size": r.get("term_size", 0),
        })
    # 按 p 值排序
    enriched.sort(key=lambda x: x["p_value"])
    return enriched


def enrichr_enrichment(gene_list, library="KEGG_2021_Human"):
    """
    用 Enrichr 做功能富集分析
    :param gene_list: ["TNF", "IL6", ...]
    :param library: 库名（如 KEGG_2021_Human, GO_Biological_Process_2023, Reactome_2022）
    :return: [{term, p_value, overlap, genes}]
    """
    # 第一步：上传基因列表
    gene_text = "\n".join(gene_list)
    resp = requests.post(
        ENRICHR_ADD_URL,
        files={"list": ("gene_list.txt", gene_text)},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        return []
    user_list_id = resp.json().get("userListId")
    if not user_list_id:
        return []
    
    # 第二步：查询富集结果
    enrich_url = f"{ENRICHR_ENRICH_URL}?userListId={user_list_id}&backgroundType={library}"
    resp = requests.get(enrich_url, timeout=TIMEOUT)
    if resp.status_code != 200:
        return []
    
    data = resp.json()
    entries = data.get(library, [])
    enriched = []
    for entry in entries:
        # Enrichr 返回格式: [rank, term, p_value, z_score, combined_score, genes, ...]
        enriched.append({
            "term": entry[1] if len(entry) > 1 else "",
            "p_value": entry[2] if len(entry) > 2 else 1,
            "combined_score": entry[4] if len(entry) > 4 else 0,
            "overlap_genes": entry[5] if len(entry) > 5 else [],
        })
    enriched.sort(key=lambda x: x["p_value"])
    return enriched


# ============================================================
# 第六步：靶点排序与 Excel 输出
# ============================================================

def export_results_to_excel(scored_targets, enrichment_results, output_path):
    """
    将筛选结果导出为 Excel
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    
    wb = Workbook()
    
    # Sheet 1: 靶点排名
    ws1 = wb.active
    ws1.title = "靶点排名"
    headers = [
        "排名", "基因符号", "Ensembl ID", "综合评分",
        "遗传学关联", "临床证据", "药物活性", "文献证据", "PPI中心性",
        "来源成分数", "来源成分"
    ]
    ws1.append(headers)
    # 表头样式
    for cell in ws1[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        cell.font = Font(bold=True, color="FFFFFF")
    
    for i, t in enumerate(scored_targets, 1):
        ss = t.get("sub_scores", {})
        ws1.append([
            i,
            t["symbol"],
            t["ensembl_id"],
            t["total_score"],
            ss.get("genetic_association", 0),
            ss.get("clinical", 0),
            ss.get("drug_activity", 0),
            ss.get("literature", 0),
            ss.get("ppi_centrality", 0),
            len(t["source_compounds"]),
            ", ".join(t["source_compounds"][:10]),
        ])
    
    # 列宽
    for col in range(1, len(headers) + 1):
        ws1.column_dimensions[chr(64 + col) if col <= 26 else chr(64 + (col-1)//26) + chr(64 + (col-1)%26 + 1)].width = 15
    ws1.column_dimensions["K"].width = 40
    
    # Sheet 2: 功能富集
    ws2 = wb.create_sheet("功能富集")
    ws2.append(["通路/GO术语", "通路ID", "p值", "来源", "交集大小", "通路总大小"])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        cell.font = Font(bold=True, color="FFFFFF")
    
    for er in enrichment_results:
        ws2.append([
            er["description"],
            er["native"],
            er["p_value"],
            er["source"],
            er["intersection_size"],
            er["term_size"],
        ])
    
    # 保存
    wb.save(output_path)
    print(f"结果已保存至: {output_path}")


# ============================================================
# 主工作流
# ============================================================

def run_target_screening_workflow(
    compound_list,
    disease_keyword="ulcerative colitis",
    output_path=None,
    max_disease_targets=500,
    ppi_score_threshold=400,
):
    """
    运行完整的 AI 药物靶点筛选工作流
    
    :param compound_list: 成分列表 [{name, chembl_id, smiles, pubchem_cid}]
    :param disease_keyword: 疾病关键词
    :param output_path: 输出 Excel 路径
    :param max_disease_targets: 最多获取的疾病靶点数
    :param ppi_score_threshold: STRING PPI 置信度阈值
    """
    print("=" * 60)
    print("AI 药物靶点筛选工作流")
    print("=" * 60)
    
    # --- 第一步：成分→靶点映射 ---
    print("\n[步骤1] 成分→靶点映射（ChEMBL + STITCH）...")
    compound_targets = map_compounds_to_targets(compound_list)
    print(f"  映射到 {len(compound_targets)} 个潜在靶点")
    
    # --- 第二步：疾病-靶点关联 ---
    print(f"\n[步骤2] 疾病-靶点关联（Open Targets: {disease_keyword}）...")
    # 搜索确认 MONDO ID
    disease_hits = ot_search_disease(disease_keyword)
    if not disease_hits:
        print("  错误：未找到疾病")
        return
    mondo_id = disease_hits[0]["id"]
    disease_name = disease_hits[0]["name"]
    print(f"  疾病: {disease_name} (ID: {mondo_id})")
    
    # 获取疾病关联靶点
    disease_targets = ot_disease_to_targets(mondo_id, max_targets=max_disease_targets)
    print(f"  获取 {len(disease_targets)} 个疾病关联靶点")
    
    # --- 交集筛选 ---
    compound_target_symbols = set(compound_targets.keys())
    disease_target_symbols = set(dt["approved_symbol"] for dt in disease_targets)
    intersection = compound_target_symbols & disease_target_symbols
    print(f"  成分靶点 ∩ 疾病靶点 = {len(intersection)} 个交集靶点")
    
    # --- 第三步：PPI 网络构建 ---
    print(f"\n[步骤3] PPI 网络构建（STRING, score≥{ppi_score_threshold}）...")
    if intersection:
        degrees, edges = string_build_ppi_network(
            list(intersection), score_threshold=ppi_score_threshold
        )
        print(f"  PPI 网络: {len(edges)} 条边, {len(degrees)} 个节点")
        hub_targets = identify_hub_targets(degrees, top_percent=10)
        print(f"  Hub 靶点 (top 10%): {[t[0] for t in hub_targets]}")
    else:
        degrees = {}
        print("  无交集靶点，跳过 PPI 构建")
    
    # --- 第四步：多源评分 ---
    print("\n[步骤4] 多源证据加权评分...")
    scored_targets = score_targets(compound_targets, disease_targets, degrees)
    print(f"  评分完成，top 10 靶点:")
    for t in scored_targets[:10]:
        print(f"    {t['symbol']}: {t['total_score']:.4f} (来源: {len(t['source_compounds'])}个成分)")
    
    # --- 第五步：功能富集 ---
    print("\n[步骤5] 功能富集分析（g:Profiler）...")
    top_genes = [t["symbol"] for t in scored_targets[:50] if t["symbol"]]
    if top_genes:
        enrichment = gprofiler_enrichment(top_genes)
        print(f"  富集到 {len(enrichment)} 条通路/GO术语")
        for er in enrichment[:5]:
            print(f"    {er['description']} (p={er['p_value']:.2e})")
    else:
        enrichment = []
    
    # --- 第六步：导出 ---
    print(f"\n[步骤6] 导出结果到 {output_path}")
    # 裸文件名时 os.path.dirname 返回空串会导致 makedirs 报错；abspath 后 dirname 永不为空
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    export_results_to_excel(scored_targets, enrichment, output_path)
    
    print("\n" + "=" * 60)
    print("靶点筛选工作流完成！")
    print(f"结果文件: {output_path}")
    print("=" * 60)
    return scored_targets, enrichment


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI 药物靶点筛选工作流（成分→靶点→疾病关联→多源评分）")
    parser.add_argument("--compounds", nargs="+", required=True, help="成分 SMILES 列表（多个以空格分隔）")
    parser.add_argument("--disease", default="ulcerative colitis", help="疾病关键词（默认 ulcerative colitis）")
    parser.add_argument("--output", required=True, help="结果 Excel 输出路径")
    parser.add_argument("--max-disease-targets", type=int, default=200, help="疾病关联靶点上限（默认 200）")
    parser.add_argument("--cache", help="缓存目录（默认脚本同目录 cache/，亦可用环境变量 TARGET_SCREENING_CACHE）")
    args = parser.parse_args()

    if args.cache:
        CACHE_DIR = args.cache

    # 成分列表：支持 "name=SMILES" 或纯 SMILES；name 缺省时用 SMILES 前 12 字符
    compound_list = []
    for item in args.compounds:
        if "=" in item:
            name, smiles = item.split("=", 1)
            compound_list.append({"name": name.strip(), "smiles": smiles.strip()})
        else:
            compound_list.append({"name": item[:12], "smiles": item})

    results = run_target_screening_workflow(
        compound_list=compound_list,
        disease_keyword=args.disease,
        output_path=args.output,
        max_disease_targets=args.max_disease_targets,
    )
