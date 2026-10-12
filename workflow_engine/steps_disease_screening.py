# -*- coding: utf-8 -*-
"""
L2 步骤库：疾病锚定靶点法小分子筛选工作流（疾病可配置泛化版）
数据源整合：标准化机制库 + 指南临床药 + DrugBank 靶点 + CTD 本地库 + 分子库
每个步骤：可复用原子操作，带循证溯源记录

泛化机制（v2）：
  - 疾病相关参数全部来自 config.yaml 的 disease 段（disease profile）
  - 缺省回退 UC 默认值，保证旧配置零改动可跑（向后兼容）
  - 旧入口 steps_uc_screening.build_workflow 保留为别名

兼容说明：本模块由 steps_uc_screening.py 重构而来，
历史 UCS_* 阶段（s01-s07 归档脚本）的步骤逻辑未变，仅参数化。
"""
import os
import re
import gzip

import pandas as pd

from engine import StepResult, EvidenceSource

# 配置默认路径（run_server 会用 config.yaml 覆盖）
# 开源通用版：不硬编码任何本地路径，全部由 config.yaml 的 paths 段提供
DEFAULTS = {
    "mechanism_db": "",
    "clinical_drugs": "",
    "drugbank_targets": "",
    "molecule_library": "",
    "ctd_ixns_gz": "",
    "ctd_chemicals_gz": "",
    "full_molecule_library": "",
}

EVIDENCE_WEIGHTS = {   # 证据分层权重（可被 config.scoring / disease.evidence_weights 覆盖）
    "clinical_anchor": 1.0,   # 临床药直接作用靶点
    "E1": 0.7,                # 直接实验机制证据
    "E2": 0.4,                # 组学/菌群关联证据
    "E3": 0.2,                # 综述/理论推断
}

# ---- disease profile 缺省值（等价于旧 UC 版行为，保证向后兼容） ----
DISEASE_DEFAULTS = {
    "name": "溃疡性结肠炎",
    "mesh_id": "D003093",
    "mondo_id": "MONDO_0005101",
    "aliases": ["ulcerative colitis", "UC"],
    "clinical_drugs_sheet": "UC Drug Set",
    "positive_controls": [
        {"key": "mesalazine",    "cn": "美沙拉嗪"},
        {"key": "sulfasalazine", "cn": "柳氮磺吡啶"},
        {"key": "budesonide",    "cn": "布地奈德"},
        {"key": "prednisolone",  "cn": "泼尼松龙"},
    ],
}


def _path(ctx, key):
    return ctx.params.get("paths", {}).get(key, DEFAULTS.get(key, ""))


def _disease(ctx) -> dict:
    """读取 disease profile（config.yaml disease 段），缺省回退 UC 兼容值"""
    prof = dict(DISEASE_DEFAULTS)
    user = ctx.params.get("disease", {}) or {}
    for k, v in user.items():
        if v not in (None, ""):
            prof[k] = v
    return prof


def _evidence_weights(ctx, disease: dict) -> dict:
    """证据权重：disease.evidence_weights > scoring.evidence_weights > 内置缺省"""
    w = dict(EVIDENCE_WEIGHTS)
    w.update(ctx.params.get("scoring", {}).get("evidence_weights", {}) or {})
    w.update(disease.get("evidence_weights", {}) or {})
    return w


# ============================================================
# S1 加载标准化机制库
# ============================================================

def s1_load_mechanism_db(ctx):
    dis = _disease(ctx)
    ctx.log(f"开始读取 {dis['name']} 机制数据库…")
    f = _path(ctx, "mechanism_db")
    main = pd.read_excel(f, sheet_name="标准化主表")
    ctx.report_progress(0.45, f"主表加载完成（{len(main)} 条）",
                        {"主表记录": len(main)})
    ctx.log(f"标准化主表加载完成：{len(main)} 条记录", "success")
    long_table = pd.read_excel(f, sheet_name="机制实体长表")
    ctx.report_progress(0.9, f"长表加载完成（{len(long_table)} 条）",
                        {"主表记录": len(main), "长表三元组": len(long_table)})
    ctx.log(f"机制实体长表加载完成：{len(long_table)} 条三元组", "success")
    ctx.data["mechanism_main"] = main
    ctx.data["mechanism_long"] = long_table

    ctx.evidence.add(
        claim=f"加载 {dis['name']} 有效中药标准化机制数据库（{len(main)} 条文献记录）",
        sources=[EvidenceSource(
            kind="file", ref=os.path.basename(f) + "#标准化主表+机制实体长表",
            detail=f"疾病 profile：{dis['name']}（MeSH {dis['mesh_id']} / {dis['mondo_id']}）；"
                   f"标准化主表 {len(main)} 条；机制实体长表 {len(long_table)} 条；"
                   f"含 Record_ID/DOI/证据分级/标准靶点(HGNC)/KEGG 通路 ID")],
        transform="读取两个核心 Sheet，供后续靶点提取与循证引用",
        step="s1_load_mechanism_db")
    return StepResult(ok=True, outputs={
        "主表记录数": len(main), "长表三元组数": len(long_table),
        "药物数": int(main["药物名称"].nunique()),
        "覆盖分类数": int(main["药物分类"].nunique())},
        metrics={"records": len(main), "triples": len(long_table)})


# ============================================================
# S2 加载临床药集合
# ============================================================

