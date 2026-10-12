# -*- coding: utf-8 -*-
"""
AI Agent 工具层：数据库检索 + 工作流控制 + 数据分析
每个工具 = 一个 JSON-schema 函数，供 LLM function calling 调用
工具实现沉淀自 tcm-database-query 技能的实测 API 文档
"""
import gzip
import json
import re
import traceback

import pandas as pd
import requests

# ================= 工具注册表 =================

TOOL_REGISTRY = {}          # name -> {"schema": {...}, "func": callable, "domain": str}
WORKFLOW_CALLBACKS = {}     # 工作流控制回调，由 run_server 注入


def tool(name: str, domain: str, desc: str, params: dict, required: list = None):
    """装饰器：注册工具"""
    def deco(fn):
        TOOL_REGISTRY[name] = {
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {"type": "object", "properties": params,
                                   "required": required or []},
                },
            },
            "func": fn,
            "domain": domain,
        }
        return fn
    return deco


def _s(x, limit=2500):
    """结果安全序列化（DataFrame/异常 → 紧凑 JSON 字符串）"""
    try:
        if isinstance(x, pd.DataFrame):
            return x.to_json(orient="records", force_ascii=False)[:limit]
        return json.dumps(x, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(x)[:limit]


HTTP_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ================= 数据库检索工具（在线） =================

@tool("tcmbank_search", "TCM检索",
      "TCMBank 全库检索（61,965 中药成分/药材/靶点/疾病四实体）。返回结果列表与总条数。可搜成分名(ingredient)、药材名(herb)、基因(gene)、疾病(disease)。必须 http 不是 https。",
      {"entity": {"type": "string", "enum": ["ingredient", "herb", "gene", "disease"],
                  "description": "检索实体类型"},
       "keyword": {"type": "string", "description": "检索关键词，如 berberine / astragalus / TNF / colitis"},
       "page": {"type": "integer", "description": "页码，默认 1"},
       "per_page": {"type": "integer", "description": "每页条数，默认 10，最大 50"}},
      ["entity", "keyword"])
def tcmbank_search(entity, keyword, page=1, per_page=10):
    per_page = min(per_page, 50)
    p = {f"{entity}_name": keyword, "cur_page": page, "per_page": per_page}
    r = requests.get(f"http://tcmbank.cn/api/{entity}/search", headers=HTTP_UA,
                     params=p, timeout=30)
    d = r.json()["data"]
    total = (d.get("total_ingredients") or d.get("total_herbs")
             or d.get("total_genes") or d.get("total_diseases"))
    return {"total": total, "items": d["list"]}


@tool("tcmbank_detail", "TCM检索",
      "TCMBank 实体详情与互查：成分详情（SMILES/PubChem/CAS/ADMET 预测）、药材→成分、靶点→成分、疾病→靶点等。",
      {"entity": {"type": "string", "enum": ["ingredient", "herb", "gene", "disease"]},
       "tcmbank_id": {"type": "string", "description": "TCMBank ID，如 TCMBANKIN000001 / TCMBANKHE000243"},
       "relation": {"type": "string", "enum": ["", "toingredients", "toherbs", "totargets", "todiseases"],
                    "description": "互查关系：留空=详情；toingredients=查关联成分等"}},
      ["entity", "tcmbank_id"])
def tcmbank_detail(entity, tcmbank_id, relation=""):
    api = f"{entity}/detail" if not relation else f"{entity}/{relation}"
    r = requests.get(f"http://tcmbank.cn/api/{api}", headers=HTTP_UA,
                     params={"tcmbank_id": tcmbank_id}, timeout=30)
    return r.json()["data"]


@tool("npass_search_compound", "TCM检索",
      "NPASS 天然产物库（204,023 化合物）按名称/性质搜索。返回 NPC ID/名称/分子量/来源物种数/靶点数。注意：POST 参数 search_by_property 必须传 'SEARCH'。",
      {"compound_name": {"type": "string", "description": "化合物名，如 quercetin"},
       "mw_min": {"type": "number", "description": "分子量下限（可选）"},
       "mw_max": {"type": "number", "description": "分子量上限（可选）"}},
      ["compound_name"])
def npass_search_compound(compound_name, mw_min=None, mw_max=None):
    d = {"CompoundName": compound_name, "logPMin": "", "logPMax": "",
         "MWMin": mw_min or "", "MWMax": mw_max or "", "HdonorMin": "", "HdonorMax": "",
         "HacceptorMin": "", "HacceptorMax": "", "RotatableMin": "", "RotatableMax": "",
         "IfQuantity": "AllNPs", "search_by_property": "SEARCH"}
    r = requests.post("https://bidd.group/NPASS/search_compound.php",
                      data=d, headers=HTTP_UA, timeout=30)
    return {"rows_html_parsed": _parse_html_table(r.text), "url": r.url}


def _parse_html_table(html):
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    out = []
    for row in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if cells and any(cells):
            out.append(cells)
    return out[:30]


@tool("pubchem_lookup", "化学信息",
      "PubChem REST API：化合物名称 → CID / SMILES / 分子式 / 分子量等属性。支持批量名称查询。",
      {"names": {"type": "array", "items": {"type": "string"},
                 "description": "化合物英文名列表，最多 30 个"},
       "properties": {"type": "string",
                      "description": "逗号分隔的属性，默认 MolecularFormula,MolecularWeight,CanonicalSMILES,IsomericSMILES"}},
      ["names"])
def pubchem_lookup(names, properties="MolecularFormula,MolecularWeight,CanonicalSMILES,IsomericSMILES"):
    names = [n.strip() for n in names[:30] if n.strip()]
    results = []
    for n in names:
        try:
            r = requests.get(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{n}/property/{properties}/JSON",
                timeout=20)
            if r.status_code == 200:
                results.append({"name": n, "found": True,
                                **r.json()["PropertyTable"]["Properties"][0]})
            else:
                results.append({"name": n, "found": False})
        except Exception as e:
            results.append({"name": n, "found": False, "error": str(e)})
    return results


@tool("opentargets_search", "靶点/疾病",
      "Open Targets Platform：搜索疾病（返回 MONDO ID）或靶点。注意 queryString 参数名。UC=MONDO_0005101。",
      {"query": {"type": "string", "description": "搜索词，如 ulcerative colitis"},
       "entity": {"type": "string", "enum": ["disease", "target"],
                  "description": "实体类型，默认 disease"}},
      ["query"])
def opentargets_search(query, entity="disease"):
    q = """
    { search(queryString: "%s", entityNames: ["%s"], page: {index: 0, size: 10}) {
        total hits { id name entity } } }
    """ % (query, entity)
    r = requests.post("https://api.platform.opentargets.org/api/v4/graphql",
                      json={"query": q}, timeout=30)
    return r.json()["data"]["search"]


@tool("opentargets_disease_targets", "靶点/疾病",
      "Open Targets：疾病 → 关联靶点列表（按遗传学/文本挖掘等综合评分排序）。用于构建疾病锚定靶点集。",
      {"mondo_id": {"type": "string", "description": "疾病 MONDO ID，如 MONDO_0005101（UC）"},
       "page_size": {"type": "integer", "description": "返回靶点数，默认 50，最大 200"}},
      ["mondo_id"])
def opentargets_disease_targets(mondo_id, page_size=50):
    page_size = min(page_size, 200)
    q = """
    { disease(efoId: "%s") { id name
        associatedTargets(page: {index: 0, size: %d}) {
          count rows { target { id approvedSymbol } score } } } }
    """ % (mondo_id, page_size)
    r = requests.post("https://api.platform.opentargets.org/api/v4/graphql",
                      json={"query": q}, timeout=45)
    return r.json()["data"]["disease"]


@tool("swisstarget_predict", "反向预测",
      "SwissTargetPrediction 反向外靶点预测：SMILES → Top100 潜在靶点（2D/3D 相似性，Prob* 概率）。耗时约 60-70 秒。ioi 参数必须传 2。重原子数需 ≥5。",
      {"smiles": {"type": "string", "description": "查询分子的 SMILES"},
       "organism": {"type": "string", "enum": ["Homo_sapiens", "Mus_musculus", "Rattus_norvegicus"],
                    "description": "物种，默认人源"}},
      ["smiles"])
def swisstarget_predict(smiles, organism="Homo_sapiens"):
    S = requests.Session()
    S.headers.update(HTTP_UA)
    S.get("https://www.swisstargetprediction.ch/predict.php", timeout=30)
    r = S.post("https://www.swisstargetprediction.ch/predict.php",
               data={"organism": organism, "smiles": smiles, "ioi": "2"}, timeout=60)
    m = re.search(r'location\.replace\("([^"]+)"\)', r.text)
    if not m:
        return {"ok": False, "msg": "提交被拒（重原子<5 或 SMILES 无效）",
                "hint": "检查 SMILES 有效性"}
    import time
    time.sleep(62)
    rr = S.get("https://www.swisstargetprediction.ch/" + m.group(1), timeout=60)
    targets = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", rr.text, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 9 and cells[0]:
            targets.append({"Target": cells[0], "Common_name": cells[1],
                            "Uniprot": cells[2], "Probability": cells[5]})
    return {"ok": True, "n_targets": len(targets), "targets": targets[:50]}


@tool("ctd_chem_gene", "本地库",
      "CTD 本地库（315 万化学-基因交互）：查询某化学物的人类靶点基因。输入化学物名（支持同义词）。离线可用。",
      {"chemical": {"type": "string", "description": "化学物名，如 quercetin"},
       "ctd_dir": {"type": "string", "description": "CTD 数据目录（含 CTD_chemicals.tsv.gz 与 CTD_chem_gene_ixns.tsv.gz）"}},
      ["chemical"])
def ctd_chem_gene(chemical, ctd_dir=""):
    import os
    chem_gz = os.path.join(ctd_dir, "CTD_chemicals.tsv.gz")
    ixn_gz = os.path.join(ctd_dir, "CTD_chem_gene_ixns.tsv.gz")
    if not os.path.exists(chem_gz):
        return {"ok": False, "msg": "CTD 本地库不存在，请先下载数据文件"}
    key = chemical.strip().lower()
    # 1) 词表 → MeSH ID
    mesh_ids = set()
    with gzip.open(chem_gz, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            hit = False
            if parts[0].strip().lower() == key:
                hit = True
            elif parts[2].strip().lower() == key:
                hit = True
            else:
                for col in (11, 12):
                    if len(parts) > col and parts[col]:
                        if key in [s.strip().lower() for s in parts[col].split("|")]:
                            hit = True
                            break
            if hit:
                mesh_ids.add(parts[1].replace("MESH:", ""))
                break
    if not mesh_ids:
        return {"ok": True, "chemical": chemical, "mesh_id": None, "genes": []}
    # 2) 交互表 → 人类基因
    mid = mesh_ids.pop()
    genes = {}
    with gzip.open(ixn_gz, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 7 or parts[6] != "Homo sapiens":
                continue
            if parts[1].replace("MESH:", "") != mid:
                continue
            genes.setdefault(parts[3], set()).add(parts[9])
    return {"ok": True, "chemical": chemical, "mesh_id": mid,
            "n_genes": len(genes),
            "genes": {g: sorted(a)[:3] for g, a in list(genes.items())[:60]}}


# ================= 本地工作流数据工具（离线） =================

@tool("workflow_state", "工作流",
      "查询本地工作流当前状态：11 步执行状态/耗时/产物文件/关键数据集概况。",
      {})
def workflow_state():
    if "state" not in WORKFLOW_CALLBACKS:
        return {"ok": False, "msg": "工作流引擎未注入"}
    return WORKFLOW_CALLBACKS["state"]()


@tool("workflow_results", "工作流",
      "查询本地工作流筛选结果：Top N 候选小分子（排名/中文名/英文名/总分/命中靶点等）。",
      {"n": {"type": "integer", "description": "返回前 N 条，默认 20，最大 200"}},
      )
def workflow_results(n=20):
    if "results" not in WORKFLOW_CALLBACKS:
        return {"ok": False, "msg": "工作流引擎未注入"}
    return WORKFLOW_CALLBACKS["results"](min(n, 200))


@tool("workflow_targets", "工作流",
      "查询本地工作流统一靶点集：基因/证据层（临床锚定 or 文献机制）/权重/支撑药物数。可按基因名过滤。",
      {"gene": {"type": "string", "description": "基因符号过滤（可选），如 TNF"},
       "layer": {"type": "string", "enum": ["", "临床锚定", "文献机制", "扩展"],
                 "description": "证据层过滤（可选）"}},
      )
def workflow_targets(gene="", layer=""):
    if "targets" not in WORKFLOW_CALLBACKS:
        return {"ok": False, "msg": "工作流引擎未注入"}
    return WORKFLOW_CALLBACKS["targets"](gene, layer)


@tool("workflow_detail", "工作流",
      "查询某成分的完整循证链：命中的锚定/文献靶点、支撑药物数、Action 方向、评分明细。",
      {"compound": {"type": "string", "description": "成分英文名，如 Quercetin"}},
      ["compound"])
def workflow_detail(compound):
    if "detail" not in WORKFLOW_CALLBACKS:
        return {"ok": False, "msg": "工作流引擎未注入"}
    return WORKFLOW_CALLBACKS["detail"](compound)


@tool("compound_search", "分子库",
      "本地分子库检索（1,638 个 UC 候选成分，OB≥30 & DL≥0.18）：按名称/CAS/结构分类/来源药材查询成分的理化属性（OB/DL/SMILES/CID）。",
      {"keyword": {"type": "string", "description": "检索词（名称/CAS/药材/分类）"},
       "field": {"type": "string", "enum": ["英文名", "中文名", "CAS号", "来源药材", "化学结构大类", "任意"],
                 "description": "检索字段，默认任意"}},
      ["keyword"])
def compound_search(keyword, field="任意"):
    if "compound" not in WORKFLOW_CALLBACKS:
        return {"ok": False, "msg": "工作流引擎未注入"}
    return WORKFLOW_CALLBACKS["compound"](keyword, field)


# ================= 数据库检索工具（在线·增强） =================

@tool("string_ppi", "靶点/疾病",
      "STRING 蛋白互作网络（PPI）：基因列表 → 互作边 + 度中心性排序（Hub 识别）。"
      "identifiers 多个基因用空格分隔，score 阈值 0-1000（默认 400，即 0.4）。",
      {"genes": {"type": "array", "items": {"type": "string"},
                 "description": "基因符号列表，如 [\"TNF\",\"IL6\",\"STAT3\"]，最多 80 个"},
       "species": {"type": "integer", "description": "NCBI 物种 ID，人 9606（默认）"},
       "required_score": {"type": "integer",
                          "description": "置信度阈值 0-1000，默认 400"}},
      ["genes"])
def string_ppi(genes, species=9606, required_score=400):
    genes = [g.strip() for g in genes[:80] if g.strip()]
    if len(genes) < 2:
        return {"ok": False, "msg": "至少需要 2 个基因才能构建 PPI 网络"}
    r = requests.get("https://string-db.org/api/json/network",
                     params={"identifiers": "%0d".join(genes),
                             "species": species,
                             "required_score": required_score},
                     timeout=60)
    edges = r.json()
    deg = {}
    for e in edges:
        a, b, s = e["preferredName_A"], e["preferredName_B"], e["score"]
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    hubs = sorted(deg.items(), key=lambda x: -x[1])[:20]
    return {"ok": True, "n_genes": len(genes), "n_edges": len(edges),
            "hubs": [{"gene": g, "degree": d} for g, d in hubs],
            "edges": [{"a": e["preferredName_A"], "b": e["preferredName_B"],
                       "score": e["score"]} for e in edges[:150]]}


@tool("string_enrichment", "靶点/疾病",
      "STRING 功能富集分析：基因列表 → KEGG 通路/GO/Reactome 富集（p 值与 FDR）。"
      "注意 identifiers 用 %0d 连接，基因名需为标准符号。",
      {"genes": {"type": "array", "items": {"type": "string"},
                 "description": "基因符号列表，最多 100 个"},
       "species": {"type": "integer", "description": "NCBI 物种 ID，人 9606（默认）"}},
      ["genes"])
def string_enrichment(genes, species=9606):
    genes = [g.strip() for g in genes[:100] if g.strip()]
    if not genes:
        return {"ok": False, "msg": "基因列表为空"}
    r = requests.get("https://string-db.org/api/json/enrichment",
                     params={"identifiers": "%0d".join(genes), "species": species},
                     timeout=60)
    items = r.json()
    kegg = [{"term": x.get("description", x.get("term", "")),
             "p": x.get("p_value"), "fdr": x.get("fdr")}
            for x in items if x.get("category") == "KEGG"]
    go_bp = [{"term": x.get("description", x.get("term", "")),
              "p": x.get("p_value"), "fdr": x.get("fdr")}
             for x in items if x.get("category") == "Process"]
    return {"ok": True, "n_input": len(genes), "n_terms": len(items),
            "kegg": kegg[:20], "go_bp": go_bp[:20],
            "categories": sorted({x.get("category", "") for x in items})}


@tool("kegg_enrichment", "靶点/疾病",
      "KEGG REST 通路富集查询：基因符号列表 → 映射到 hsa 通路并统计每条通路的命中基因。"
      "返回通路 ID/名称/命中基因。注意 rest.kegg.jp 返回纯文本需按行解析。",
      {"genes": {"type": "array", "items": {"type": "string"},
                 "description": "基因符号列表，如 [\"TNF\",\"IL6\"]，最多 100 个"}},
      ["genes"])
def kegg_enrichment(genes):
    genes = [g.strip().upper() for g in genes[:100] if g.strip()]
    if not genes:
        return {"ok": False, "msg": "基因列表为空"}
    # 1) 基因符号 → KEGG hsa ID：逐个 find/hsa/{sym} 精确匹配（每个 ~0.3s）
    hsa_ids = []
    for g in genes:
        try:
            rr = requests.get(f"https://rest.kegg.jp/find/hsa/{g}", timeout=15)
            for line in rr.text.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) >= 2 and parts[0].startswith("hsa:"):
                    sym = parts[1].split(";")[0].split(",")[0].strip().upper()
                    if sym == g:
                        hsa_ids.append(parts[0].split(":")[1])
                        break
        except Exception:
            continue
    hsa_ids = list(dict.fromkeys(hsa_ids))
    if not hsa_ids:
        return {"ok": False, "msg": "基因符号均无法映射到 KEGG hsa ID"}
    # 2) link/pathway/{gene} → 通路-基因关系（gene 必须带 hsa: 前缀）
    r2 = requests.get(
        "https://rest.kegg.jp/link/pathway/"
        + "+".join(f"hsa:{i}" for i in hsa_ids[:60]),
        timeout=60)
    pathway_genes = {}
    for line in r2.text.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].startswith("hsa:") and parts[1].startswith("path:"):
            pathway_genes.setdefault(parts[1].split(":")[1], set()).add(parts[0].split(":")[1])
    if not pathway_genes:
        return {"ok": True, "n_input": len(genes), "n_mapped": len(hsa_ids),
                "pathways": [], "msg": "无通路命中"}
    # 3) 通路 ID → 名称
    pid = list(pathway_genes.keys())
    r3 = requests.get("https://rest.kegg.jp/list/pathway/" + "+".join(pid[:30]),
                      timeout=30)
    names = {}
    for line in r3.text.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].startswith("path:"):
            names[parts[0].split(":")[1]] = parts[1]
    pw = [{"pathway_id": p, "name": names.get(p, ""),
           "n_genes": len(gset), "genes": sorted(gset)[:15]}
          for p, gset in pathway_genes.items()]
    pw.sort(key=lambda x: (-x["n_genes"], x["pathway_id"]))
    return {"ok": True, "n_input": len(genes), "n_mapped": len(hsa_ids),
            "n_pathways": len(pw), "pathways": pw[:25]}


