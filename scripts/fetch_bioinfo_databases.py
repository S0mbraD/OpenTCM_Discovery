#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生物信息学数据库综合检索模板
覆盖基因组学、转录组学、蛋白组学、通路/网络、功能注释、变异/群体遗传、表观组学

使用前安装：
    pip install requests

使用方法：
    from fetch_bioinfo_databases_template import *
    # NCBI Gene 搜索
    gene_ids, total = ncbi_gene_search("TNF", "Homo sapiens")
    # GEO 数据集搜索
    geo_ids, total = ncbi_geo_search("ulcerative colitis")
    # TCGA 文件查询
    files = gdc_search_files(data_category="Transcriptome Profiling", project_id="TCGA-COAD")
    # PDB 结构搜索
    entries = pdb_search("tumor necrosis factor")
    # AlphaFold 结构
    af = alphafold_get_prediction("P01375")
    # Reactome 通路搜索
    pathways = reactome_search_pathways("TNF")
    # g:Profiler 富集分析
    enrichment = gprofiler_enrichment(["TNF", "IL6", "IL10"])
    # Enrichr 富集分析
    user_list_id = enrichr_add_list(["TNF", "IL6", "IL10"])
    results = enrichr_get_enrichment(user_list_id, "KEGG_2021_Human")
    # JASPAR 转录因子搜索
    matrices = jaspar_search("NFKB1")
    # dbSNP 变异搜索
    snp_ids, total = ncbi_snp_search("TNF[Gene Name] AND Homo sapiens[Organism]")
    # gnomAD 变异频率
    freq = gnomad_get_variant_freq("1", 55042662, "G", "A")
    # GWAS Catalog 关联搜索
    associations = gwas_search_by_gene("TNF")
    # ClinVar 临床变异搜索
    cv_ids, total = ncbi_clinvar_search("TNF[Gene] AND pathogenic[Clinical_Significance]")
    # ENCODE 实验搜索
    experiments = encode_search_experiments(assay_title="ATAC-seq")