def s2_load_clinical_drugs(ctx):
    dis = _disease(ctx)
    ctx.log("读取指南临床药集合…")
    f = _path(ctx, "clinical_drugs")
    sheet = dis.get("clinical_drugs_sheet", DISEASE_DEFAULTS["clinical_drugs_sheet"])
    drugs = pd.read_excel(f, sheet_name=sheet)
    final = drugs[drugs["最终纳入"] == "是"].copy()
    ctx.data["clinical_drugs"] = final
    ctx.report_progress(1.0, f"最终纳入 {len(final)} 种",
                        {"候选": len(drugs), "纳入": len(final)})
    ctx.log(f"候选 {len(drugs)} 种 → 最终纳入 {len(final)} 种", "success")

    # 指南背书列统计（存在才统计，泛化时指南列可不同）
    backings = {}
    for col in ("ACG 2025", "AGA living", "ECCO"):
        if col in final.columns:
            backings[col] = int((final[col] == "是").sum())
    acg = backings.get("ACG 2025", "-")
    aga = backings.get("AGA living", "-")
    ecco = backings.get("ECCO", "-")
    ctx.evidence.add(
        claim=f"加载 {dis['name']} 临床药物集合（指南背书，最终纳入 {len(final)} 种）",
        sources=[EvidenceSource(
            kind="file", ref=f"{os.path.basename(f)}#{sheet}",
            detail=f"共 {len(drugs)} 种候选；最终纳入 {len(final)}；"
                   f"指南背书统计：{'; '.join(f'{k}={v}' for k, v in backings.items()) or '未配置指南列'}")],
        transform="按『最终纳入』列过滤，作为临床锚定靶点的药物来源",
        step="s2_load_clinical_drugs")
    return StepResult(ok=True, outputs={
        "候选药物": len(drugs), "最终纳入": len(final),
        "ACG背书": acg, "AGA背书": aga, "ECCO背书": ecco},
        metrics={"drugs": len(final)})


# ============================================================
# S3 加载 DrugBank 靶点表
# ============================================================

def s3_load_drugbank_targets(ctx):
    ctx.log("读取 DrugBank 靶点表并拆分多基因…")
    f = _path(ctx, "drugbank_targets")
    df = pd.read_excel(f)
    ctx.report_progress(0.5, f"原始 {len(df)} 条", {"原始记录": len(df)})
    ctx.data["drugbank_raw"] = df

    # 拆分分号分隔的多基因/多蛋白
    rows = []
    for _, r in df.iterrows():
        genes = [g.strip() for g in str(r["Gene Symbol"]).split(";")
                 if g.strip() and g.strip() != "N.A."]
        for g in genes:
            rows.append({
                "Drug": r["Drug"], "Drug_class": r["Drug class"],
                "DrugBank_ID": r["DrugBank ID"], "Gene": g,
                "Target_name": r["Target name"],
                "UniProt": r["UniProt ID"],
                "Pharm_action": r["Pharmacological action"],
                "Action": r["Action"]})
    pairs = pd.DataFrame(rows)
    ctx.data["drugbank_pairs"] = pairs
    n_drugs = pairs["Drug"].nunique()
    n_genes = pairs["Gene"].nunique()
    ctx.report_progress(1.0, f"展开后 {len(pairs)} 对",
                        {"原始记录": len(df), "药物-基因对": len(pairs),
                         "药物数": int(n_drugs), "基因数": int(n_genes)})
    ctx.log(f"多基因拆分完成：{len(df)} 条 → {len(pairs)} 对（{n_drugs} 药 × {n_genes} 基因）", "success")

    ctx.evidence.add(
        claim=f"加载 DrugBank 临床药物作用靶点（{n_drugs} 药 × {n_genes} 基因）",
        sources=[EvidenceSource(
            kind="file", ref=os.path.basename(f) + "#Sheet1",
            detail=f"原始 {len(df)} 条药物-靶点记录，展开多基因后 {len(pairs)} 对；"
                   f"含 UniProt ID/药理作用/Action 方向")],
        transform="拆分分号分隔的多基因符号（如 'P23219; P35354'），生成规范药物-基因对",
        step="s3_load_drugbank_targets")
    return StepResult(ok=True, outputs={
        "原始记录": len(df), "药物-基因对": len(pairs),
        "药物数": int(n_drugs), "基因数": int(n_genes)},
        metrics={"pairs": len(pairs), "genes": int(n_genes)})


# ============================================================
# S4 构建临床锚定靶点集
# ============================================================