@tool("uniprot_gene", "靶点/疾病",
      "UniProt 蛋白注释查询：基因符号 → UniProt accession/蛋白名/功能/亚细胞定位/长度。"
      "query 使用 gene_exact:{name} AND organism_id:9606 AND reviewed:true 组合语法"
      "（注意 organism/organism_id 字段不可用 reviewed:yes 写法）。",
      {"gene": {"type": "string", "description": "基因符号，如 TNF"},
       "accession": {"type": "string",
                     "description": "或直接按 UniProt accession 查（如 P01375），优先级高于 gene"}},
      [])
def uniprot_gene(gene="", accession=""):
    if accession:
        r = requests.get(f"https://rest.uniprot.org/uniprotkb/{accession}.json",
                         timeout=30)
        if r.status_code != 200:
            return {"ok": False, "msg": f"accession {accession} 不存在"}
        d = r.json()
        rec = (d.get("proteinDescription", {}).get("recommendedName", {}) or {})
        return {"ok": True, "accession": d.get("primaryAccession"),
                "id": d.get("uniProtkbId"),
                "protein_name": (rec.get("fullName", {}) or {}).get("value", ""),
                "gene_names": [g.get("geneName", {}).get("value")
                               for g in d.get("genes", []) if g.get("geneName")],
                "length": d.get("sequence", {}).get("length"),
                "function": next((c["texts"][0]["value"]
                                  for c in d.get("comments", [])
                                  if c.get("commentType") == "FUNCTION"), ""),
                "subcellular": [loc["location"]["value"]
                                for c in d.get("comments", [])
                                if c.get("commentType") == "SUBCELLULAR LOCATION"
                                for loc in c.get("subcellularLocations", [])][:5]}
    if not gene:
        return {"ok": False, "msg": "需提供 gene 或 accession"}
    q = f"gene_exact:{gene} AND organism_id:9606 AND reviewed:true"
    r = requests.get("https://rest.uniprot.org/uniprotkb/search",
                     params={"query": q, "format": "json", "size": 10}, timeout=30)
    if r.status_code != 200:
        return {"ok": False, "msg": f"UniProt 查询失败 HTTP {r.status_code}"}
    out = []
    for d in r.json().get("results", [])[:10]:
        rn = (d.get("proteinDescription", {}).get("recommendedName", {}) or {})
        out.append({"accession": d.get("primaryAccession"),
                    "id": d.get("uniProtkbId"),
                    "protein_name": (rn.get("fullName", {}) or {}).get("value", ""),
                    "length": d.get("sequence", {}).get("length")})
    return {"ok": True, "gene": gene, "n_results": len(out), "results": out}