"""

import requests
import json
import time
from collections import defaultdict


# ============================================================
# 1. NCBI E-utilities（Gene / GEO / SRA / dbSNP / ClinVar）
# ============================================================

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _esearch(db, term, retmax=20, api_key=None):
    params = {"db": db, "term": term, "retmax": retmax, "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    resp = requests.get(f"{EUTILS}/esearch.fcgi", params=params, timeout=30)
    result = resp.json().get("esearchresult", {})
    return result.get("idlist", []), int(result.get("count", 0))


def _esummary(db, uid, api_key=None):
    params = {"db": db, "id": uid, "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    resp = requests.get(f"{EUTILS}/esummary.fcgi", params=params, timeout=30)
    return resp.json().get("result", {}).get(str(uid), {})


def ncbi_gene_search(gene_name, organism="Homo sapiens", retmax=20):
    """搜索 NCBI Gene 数据库"""
    term = f"{gene_name}[Gene Name] AND {organism}[Organism]"
    return _esearch("gene", term, retmax)


def ncbi_geo_search(keyword, retmax=20):
    """搜索 GEO DataSets"""
    return _esearch("gds", f"{keyword}[All Fields]", retmax)


def ncbi_sra_search(keyword, retmax=20):
    """搜索 SRA 测序数据"""
    return _esearch("sra", keyword, retmax)


def ncbi_snp_search(term, retmax=20):
    """搜索 dbSNP 变异"""
    ids, total = _esearch("snp", term, retmax)
    results = []
    for uid in ids:
        info = _esummary("snp", uid)
        results.append({
            "uid": uid,
            "snp_id": info.get("snp_id", ""),
            "clinical_significance": info.get("clinical_significance", ""),
            "global_population": info.get("global_population", ""),
        })
        time.sleep(0.4)  # 遵守 3 次/秒限制
    return results, total


def ncbi_clinvar_search(term, retmax=20):
    """搜索 ClinVar 临床变异"""
    return _esearch("clinvar", term, retmax)


# ============================================================
# 2. Ensembl REST API
# ============================================================

def ensembl_lookup_gene(gene_symbol, species="homo_sapiens"):
    """查询基因信息"""
    resp = requests.get(
        f"https://rest.ensembl.org/lookup/symbol/{species}/{gene_symbol}",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def ensembl_lookup_variant(rs_id, species="human"):
    """查询变异信息"""
    resp = requests.get(
        f"https://rest.ensembl.org/variation/{species}/{rs_id}",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ============================================================
# 3. GDC/TCGA REST API
# ============================================================

GDC_API = "https://api.gdc.cancer.gov"


def gdc_get_projects(size=10):
    """获取 TCGA 项目列表"""
    resp = requests.get(f"{GDC_API}/projects", params={"size": size}, timeout=30)
    return resp.json().get("data", {}).get("hits", [])


def gdc_get_cases(size=10, project_id=None):
    """获取案例列表"""
    params = {"size": size, "fields": "case_id,submitter_id,disease_type"}
    if project_id:
        params["filters"] = json.dumps({
            "op": "in",
            "content": {"field": "project.project_id", "value": [project_id]}
        })
    resp = requests.get(f"{GDC_API}/cases", params=params, timeout=30)
    return resp.json().get("data", {}).get("hits", [])


def gdc_search_files(data_category="Transcriptome Profiling", project_id=None, size=10):
    """搜索 TCGA 文件（GET + filters）"""
    filters_content = [
        {"op": "in", "content": {"field": "access", "value": ["open"]}},
        {"op": "in", "content": {"field": "data_category", "value": [data_category]}},
    ]
    if project_id:
        filters_content.append(
            {"op": "in", "content": {"field": "cases.project.project_id", "value": [project_id]}}
        )
    filters = {"op": "and", "content": filters_content}
    params = {
        "filters": json.dumps(filters),
        "fields": "file_id,file_name,data_category,data_type",
        "format": "JSON",
        "size": size,
    }
    resp = requests.get(f"{GDC_API}/files", params=params, timeout=30)
    return resp.json().get("data", {}).get("hits", [])


# ============================================================
# 4. PDB (RCSB) REST API v2
# ============================================================

def pdb_search(query_text, rows=10):
    """搜索 PDB 蛋白结构（全文搜索）"""
    query = {
        "query": {
            "type": "group",
            "logical_operator": "and",
            "nodes": [
                {
                    "type": "terminal",
                    "service": "full_text",
                    "parameters": {"value": query_text}
                }
            ]
        },
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": rows}}
    }
    resp = requests.post(
        "https://search.rcsb.org/rcsbsearch/v2/query",
        json=query,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("result_set", [])


def pdb_get_entry(pdb_id):
    """获取 PDB 条目详情"""
    resp = requests.get(
        f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ============================================================
# 5. AlphaFold DB
# ============================================================

def alphafold_get_prediction(uniprot_id):
    """获取 AlphaFold 蛋白结构预测"""
    resp = requests.get(
        f"https://alphafold.ebi.ac.uk/api/prediction/{uniprot_id}",
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0] if isinstance(data, list) else data


# ============================================================
# 6. InterPro / Pfam
# ============================================================

def interpro_get_protein(uniprot_id):
    """查询蛋白结构域"""
    resp = requests.get(
        f"https://www.ebi.ac.uk/interpro/api/protein/uniprot/{uniprot_id}",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("metadata", {})


def pfam_get_entry(pfam_id):
    """查询 Pfam 蛋白家族"""
    resp = requests.get(
        f"https://www.ebi.ac.uk/interpro/api/entry/pfam/{pfam_id}",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("metadata", {})


# ============================================================
# 7. Reactome REST API
# ============================================================

def reactome_search_pathways(query, species="Homo sapiens"):
    """搜索 Reactome 通路"""
    resp = requests.get(
        "https://reactome.org/ContentService/search/query",
        params={
            "query": query,
            "species": species.replace(" ", "+"),
            "types": "Pathway",
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def reactome_get_pathway(pathway_id):
    """获取通路详情"""
    resp = requests.get(
        f"https://reactome.org/ContentService/data/query/{pathway_id}",
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ============================================================
# 8. g:Profiler 富集分析
# ============================================================

def gprofiler_enrichment(gene_list, organism="hsapiens"):
    """基因列表功能富集分析"""
    resp = requests.post(
        "https://biit.cs.ut.ee/gprofiler/api/gost/profile/",
        json={"organism": organism, "query": gene_list},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("result", [])


# ============================================================
# 9. Enrichr 富集分析
# ============================================================

def enrichr_add_list(gene_list, description="gene list"):
    """上传基因列表到 Enrichr"""
    gene_str = "\n".join(gene_list)
    resp = requests.post(
        "https://maayanlab.cloud/Enrichr/addList",
        files={"list": ("gene_list.txt", gene_str), "description": (None, description)}
    )
    resp.raise_for_status()
    return resp.json()["userListId"]


def enrichr_get_enrichment(user_list_id, background_type="KEGG_2021_Human"):
    """获取富集结果"""
    resp = requests.get(
        "https://maayanlab.cloud/Enrichr/enrich",
        params={"userListId": user_list_id, "backgroundType": background_type},
    )
    resp.raise_for_status()
    return resp.json().get(background_type, [])


# ============================================================
# 10. JASPAR 转录因子
# ============================================================

def jaspar_search(keyword, page_size=10):
    """搜索转录因子结合位点矩阵"""
    resp = requests.get(
        "https://jaspar.elixir.no/api/v1/matrix/",
        params={"search": keyword, "page_size": page_size},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


# ============================================================
# 11. gnomAD 变异频率
# ============================================================

def gnomad_get_variant_freq(chrom, pos, ref, alt, dataset="gnomad_r4"):
    """查询变异群体频率"""
    variant_id = f"{chrom}-{pos}-{ref}-{alt}"
    query = f"""
    {{
      variant(dataset: {dataset}, variantId: "{variant_id}") {{
        genome {{ ac ac_hom }}
        exome {{ ac ac_hom }}
      }}
    }}
    """
    resp = requests.post(
        "https://gnomad.broadinstitute.org/api",
        json={"query": query},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", {}).get("variant")


# ============================================================
# 12. GWAS Catalog REST API
# ============================================================

def gwas_search_studies(keyword):
    """按关键词搜索 GWAS 研究"""
    resp = requests.get(
        "https://www.ebi.ac.uk/gwas/rest/api/studies/search",
        params={"query": keyword},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("_embedded", {}).get("studies", [])


def gwas_search_by_gene(gene_name, size=10):
    """按基因搜索 GWAS 关联"""
    resp = requests.get(
        "https://www.ebi.ac.uk/gwas/rest/api/associations",
        params={"gene": gene_name, "size": size},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("_embedded", {}).get("associations", [])


# ============================================================
# 13. ENCODE REST API
# ============================================================

def encode_search_experiments(assay_title="ATAC-seq", limit=5):
    """搜索 ENCODE 实验"""
    resp = requests.get(
        "https://www.encodeproject.org/search/",
        params={
            "type": "Experiment",
            "assay_title": assay_title,
            "format": "json",
            "limit": limit,
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("@graph", [])


# ============================================================
# 14. UniProt (修正语法版)
# ============================================================

def uniprot_search_by_gene(gene_name, organism_id=9606, reviewed_only=True, size=10):
    """按基因名搜索 UniProt 蛋白"""
    query = f"gene:{gene_name} AND organism_id:{organism_id}"
    if reviewed_only:
        query += " AND reviewed:true"
    resp = requests.get(
        "https://rest.uniprot.org/uniprotkb/search",
        params={"query": query, "format": "json", "size": size},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def uniprot_get_protein(uniprot_id):
    """获取蛋白详情"""
    resp = requests.get(
        f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.json",
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ============================================================
# 综合工作流：网络药理学 → 生信分析全流程
# ============================================================

def network_pharmacology_to_bioinformatics(target_genes, disease_keyword="ulcerative colitis"):
    """
    从靶点基因列表出发，执行完整的生信分析流程

    Parameters:
        target_genes: list[str] - 靶点基因名列表
        disease_keyword: str - 疾病关键词

    Returns:
        dict - 分析结果
    """
    results = {}

    # 1. 靶点蛋白信息（UniProt）
    print("1. 查询靶点蛋白信息...")
    proteins = []
    for gene in target_genes[:10]:
        uniprot_results = uniprot_search_by_gene(gene, reviewed_only=True, size=1)
        if uniprot_results:
            acc = uniprot_results[0].get("primaryAccession", "")
            proteins.append({"gene": gene, "uniprot_id": acc})
        time.sleep(0.3)
    results["proteins"] = proteins

    # 2. PPI 网络（STRING）
    print("2. 构建 PPI 网络...")
    identifiers = "%0d".join(target_genes)
    resp = requests.get(
        "https://string-db.org/api/json/network",
        params={"identifiers": identifiers, "species": 9606, "required_score": 400},
        timeout=60,
    )
    ppi_edges = resp.json() if resp.status_code == 200 else []
    results["ppi_edges"] = len(ppi_edges)

    # 3. 功能富集（g:Profiler）
    print("3. 功能富集分析...")
    enrichment = gprofiler_enrichment(target_genes)
    top_terms = [
        {"term": r.get("description", ""), "p_value": r.get("p_value", 1)}
        for r in enrichment[:10]
    ]
    results["enrichment"] = top_terms

    # 4. 疾病相关 GEO 数据集
    print("4. 搜索疾病相关表达数据集...")
    geo_ids, geo_total = ncbi_geo_search(disease_keyword)
    results["geo_datasets"] = {"total": geo_total, "top_ids": geo_ids[:5]}

    # 5. GWAS 关联
    print("5. 搜索 GWAS 关联...")
    gwas = []
    for gene in target_genes[:5]:
        associations = gwas_search_by_gene(gene, size=3)
        for a in associations:
            gwas.append({"gene": gene, "pvalue": a.get("pvalueMantissa", 0)})
        time.sleep(0.3)
    results["gwas"] = gwas[:10]

    # 6. 蛋白结构（AlphaFold）
    print("6. 查询蛋白结构预测...")
    structures = []
    for p in proteins[:3]:
        try:
            af = alphafold_get_prediction(p["uniprot_id"])
            structures.append({
                "gene": p["gene"],
                "plddt": af.get("globalMetricValue", 0),
            })
        except Exception:
            pass
        time.sleep(0.3)
    results["structures"] = structures

    return results


# ============================================================
# 主程序入口
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="生信多库综合检索（NCBI/GEO/TCGA/PDB/AlphaFold/Reactome 等）")
    parser.add_argument("--gene", help="NCBI Gene 搜索（如 TNF）")
    parser.add_argument("--uniprot-gene", help="UniProt 按基因名搜索蛋白")
    parser.add_argument("--geo", help="GEO 数据集搜索（如 ulcerative colitis）")
    parser.add_argument("--tcga-project", help="GDC/TCGA 项目（如 TCGA-COAD）")
    parser.add_argument("--pdb", help="PDB 全文搜索（如 tumor necrosis factor）")
    parser.add_argument("--alphafold", help="AlphaFold 预测结构（UniProt ID，如 P01375）")
    parser.add_argument("--reactome", help="Reactome 通路搜索（如 TNF）")
    parser.add_argument("--enrich", nargs="+", help="g:Profiler 富集基因列表（多个基因）")
    parser.add_argument("--variant", nargs=3, metavar=("CHROM", "POS", "ALT"), help="gnomAD 变异频率查询（染色体 位置 参考碱基 突变碱基）")
    parser.add_argument("--gwas", help="GWAS 按基因搜索关联（如 TNF）")
    parser.add_argument("--encode", help="ENCODE 实验搜索（如 ATAC-seq）")
    args = parser.parse_args()

    if args.gene:
        print(f"\n--- NCBI Gene: {args.gene} ---")
        ids, total = ncbi_gene_search(args.gene)
        print(f"{args.gene} Gene: {ids} (total: {total})")

    if args.uniprot_gene:
        print(f"\n--- UniProt: {args.uniprot_gene} ---")
        hits = uniprot_search_by_gene(args.uniprot_gene)
        for h in hits:
            print(f"  {h.get('primaryAccession', '')}: {h.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value', 'N/A')}")

    if args.geo:
        print(f"\n--- GEO: {args.geo} ---")
        ids, total = ncbi_geo_search(args.geo)
        print(f"GEO datasets: {total} found, top 3: {ids[:3]}")

    if args.tcga_project:
        print(f"\n--- TCGA: {args.tcga_project} ---")
        files = gdc_search_files(project_id=args.tcga_project, size=3)
        print(f"{args.tcga_project} files: {len(files)}")
        if files:
            print(f"  Example: {files[0].get('file_name', 'N/A')}")

    if args.pdb:
        print(f"\n--- PDB: {args.pdb} ---")
        entries = pdb_search(args.pdb, rows=3)
        print(f"PDB entries: {len(entries)}")
        for e in entries:
            print(f"  {e['identifier']} (score: {e['score']:.3f})")

    if args.alphafold:
        print(f"\n--- AlphaFold: {args.alphafold} ---")
        af = alphafold_get_prediction(args.alphafold)
        print(f"{args.alphafold} pLDDT: {af.get('globalMetricValue', 'N/A')}")

    if args.reactome:
        print(f"\n--- Reactome: {args.reactome} ---")
        pathways = reactome_search_pathways(args.reactome)
        print(f"{args.reactome} pathways: {len(pathways)}")
        for p in pathways[:3]:
            entries = p.get("entries", [])
            if entries:
                print(f"  {entries[0].get('name', 'N/A')}")

    if args.enrich:
        print(f"\n--- g:Profiler enrichment ---")
        enrichment = gprofiler_enrichment(args.enrich)
        print(f"Enrichment terms: {len(enrichment)}")
        for r in enrichment[:3]:
            print(f"  {r.get('description', 'N/A')} (p={r.get('p_value', 'N/A')})")

    if args.variant:
        print(f"\n--- gnomAD variant: {args.variant} ---")
        freq = gnomad_get_variant_freq(args.variant[0], args.variant[1], args.variant[2], args.variant[3])
        if freq:
            print(f"Variant freq: genome AC={freq.get('genome', {}).get('ac', 'N/A')}")
        else:
            print("Variant not found in gnomAD")

    if args.gwas:
        print(f"\n--- GWAS: {args.gwas} ---")
        associations = gwas_search_by_gene(args.gwas, size=3)
        print(f"{args.gwas} GWAS associations: {len(associations)}")
        for a in associations[:3]:
            print(f"  p-value: {a.get('pvalueMantissa', 'N/A')}e{a.get('pvalueExponent', 'N/A')}")

    if args.encode:
        print(f"\n--- ENCODE: {args.encode} ---")
        experiments = encode_search_experiments(args.encode, limit=3)
        print(f"{args.encode} experiments: {len(experiments)}")
        for e in experiments:
            print(f"  {e.get('accession', '')}: {e.get('assay_title', '')}")

    if not any([args.gene, args.uniprot_gene, args.geo, args.tcga_project, args.pdb, args.alphafold,
                args.reactome, args.enrich, args.variant, args.gwas, args.encode]):
        parser.print_help()