def s4_clinical_anchor_targets(ctx):
    dis = _disease(ctx)
    ctx.log("计算临床药 × DrugBank 靶点交集…")
    pairs: pd.DataFrame = ctx.data["drugbank_pairs"]
    drugs: pd.DataFrame = ctx.data["clinical_drugs"]

    # 仅保留最终纳入药物的靶点
    drug_names = set(drugs["Drug"])
    anchored = pairs[pairs["Drug"].isin(drug_names)].copy()
    ctx.report_progress(0.6, f"纳入药物靶点对 {len(anchored)} 条",
                        {"纳入药物靶点对": len(anchored)})
    # 基因符号规范（大写）
    anchored["Gene"] = anchored["Gene"].str.upper().str.strip()
    anchor_genes = sorted(anchored["Gene"].unique())

    # 每基因的支撑药物数
    gene_support = (anchored.groupby("Gene")
                    .agg(n_drugs=("Drug", "nunique"),
                         drug_classes=("Drug_class", lambda x: "；".join(sorted(set(x)))),
                         actions=("Action", lambda x: "；".join(sorted(set(str(i) for i in x)))))
                    .reset_index().sort_values("n_drugs", ascending=False))
    ctx.data["anchor_genes"] = anchor_genes
    ctx.data["anchor_gene_support"] = gene_support
    ctx.data["anchor_pairs"] = anchored
    ctx.report_progress(1.0, f"锚定靶点 {len(anchor_genes)} 个",
                        {"锚定靶点": len(anchor_genes),
                         "多药支撑(≥2药)": int((gene_support["n_drugs"] >= 2).sum())})
    ctx.log(f"锚定靶点集构建完成：{len(anchor_genes)} 个基因，"
            f"Top支撑：{', '.join(gene_support.head(3)['Gene'])}", "success")

    ctx.evidence.add(
        claim=f"构建临床锚定靶点集（{len(anchor_genes)} 个基因，来自 {anchored['Drug'].nunique()} 种临床药）",
        sources=[EvidenceSource(
            kind="computed",
            ref=f"drugbank临床药物作用靶点.xlsx ∩ {os.path.basename(_path(ctx, 'clinical_drugs'))}",
            detail=f"疾病：{dis['name']}；DrugBank 药物-基因对按『最终纳入』药物过滤；"
                   f"Top 支撑基因：{', '.join(gene_support.head(5)['Gene'])}")],
        transform="药物-基因对 ∩ 最终纳入药物 → 基因集合（大写规范化）",
        step="s4_clinical_anchor_targets")
    return StepResult(ok=True, outputs={
        "锚定靶点数": len(anchor_genes),
        "覆盖药物数": int(anchored["Drug"].nunique()),
        "多药支撑靶点(≥2药)": int((gene_support["n_drugs"] >= 2).sum())},
        metrics={"anchor_genes": len(anchor_genes)})


# ============================================================
# S5 构建文献机制靶点集
# ============================================================

def s5_mechanism_targets(ctx):
    dis = _disease(ctx)
    ctx.log("提取 Target 型条目并按证据分级加权…")
    weights = _evidence_weights(ctx, dis)
    long_table: pd.DataFrame = ctx.data["mechanism_long"]
    # Target 型条目（HGNC symbol 为主）
    gene_rows = long_table[long_table["Target_Type"] == "Target"].copy()
    gene_rows["Gene"] = gene_rows["Target"].astype(str).str.upper().str.strip()
    gene_rows = gene_rows[gene_rows["Gene"].str.match(r"^[A-Z0-9@\-\.]+$")]

    # 证据等级映射权重
    def weight_of(level):
        for k, v in weights.items():
            if k in str(level):
                return v
        return 0.2
    gene_rows["weight"] = gene_rows["Evidence_Level"].apply(weight_of)

    # 基因 × 证据等级聚合
    gene_ev = (gene_rows.groupby("Gene")
               .agg(n_records=("Record_ID", "nunique"),
                    evidence_levels=("Evidence_Level", lambda x: "；".join(sorted(set(x)))),
                    max_weight=("weight", "max"),
                    herbs=("中药名称", lambda x: "；".join(sorted(set(x))[:6])))
               .reset_index())
    mech_genes = set(gene_ev["Gene"])
    ctx.data["mechanism_genes"] = mech_genes
    ctx.data["mechanism_gene_table"] = gene_ev
    ctx.report_progress(0.7, f"长表机制基因 {len(mech_genes)} 个",
                        {"长表Target条目": len(gene_rows), "机制基因": len(mech_genes)})

    # 高价值靶点（出现在标准靶点列的）
    main: pd.DataFrame = ctx.data["mechanism_main"]
    main_targets = set()
    for v in main["标准靶点/HGNC"].dropna():
        for g in str(v).replace("；", ";").split(";"):
            g = g.strip().upper()
            if g and g not in ("未提取到明确分子靶点", "NAN"):
                main_targets.add(g)
    ctx.data["main_table_genes"] = main_targets
    ctx.report_progress(1.0, f"主表标准靶点 {len(main_targets)} 个",
                        {"主表标准靶点基因": len(main_targets),
                         "机制基因（含长表）": len(mech_genes | main_targets)})
    ctx.log(f"文献机制靶点集：长表 {len(mech_genes)} + 主表 {len(main_targets)} 个基因", "success")

    ctx.evidence.add(
        claim=f"构建文献机制靶点集（{len(mech_genes)} 个 HGNC 基因，来自 {len(main)} 条文献）",
        sources=[EvidenceSource(
            kind="file",
            ref=f"{os.path.basename(_path(ctx, 'mechanism_db'))}#机制实体长表",
            detail=f"疾病：{dis['name']}；Target 型条目 {len(gene_rows)} 行"
                   f"（HGNC symbol {int((gene_rows['ID_System']=='HGNC symbol').sum())} 条）；"
                   f"证据等级 E1/E2 分层加权 {weights['E1']}/{weights['E2']}")],
        transform="提取 Target_Type=Target 条目，基因符号大写规范化，按证据等级加权聚合",
        step="s5_mechanism_targets")
    return StepResult(ok=True, outputs={
        "机制靶点基因": len(mech_genes),
        "长表Target条目": len(gene_rows),
        "主表标准靶点基因": len(main_targets)},
        metrics={"mechanism_genes": len(mech_genes)})


# ============================================================
# S6 靶点集整合与分层
# ============================================================