@tool("compound_screen", "分子库",
      "高级筛选：对本地评分结果/分子库做数值条件过滤 + 排序（如 总分≥2.0 且 OB≥40 按总分降序）。"
      "field 必须是数据列名，dataset 选用评分表还是原始分子库。",
      {"dataset": {"type": "string", "enum": ["scored", "molecule_lib"],
                   "description": "数据集：scored=工作流评分表，molecule_lib=原始分子库"},
       "filters": {"type": "array",
                   "items": {"type": "object", "properties": {
                       "field": {"type": "string"},
                       "op": {"type": "string", "enum": [">", ">=", "<", "<=", "==", "contains"]},
                       "value": {}},
                       "required": ["field", "op", "value"]},
                   "description": "过滤条件列表，AND 组合"},
       "sort_by": {"type": "string", "description": "排序字段"},
       "ascending": {"type": "boolean", "description": "升序，默认 false"},
       "limit": {"type": "integer", "description": "返回条数，默认 30，最大 100"}},
      ["dataset", "filters"])
def compound_screen(dataset, filters, sort_by="", ascending=False, limit=30):
    if "screen" not in WORKFLOW_CALLBACKS:
        return {"ok": False, "msg": "工作流引擎未注入"}
    return WORKFLOW_CALLBACKS["screen"](dataset, filters, sort_by, ascending,
                                        min(limit, 100))