def s6_integrate_targets(ctx):
    dis = _disease(ctx)
    ctx.log("整合三层靶点集并加权…")
    anchor_genes: list = ctx.data["anchor_genes"]
    anchor_support: pd.DataFrame = ctx.data["anchor_gene_support"]
    mech_genes: set = ctx.data["mechanism_genes"]
    main_genes: set = ctx.data["main_table_genes"]
    weights = _evidence_weights(ctx, dis)

    all_genes = sorted(set(anchor_genes) | mech_genes | main_genes)
    rows = []
    for g in all_genes:
        is_anchor = g in set(anchor_genes)
        in_mech = g in mech_genes or g in main_genes
        if is_anchor:
            layer, w = "临床锚定", weights.get("clinical_anchor", 1.0)
        elif in_mech:
            layer, w = "文献机制", weights.get("E2", 0.4)
        else:
            layer, w = "扩展", 0.1
        rows.append({"Gene": g, "Layer": layer, "Weight": w,
                     "IsAnchor": is_anchor, "InMechanism": in_mech})
    unified = pd.DataFrame(rows)

    # 补充支撑信息
    sup = anchor_support.set_index("Gene")
    unified["Anchor_n_drugs"] = unified["Gene"].map(
        sup["n_drugs"].to_dict()).fillna(0).astype(int)
    unified["Anchor_actions"] = unified["Gene"].map(
        sup["actions"].to_dict()).fillna("")

    overlap = set(anchor_genes) & mech_genes
    ctx.data["unified_targets"] = unified
    ctx.data["anchor_mech_overlap"] = sorted(overlap)
    ctx.report_progress(1.0, f"统一靶点 {len(unified)} 个",
                        {"统一靶点": len(unified), "临床锚定": len(anchor_genes),
                         "文献机制": len(mech_genes | main_genes), "双证据交集": len(overlap)})
    ctx.log(f"靶点整合完成：{len(unified)} 个（锚定 {len(anchor_genes)} / "
            f"文献 {len(mech_genes | main_genes)} / 交集 {len(overlap)}）", "success")

    ctx.evidence.add(
        claim=f"靶点集整合：{len(all_genes)} 个统一靶点（临床锚定 {len(anchor_genes)} + 文献机制 {len(mech_genes | main_genes)}，交集 {len(overlap)}）",
        sources=[EvidenceSource(
            kind="computed", ref="临床锚定靶点集 ∪ 文献机制靶点集",
            detail=f"疾病：{dis['name']}；交集基因（双证据支撑）：{', '.join(sorted(overlap)[:15])}"
                   + ("..." if len(overlap) > 15 else ""))],
        transform=f"并集整合 + 分层加权（临床锚定 {weights.get('clinical_anchor', 1.0)} / "
                  f"文献机制 {weights.get('E2', 0.4)} / 扩展 0.1）",
        step="s6_integrate_targets")
    return StepResult(ok=True, outputs={
        "统一靶点总数": len(unified), "临床锚定层": int((unified["Layer"] == "临床锚定").sum()),
        "文献机制层": int((unified["Layer"] == "文献机制").sum()),
        "双证据交集": len(overlap)},
        metrics={"unified_targets": len(unified)})


# ============================================================
# S7 加载分子库
# ============================================================

def s7_load_molecule_library(ctx):
    dis = _disease(ctx)
    ctx.log("读取分子库 Excel…")
    f = _path(ctx, "molecule_library")
    lib = pd.read_excel(f)
    ctx.data["molecule_lib"] = lib

    n_valid_ob = int(lib["OB(%)"].notna().sum()) if "OB(%)" in lib else 0
    n_valid_dl = int(lib["DL"].notna().sum()) if "DL" in lib else 0
    n_cid = int(lib["PubChem CID"].notna().sum()) if "PubChem CID" in lib else 0
    ctx.report_progress(1.0, f"{len(lib)} 个成分",
                        {"候选成分": len(lib), "有OB": n_valid_ob,
                         "有DL": n_valid_dl, "有CID": n_cid})
    ctx.log(f"分子库加载完成：{len(lib)} 成分（OB {n_valid_ob}/DL {n_valid_dl}/CID {n_cid}）", "success")
    ctx.evidence.add(
        claim=f"加载 {dis['name']} 中药有效成分分子库（OB/DL 筛选版，{len(lib)} 个成分）",
        sources=[EvidenceSource(
            kind="file", ref=os.path.basename(f),
            detail=f"疾病：{dis['name']}；含 OB/DL/SMILES/PubChem CID/CAS；"
                   f"有效 OB {n_valid_ob} 条、DL {n_valid_dl} 条、CID {n_cid} 条")],
        transform="读取筛选版分子库作为候选小分子全集",
        step="s7_load_molecule_library")
    return StepResult(ok=True, outputs={
        "候选成分数": len(lib), "有OB值": n_valid_ob,
        "有DL值": n_valid_dl, "有PubChemCID": n_cid},
        metrics={"compounds": len(lib)})


# ============================================================
# S8 CTD 匹配（成分 → 靶点）
# ============================================================

CTD_IXN_COLS = ["ChemicalName", "ChemicalID", "CasRN", "GeneSymbol", "GeneID",
                "Organism", "OrganismID", "Interaction", "InteractionActions",
                "PubMedIDs"]


# 名称规范化：去常见前缀/盐型后缀，提升匹配率
_PREFIX_RE = re.compile(r"^(?:[+\-]?(?:[0-9a-z]+(?:alpha|beta)?)[\s\-]*)", re.I)
_SALT_RE = re.compile(r"\s+(?:hydrochloride|chloride|sodium|potassium|"
                      r"bromide|sulfate|citrate|phosphate|acetate|tartrate|"
                      r"hydrate|dihydrate|monohydrate|trihydrate)$", re.I)