@tool("export_report", "报告",
      "导出数据报告：把成分列表（评分表切片或筛选结果）写入 CSV 文件到 artifacts 目录，"
      "返回可下载文件名。适合给导师交付中间结果。",
      {"rows": {"type": "array", "items": {"type": "object"},
                "description": "要导出的记录列表（对象数组），最多 500 条"},
       "filename": {"type": "string", "description": "文件名（不含扩展名），默认 agent_export"},
       "note": {"type": "string", "description": "备注，写入文件首行注释"}},
      ["rows"])
def export_report(rows, filename="agent_export", note=""):
    rows = rows[:500] if isinstance(rows, list) else []
    if not rows:
        return {"ok": False, "msg": "rows 为空"}
    import csv
    import io
    import os
    from datetime import datetime
    fn = re.sub(r'[\\/:*?"<>|]', "_", str(filename))[:60] or "agent_export"
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
    os.makedirs(base, exist_ok=True)
    run_dirs = sorted(d for d in os.listdir(base)
                      if d.startswith("run_") and os.path.isdir(os.path.join(base, d)))
    out_dir = os.path.join(base, run_dirs[-1]) if run_dirs else base
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{fn}.csv")
    buf = io.StringIO()
    if note:
        buf.write(f"# {note}\n")
    buf.write(f"# exported {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()),
                            extrasaction="ignore")
    writer.writeheader()
    for r_ in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in r_.items()})
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(buf.getvalue())
    return {"ok": True, "file": os.path.basename(out_path),
            "n_rows": len(rows), "path": out_path}


# ================= 分析工具 =================

@tool("python_calc", "分析",
      "安全 Python 数据分析：在工作流数据上执行 pandas 透视/统计/筛选。expression 里可用 df 变量。",
      {"expression": {"type": "string",
                      "description": "Python 表达式，如 \"df['总分'].describe()\" 或 \"df.groupby('化学结构大类')['总分'].mean().sort_values(ascending=False)\""}},
      ["expression"])
def python_calc(expression):
    if "dataframes" not in WORKFLOW_CALLBACKS:
        return {"ok": False, "msg": "工作流引擎未注入"}
    allowed = True
    # 简单安全闸：禁危险操作
    for bad in ("import", "open(", "exec", "eval(", "__", "os.", "sys.", "subprocess"):
        if bad in expression:
            allowed = False
    if not allowed:
        return {"ok": False, "msg": "表达式含禁止的操作"}
    env = {"df": None}
    try:
        # 依次在关键数据集上尝试执行
        for name, df in WORKFLOW_CALLBACKS["dataframes"]().items():
            try:
                env["df"] = df
                res = eval(expression, {"__builtins__": {}}, env)
                if res is not None:
                    return {"ok": True, "dataset": name, "result": _s(res)}
            except NameError:
                continue
        return {"ok": False, "msg": "表达式在所有数据集上均未产生结果"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def get_tool_schemas():
    return [t["schema"] for t in TOOL_REGISTRY.values()]


def execute_tool(name: str, args: dict, emitter=None):
    """执行工具；emitter 用于向 SSE 推送进度事件"""
    if name not in TOOL_REGISTRY:
        return {"ok": False, "msg": f"未知工具: {name}"}
    if emitter:
        emitter({"type": "tool_start", "tool": name, "args": args})
    try:
        result = TOOL_REGISTRY[name]["func"](**args)
        if emitter:
            emitter({"type": "tool_end", "tool": name, "ok": True,
                     "result_preview": _s(result, 300)})
        return result
    except Exception as e:
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}"
        if emitter:
            emitter({"type": "tool_end", "tool": name, "ok": False,
                     "result_preview": err[:300]})
        return {"ok": False, "error": err}