def _norm_name(name: str) -> str:
    """成分名规范化：小写、去括号盐型、去手性前缀，保留主干"""
    s = str(name).strip().lower()
    s = re.sub(r"\(.*?\)", "", s)          # 去括号内容（盐型/异构体标注）
    s = _SALT_RE.sub("", s)
    s = _PREFIX_RE.sub("", s)
    s = re.sub(r"[\s,;]+", " ", s).strip()
    return s


def _iter_ctd_human(gz_path, human_ids=("Homo sapiens",)):
    """流式读取 CTD 化学-基因交互（gzip），yield 人类行"""
    with gzip.open(gz_path, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 7:
                continue
            if parts[6] in human_ids:      # Organism
                yield parts


def s8_ctd_matching(ctx):
    dis = _disease(ctx)
    ixn_path = _path(ctx, "ctd_ixns_gz")
    chem_path = _path(ctx, "ctd_chemicals_gz")
    lib: pd.DataFrame = ctx.data["molecule_lib"]
    unified = ctx.data["unified_targets"]
    target_genes = set(unified["Gene"])

    # 1. 构建成分检索键：英文名 + 规范化名 + CAS + CID
    lib_keys = {}      # key -> (row_index, key_type)
    for idx, r in lib.iterrows():
        en = str(r.get("英文名", "") or "").strip().lower()
        if en:
            lib_keys[en] = (idx, "name")
            lib_keys[_norm_name(en)] = (idx, "norm")
        cas = str(r.get("CAS号", "") or "").strip()
        if cas and cas != "nan":
            lib_keys[cas] = (idx, "cas")
        cid = r.get("PubChem CID")
        if pd.notna(cid) and str(cid).strip() not in ("", "nan"):
            lib_keys[str(int(cid)) if isinstance(cid, float) else str(cid)] = (idx, "cid")

    # 2. 构建 CTD ChemicalID 索引（名称/CAS → ChemicalID MESH:xxxx）
    chem_ids = {}      # key(lower) -> ChemicalID
    with gzip.open(chem_path, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            name, chem_id = parts[0].lower(), parts[1]
            chem_ids[name] = chem_id
            chem_ids[_norm_name(name)] = chem_id
            if len(parts) >= 3 and parts[2]:     # CasRN
                chem_ids[parts[2]] = chem_id

    # 3. 流式扫交互表，三级键匹配（ChemicalName / ChemicalID / CasRN）
    matched = {}
    n_rows = 0
    for parts in _iter_ctd_human(ixn_path):
        n_rows += 1
        cname, cid_, cas, gene = parts[0], parts[1], parts[2], parts[3]
        hit = (lib_keys.get(cname.lower())
               or lib_keys.get(_norm_name(cname))
               or lib_keys.get(cas)
               or (lib_keys.get(str(cid_).split(":")[-1]) if cid_ else None))
        if hit and gene in target_genes:
            idx = hit[0]
            d = matched.setdefault(idx, {"genes": set(), "rows": []})
            d["genes"].add(gene)
            if len(d["rows"]) < 20:
                d["rows"].append(dict(zip(CTD_IXN_COLS, parts)))
        if n_rows % 500000 == 0:
            ctx.report_progress(0.05 + 0.55 * min(n_rows / 3.5e7, 1),
                                f"扫描 {n_rows:,} 行（命中成分 {len(matched)}）",
                                {"已扫描行": n_rows, "命中成分": len(matched)})

    ctx.report_progress(0.65, f"扫描完成：命中 {len(matched)} 个成分",
                        {"命中成分": len(matched), "交互总行": n_rows})
    ctx.data["compound_targets"] = matched
    ctx.log(f"CTD 匹配完成：{len(matched)}/{len(lib)} 成分有靶点命中（共扫 {n_rows:,} 行）", "success")

    ctx.evidence.add(
        claim=f"CTD 本地库匹配完成：{len(matched)}/{len(lib)} 个候选成分命中 {dis['name']} 相关靶点",
        sources=[EvidenceSource(
            kind="file", ref=os.path.basename(ixn_path) + " + " + os.path.basename(chem_path),
            detail=f"流式扫描 {n_rows:,} 行人类交互记录；"
                   f"三级键匹配（ChemicalName/ChemicalID(MESH)/CasRN）；"
                   f"靶点集来自统一靶点 {len(target_genes)} 个基因")],
        transform="成分名规范化 + CTD ChemicalID 索引 → 化学-基因交互三级键匹配",
        step="s8_ctd_matching")
    return StepResult(ok=True, outputs={
        "命中成分数": len(matched), "候选成分总数": len(lib),
        "扫描交互行": n_rows},
        metrics={"matched": len(matched), "rows": n_rows})


# ============================================================
# S9 多源加权评分
# ============================================================

def s9_scoring(ctx):
    dis = _disease(ctx)
    ctx.log("计算靶点覆盖分 + ADME 分…")
    sc = ctx.params.get("scoring", {})
    w_target = sc.get("target_weight", 0.7)
    w_adme = sc.get("adme_weight", 0.3)
    ob_cut = sc.get("ob_cutoff", 30.0)
    dl_cut = sc.get("dl_cutoff", 0.18)
    support_bonus = sc.get("support_bonus", 0.08)
    support_cap = sc.get("support_cap", 0.30)
    anchor_bonus = sc.get("anchor_coverage_bonus", 0.5)

    lib: pd.DataFrame = ctx.data["molecule_lib"]
    unified = ctx.data["unified_targets"].set_index("Gene")
    matched: dict = ctx.data["compound_targets"]
    anchor_genes: list = ctx.data["anchor_genes"]
    anchor_set = set(anchor_genes)
    mech_set = ctx.data["mechanism_genes"] | ctx.data["main_table_genes"]
    anchor_total = max(len(anchor_set), 1)
    weights = _evidence_weights(ctx, dis)

    inter = []
    n_done = 0
    for idx, d in matched.items():
        r = lib.loc[idx]
        genes = d["genes"]
        cover = 0.0
        support_gain = 0.0
        anchor_hits, mech_hits = [], []
        for g in genes:
            if g in anchor_set:
                cover += weights.get("clinical_anchor", 1.0)
                anchor_hits.append(g)
                # 药物支撑增益：靶点被 ≥2 种临床药支撑时加权
                n_drug = int(unified.loc[g, "Anchor_n_drugs"]) if g in unified.index else 0
                if n_drug >= 2:
                    support_gain += support_bonus * min(n_drug, 4) / 4
            elif g in mech_set:
                cover += weights.get("E2", 0.4)
                mech_hits.append(g)
        ob = float(r["OB(%)"]) if pd.notna(r.get("OB(%)")) else ob_cut
        dl = float(r["DL"]) if pd.notna(r.get("DL")) else dl_cut
        adme = min(ob / ob_cut, 1.0) * 0.5 + min(dl / dl_cut, 1.0) * 0.5
        inter.append({"idx": idx, "r": r, "cover": cover, "adme": adme,
                      "support_gain": min(support_gain, support_cap),
                      "anchor_hits": anchor_hits, "mech_hits": mech_hits,
                      "genes": genes, "ob": ob, "dl": dl})
        n_done += 1
        if n_done % 50 == 0:
            ctx.report_progress(0.3 + 0.5 * n_done / max(len(matched), 1),
                                f"评分中 {n_done}/{len(matched)}",
                                {"已评分": n_done, "总成分": len(matched)})

    # 归一基准：本次运行实际最大覆盖分（避免饱和，保留区分度）
    max_cover = max((x["cover"] for x in inter), default=1.0)
    if max_cover <= 0:
        max_cover = 1.0
    ctx.report_progress(0.85, f"归一基准 max_cover={max_cover:.1f}",
                        {"归一基准": round(max_cover, 2)})

    rows = []
    for x in inter:
        anchor_cov = len(x["anchor_hits"]) / anchor_total
        # 总分 = 靶点覆盖（含支撑增益）+ 锚定覆盖率因子 + ADME
        cover_norm = (x["cover"] / max_cover)
        target_part = (cover_norm + x["support_gain"]) * 5.0
        anchor_part = anchor_cov * anchor_bonus * 5.0
        adme_part = x["adme"] * 5.0
        total = (w_target * target_part
                 + w_target * anchor_part * 0.3
                 + w_adme * adme_part)
        r = x["r"]
        rows.append({
            "中文名": r.get("中文名", ""), "英文名": r.get("英文名", ""),
            "PubChem_CID": r.get("PubChem CID", ""),
            "CAS号": r.get("CAS号", ""), "分子式": r.get("分子式", ""),
            "分子量": r.get("分子量", ""), "OB": x["ob"], "DL": x["dl"],
            "化学结构大类": r.get("化学结构大类", "") if "化学结构大类" in lib.columns else "",
            "来源药材": r.get("来源药材", "") if "来源药材" in lib.columns else "",
            "命中靶点数": len(x["genes"]),
            "锚定靶点命中数": len(x["anchor_hits"]),
            "文献靶点命中数": len(x["mech_hits"]),
            "锚定覆盖率": round(anchor_cov, 4),
            "支撑增益": round(x["support_gain"], 3),
            "命中锚定靶点": "；".join(sorted(x["anchor_hits"])[:10]),
            "命中文献靶点": "；".join(sorted(x["mech_hits"])[:10]),
            "靶点覆盖分": round(x["cover"], 3), "ADME分": round(x["adme"], 3),
            "总分": round(total, 3)})
    scored = pd.DataFrame(rows).sort_values("总分", ascending=False).reset_index(drop=True)
    scored.insert(0, "排名", range(1, len(scored) + 1))
    ctx.data["scored"] = scored
    ctx.report_progress(1.0, f"评分完成：{len(scored)} 成分",
                        {"评分成分": len(scored),
                         "Top1": str(scored.iloc[0]["英文名"]) if len(scored) else "-"})

    top10 = scored.head(10)
    ctx.evidence.add(
        claim=f"候选小分子评分完成：{len(scored)} 个成分评分，Top10 = "
              f"{', '.join(top10['英文名'].astype(str))}",
        sources=[EvidenceSource(
            kind="computed",
            ref=f"评分公式：靶点覆盖×{w_target}（含药物支撑增益）+ 锚定覆盖率因子 + ADME×{w_adme}",
            detail=f"疾病：{dis['name']}；"
                   f"靶点覆盖 = Σ命中靶点权重（临床锚定 {weights.get('clinical_anchor', 1.0)}/"
                   f"文献 {weights.get('E2', 0.4)}）"
                   f" + 药物支撑增益（≥2 药支撑靶点 +{support_bonus}/档，封顶 {support_cap}），"
                   f"按本次最大覆盖分 {max_cover:.1f} 归一×5；"
                   f"锚定覆盖率 = 锚定命中数/{anchor_total}；"
                   f"ADME = OB/{ob_cut}×0.5 + DL/{dl_cut}×0.5")],
        transform="成分×靶点命中 → 加权评分排序（最大值归一 + 支撑增益 + 锚定覆盖率）",
        step="s9_scoring")
    return StepResult(ok=True, outputs={
        "评分成分数": len(scored),
        "锚定靶点命中成分": int((scored["锚定靶点命中数"] > 0).sum()),
        "Top1": str(top10.iloc[0]["英文名"]) if len(top10) else "-",
        "Top1总分": float(top10.iloc[0]["总分"]) if len(top10) else 0},
        metrics={"scored": len(scored), "max_cover": round(max_cover, 2)})


# ============================================================
# S10 阳性药验证（锚定有效性验证，疾病 profile 驱动）
# ============================================================

def s10_positive_control(ctx):
    """用疾病 profile 中的阳性药验证锚定靶点集：
    1) 完整性：阳性药 DrugBank 靶点是否全部进入锚定集（应 100%）
    2) 留一冗余度：阳性药靶点中被其他临床药也支撑的比例（衡量靶点集稳健性）
    """
    dis = _disease(ctx)
    positives = dis.get("positive_controls", DISEASE_DEFAULTS["positive_controls"])
    ctx.log(f"阳性药验证：{ '、'.join(p['cn'] for p in positives) }…")
    scored: pd.DataFrame = ctx.data["scored"]
    anchor_genes: list = ctx.data["anchor_genes"]
    anchor_pairs: pd.DataFrame = ctx.data["anchor_pairs"]
    anchor_set = set(anchor_genes)

    results = []
    for p in positives:
        key, cn = p["key"], p["cn"]
        sub = anchor_pairs[anchor_pairs["Drug"].str.lower().str.contains(key, na=False)]
        if not len(sub):
            results.append({"阳性药": cn, "DrugBank靶点数": 0, "进入锚定集": 0,
                            "完整性": "0%", "被其他药支撑": 0, "留一冗余度": "-"})
            continue
        drug_genes = set(sub["Gene"])
        in_anchor = drug_genes & anchor_set
        # 留一：其他药物支撑的靶点
        this_drugs = set(sub["Drug"].str.lower())
        other = anchor_pairs[~anchor_pairs["Drug"].str.lower().isin(this_drugs)]
        other_genes = set(other["Gene"])
        redundant = drug_genes & other_genes
        results.append({
            "阳性药": cn, "DrugBank靶点数": len(drug_genes),
            "进入锚定集": len(in_anchor),
            "完整性": f"{len(in_anchor)/len(drug_genes)*100:.0f}%",
            "被其他药支撑": len(redundant),
            "留一冗余度": f"{len(redundant)/len(drug_genes)*100:.0f}%"})
    pos_df = pd.DataFrame(results)
    ctx.data["positive_control"] = pos_df
    avg_red = pos_df["留一冗余度"].str.rstrip("%").apply(
        lambda x: float(x) if x != "-" else 0).mean()
    ctx.report_progress(1.0, "验证完成",
                        {"阳性药": len(results),
                         "平均完整性": "100%" if all(r["完整性"] == "100%" for r in results) else "见明细",
                         "平均冗余度": f"{avg_red:.0f}%"})

    ctx.evidence.add(
        claim=f"阳性药验证：{len(positives)} 个 {dis['name']} 阳性药靶点进入锚定集"
              f"（完整性 {'、'.join(r['完整性'] for r in results)}），"
              f"留一冗余度平均 {avg_red:.0f}%",
        sources=[EvidenceSource(
            kind="computed", ref="drugbank临床药物作用靶点.xlsx 内阳性药子集",
            detail=f"阳性药清单来自 disease profile："
                   f"{'; '.join(p['key'] + '=' + p['cn'] for p in positives)}；"
                   f"完整性=阳性药靶点∩锚定集/阳性药靶点；"
                   f"留一冗余度=被其他临床药支撑的靶点比例（越高说明靶点集越稳健）")],
        transform="完整性校验 + 留一法（leave-one-out）靶点冗余度计算",
        step="s10_positive_control")
    return StepResult(ok=True, outputs={
        "阳性药": "、".join(pos_df["阳性药"]),
        "平均留一冗余度": f"{avg_red:.0f}%",
        "完整性": "、".join(pos_df["完整性"])},
        metrics={"positive_control": "passed" if all(r["完整性"] == "100%" for r in results) else "review",
                 "avg_redundancy": round(avg_red, 3)})


# ============================================================
# S11 产物生成（疾病名前缀）
# ============================================================

def s11_output_artifacts(ctx):
    dis = _disease(ctx)
    dname = dis["name"]
    ctx.log("导出评分表与循证明细…")
    run_dir = ctx.artifacts_dir
    artifacts = []

    # 1. 完整评分表（含新列：锚定覆盖率/支撑增益/结构分类/作用方向）
    scored: pd.DataFrame = ctx.data["scored"]
    p1 = ctx.artifact_path(f"{dname}候选小分子_临床锚定评分.xlsx")
    with pd.ExcelWriter(p1, engine="openpyxl") as w:
        scored.head(200).to_excel(w, sheet_name="Top200评分", index=False)
        if "unified_targets" in ctx.data:
            ctx.data["unified_targets"].to_excel(w, sheet_name="统一靶点集", index=False)
        if "positive_control" in ctx.data:
            ctx.data["positive_control"].to_excel(w, sheet_name="阳性药验证", index=False)
        if "anchor_gene_support" in ctx.data:
            ctx.data["anchor_gene_support"].to_excel(w, sheet_name="锚定靶点支撑", index=False)
        # CTD 交互明细（新增）
        if "ctd_ixn_detail" in ctx.data:
            ixn = ctx.data["ctd_ixn_detail"].merge(
                scored[["排名", "英文名"]], left_on="lib_idx", right_index=True,
                how="left")
            ixn.to_excel(w, sheet_name="CTD交互明细", index=False)
    artifacts.append(p1)
    ctx.report_progress(0.4, "评分表已导出", {"已导出": 1, "总产物": 3})

    # 2. 溯源明细（每成分的证据链）
    ev_rows = []
    unified = ctx.data["unified_targets"].set_index("Gene")
    for _, r in scored.head(100).iterrows():
        anchor_hits = [g for g in str(r["命中锚定靶点"]).split("；") if g] if pd.notna(r["命中锚定靶点"]) else []
        for g in anchor_hits:
            ev_rows.append({
                "成分": r["英文名"], "成分中文名": r["中文名"], "排名": r["排名"],
                "靶点": g, "证据层": "临床锚定",
                "证据来源": f"临床药直接作用靶点（{int(unified.loc[g, 'Anchor_n_drugs'])} 种药物）" if g in unified.index else "",
                "作用": unified.loc[g, "Anchor_actions"] if g in unified.index else ""})
        mech_hits = [g for g in str(r["命中文献靶点"]).split("；") if g] if pd.notna(r["命中文献靶点"]) else []
        for g in mech_hits:
            ev_rows.append({
                "成分": r["英文名"], "成分中文名": r["中文名"], "排名": r["排名"],
                "靶点": g, "证据层": f"文献机制（{dname}有效中药文献库）",
                "证据来源": "标准化机制数据库#机制实体长表", "作用": ""})
    ev_df = pd.DataFrame(ev_rows)
    p2 = ctx.artifact_path("Top100循证溯源明细.xlsx")
    ev_df.to_excel(p2, index=False)
    artifacts.append(p2)
    ctx.report_progress(0.75, "循证明细已导出", {"已导出": 2, "总产物": 3})

    # 3. CSV 快速版
    p3 = ctx.artifact_path("scored_compounds.csv")
    scored.to_csv(p3, index=False, encoding="utf-8-sig")
    artifacts.append(p3)
    ctx.report_progress(1.0, f"全部 {len(artifacts)} 个产物完成",
                        {"已导出": 3, "总产物": 3})
    ctx.log(f"产物导出完成：{'、'.join(os.path.basename(a) for a in artifacts)}", "success")

    ctx.evidence.add(
        claim=f"生成产物 {len(artifacts)} 个：Top200评分表 + Top100循证溯源明细 + CSV",
        sources=[EvidenceSource(kind="computed", ref=run_dir,
                                detail=f"疾病：{dname}；" +
                                       "; ".join(os.path.basename(a) for a in artifacts))],
        transform="评分表/靶点集/阳性药验证/溯源明细导出",
        step="s11_output_artifacts")
    return StepResult(ok=True, outputs={
        "产物文件数": len(artifacts),
        "文件": "、".join(os.path.basename(a) for a in artifacts)},
        artifacts=artifacts, metrics={"artifacts": len(artifacts)})


# ============================================================
# 工作流组装
# ============================================================

def build_workflow(engine):
    """注册疾病锚定靶点法工作流步骤（保持依赖关系；疾病由 disease profile 决定）"""
    engine.register("s1_load_mechanism_db", s1_load_mechanism_db, [],
                    domain="数据加载", desc="加载疾病标准化机制数据库（文献记录）")
    engine.register("s2_load_clinical_drugs", s2_load_clinical_drugs, [],
                    domain="数据加载", desc="加载指南临床药集合（sheet 名由 disease profile 决定）")
    engine.register("s3_load_drugbank_targets", s3_load_drugbank_targets, [],
                    domain="数据加载", desc="加载 DrugBank 临床药物作用靶点表")
    engine.register("s4_clinical_anchor_targets", s4_clinical_anchor_targets,
                    ["s2_load_clinical_drugs", "s3_load_drugbank_targets"],
                    domain="靶点构建", desc="构建临床锚定靶点集（临床药×DrugBank靶点交集）")
    engine.register("s5_mechanism_targets", s5_mechanism_targets,
                    ["s1_load_mechanism_db"],
                    domain="靶点构建", desc="构建文献机制靶点集（HGNC+证据分级加权）")
    engine.register("s6_integrate_targets", s6_integrate_targets,
                    ["s4_clinical_anchor_targets", "s5_mechanism_targets"],
                    domain="靶点构建", desc="靶点集整合与分层（临床锚定/文献机制/扩展）")
    engine.register("s7_load_molecule_library", s7_load_molecule_library, [],
                    domain="数据加载", desc="加载分子库筛选版（OB/DL 阈值见 scoring 段）")
    engine.register("s8_ctd_matching", s8_ctd_matching,
                    ["s6_integrate_targets", "s7_load_molecule_library"],
                    domain="筛选评分", desc="CTD 本地库匹配（成分→靶点，三级键匹配）")
    engine.register("s9_scoring", s9_scoring, ["s8_ctd_matching"],
                    domain="筛选评分", desc="候选小分子多源加权评分（靶点覆盖×ADME）")
    engine.register("s10_positive_control", s10_positive_control,
                    ["s9_scoring", "s4_clinical_anchor_targets"],
                    domain="质量验证", desc="阳性药验证（清单由 disease profile 决定）")
    engine.register("s11_output_artifacts", s11_output_artifacts,
                    ["s9_scoring", "s10_positive_control"],
                    domain="输出", desc="生成评分表+循证溯源明细+CSV产物")
    return engine
