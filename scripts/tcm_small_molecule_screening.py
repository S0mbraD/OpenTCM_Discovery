# -*- coding: utf-8 -*-
"""
中药筛选小分子工作流模板（文献参考靶点法）
================================================
用途：以疾病有效中药文献库为参考靶点来源，结合 CTD 化学-基因交互数据，
     从候选小分子库中筛选与参考靶点集最匹配的小分子。

工作流（6步）：
  1. 文献库中药 → 候选小分子映射（药材覆盖）
  2. 成分 → CTD ChemicalID 匹配（CAS > 名称 > 规范化 > 同义词）
  3. 参考靶点集构建（CTD 人类化学-基因交互）
  4. 参考靶点集质量验证（阳性药物 + 关键治疗靶点冒烟测试）
  5. 候选小分子筛选评分（靶点覆盖率/命中数/核心靶点/交互强度加权）
  6. 排序输出 Excel

数据依赖：
  - CTD 全量下载（自动下载缓存，绕过 ALTCHA 验证）：
      https://ctdbase.org/reports/CTD_chem_gene_ixns.tsv.gz  (41 MB, 315万行)
      https://ctdbase.org/reports/CTD_chemicals.tsv.gz       (10 MB, 17.9万化学物)
      https://ctdbase.org/reports/CTD_diseases.tsv.gz        (1.7 MB, 疾病MeSH ID)
  - 文献库 Excel（含「药物名称」列，如「√文献明细」sheet）
  - 候选小分子库 Excel（含 英文名/CAS号/PubChem CID/来源药材 列）

实测验证（2026-09，UC 场景）：
  - 335 味文献中药 → 243 去重 → 178 味被成分库覆盖 (73%)
  - 1638 条成分 → 714 条匹配 CTD (43.6%) → 249 条有交互数据
  - 参考靶点集 13,780 基因；阳性药 mesalazine/sulfasalazine/budesonide/
    prednisolone 靶点重叠率 92-100%；TNF/IL12B/IL23A/JAK1/2/3 等 15 个关键靶点全命中

注意：
  - CTD 使用条款要求引用 CTD 并通知其数据使用（https://ctdbase.org/about/legal.jsp）
  - Windows 路径用 raw string 或正斜杠
"""
import os
import re
import gzip
import json
import time
import requests
from collections import defaultdict

import openpyxl

# ============================================================
# 配置
# ============================================================
CTD_BASE = 'https://ctdbase.org/reports/'
CTD_FILES = {
    'chem_ixns': 'CTD_chem_gene_ixns.tsv.gz',   # 化学-基因交互
    'chemicals': 'CTD_chemicals.tsv.gz',        # 化学物词表
    'diseases':  'CTD_diseases.tsv.gz',         # 疾病词表
}
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

# 本地缓存目录（按需修改；默认 .temp/ctd_data）
CTD_CACHE_DIR = os.path.join('.temp', 'ctd_data')

# 疾病 MeSH ID（示例：UC；其他疾病查 CTD_diseases.tsv，切勿凭记忆填写！）
DISEASE_MESH_ID = 'D003093'   # Colitis, Ulcerative

# 阳性药物验证集（药物名 -> CTD ChemicalID，需查词表确认）
POSITIVE_DRUGS = {
    'mesalazine': 'D019804',
    'sulfasalazine': 'D012460',
    'budesonide': 'D019819',
    'prednisolone': 'D011239',
}

# 筛选评分权重（第五步）
WEIGHTS = {
    'coverage': 0.40,    # 靶点覆盖率（分子CTD靶点∩参考集 / 分子全部CTD靶点）
    'hits': 0.30,        # 参考集命中数（log归一化）
    'core_targets': 0.20,# 高覆盖靶点命中（按来源中药数加权）
    'action': 0.10,      # 交互动作强度（含 inhibitor/agonist 等明确调控词）
}
# 明确调控动作加分词
ACTION_KEYWORDS = ['inhibitor', 'inhibits', 'agonist', 'antagonist',
                   'activator', 'increases', 'decreases', 'blocks']


# ============================================================
# 第零步：CTD 数据下载与缓存
# ============================================================
def download_ctd(filename, cache_dir=CTD_CACHE_DIR, force=False):
    """下载并解压 CTD 全量数据文件（绕过 ALTCHA 验证，静态文件直连）

    Returns:
        str: 解压后的本地 TSV 文件路径
    """
    os.makedirs(cache_dir, exist_ok=True)
    tsv_path = os.path.join(cache_dir, filename.replace('.gz', ''))
    if os.path.exists(tsv_path) and not force:
        return tsv_path

    gz_path = tsv_path + '.gz'
    print(f'[CTD] 下载 {filename} ...')
    r = requests.get(CTD_BASE + filename, headers=HEADERS, timeout=600)
    r.raise_for_status()
    with open(gz_path, 'wb') as f:
        f.write(r.content)
    print(f'[CTD] {filename}: {len(r.content)/1024/1024:.1f} MB')

    with open(tsv_path, 'w', encoding='utf-8') as out:
        out.write(gzip.decompress(r.content).decode('utf-8', errors='replace'))
    print(f'[CTD] 解压完成: {tsv_path}')
    return tsv_path


# ============================================================
# CTD 数据加载
# ============================================================
def load_human_chem_gene_ixns(path):
    """加载人类化学-基因交互

    Returns:
        dict: {ChemicalID(裸ID): {GeneSymbol: [(InteractionActions, PubMedIDs)]}}
        实测：~11,103 个人类化学物
    """
    chem_targets = defaultdict(dict)
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.rstrip('\n').split('\t')
            # 列序: ChemicalName ChemicalID CasRN GeneSymbol GeneID GeneForms
            #       Organism OrganismID Interaction InteractionActions PubMedIDs
            if len(parts) < 11 or parts[6] != 'Homo sapiens':
                continue
            chem_id, gene_sym = parts[1], parts[3]
            chem_targets[chem_id].setdefault(gene_sym, []).append((parts[9], parts[10]))
    print(f'[CTD] 人类化学-基因交互: {len(chem_targets):,} 化学物')
    return dict(chem_targets)


def load_chemical_vocab(path):
    """加载化学物词表（名称/CAS/同义词 → 裸 ChemicalID）

    注意：词表 ChemicalID 带 MESH: 前缀，交互表用裸 ID，必须去前缀！

    Returns:
        (name_map, synonym_map, cas_map)
    """
    name_map, syn_map, cas_map = {}, {}, {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 5:
                continue
            # 列序: ChemicalName ChemicalID CasRN PubChemCID ...
            #       MESHSynonyms(11) CTDCuratedSynonyms(12)
            cid = parts[1].replace('MESH:', '')
            name_map[parts[0].lower()] = cid
            if parts[2]:
                cas_map[parts[2]] = cid
            for col in (11, 12):
                if len(parts) > col and parts[col]:
                    for syn in parts[col].split('|'):
                        if syn:
                            syn_map.setdefault(syn.lower(), cid)
    print(f'[CTD] 化学物词表: 主名称 {len(name_map):,}, CAS {len(cas_map):,}, 同义词 {len(syn_map):,}')
    return name_map, syn_map, cas_map


def normalize_name(s):
    """TCMSP 来源成分名规范化：去手性前缀 / _qt 后缀 / 尾括号别名"""
    s = s.strip().lower()
    s = re.sub(r'^\(\d*[+-eprsz,]*\)-', '', s)   # (+)- / (1r,2s)- 等
    s = re.sub(r'^[+-]\s*', '', s)
    s = re.sub(r'[_ ]qt$', '', s)                 # TCMSP 的 _qt 修饰符
    s = re.sub(r',\s*\(.*\)$', '', s)             # ",(leontalbinine)" 尾注
    return s.strip()


def match_compound_to_ctd(en_name, cas, name_map, syn_map, cas_map):
    """成分 → CTD 裸 ChemicalID 综合匹配（优先级：CAS > 主名 > 规范化 > 同义词）

    Returns:
        str|None: 裸 ChemicalID（如 'C008298'），未匹配返回 None
    """
    cas = (cas or '').strip()
    if cas and cas in cas_map:
        return cas_map[cas]
    en_name = (en_name or '').strip()
    if not en_name:
        return None
    low = en_name.lower()
    if low in name_map:
        return name_map[low]
    norm = normalize_name(en_name)
    if norm in name_map:
        return name_map[norm]
    if norm in syn_map:
        return syn_map[norm]
    if low in syn_map:
        return syn_map[low]
    return None


# ============================================================
# 第一步：文献库中药 → 候选小分子映射
# ============================================================
def load_literature_herbs(lit_xlsx, sheet_name=None, herb_col='药物名称'):
    """读取文献库中有文献支持的药物名称集合

    Args:
        lit_xlsx: 文献库 Excel 路径
        sheet_name: sheet 名（如 '√文献明细'），None 则取第一个
        herb_col: 药物名称列名

    Returns:
        set: 药物名称集合
    """
    wb = openpyxl.load_workbook(lit_xlsx, read_only=True)
    ws = wb[sheet_name] if sheet_name else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() if h else '' for h in rows[0]]
    try:
        col_idx = header.index(herb_col)
    except ValueError:
        raise ValueError(f'文献库未找到列「{herb_col}」，实际表头: {header}')
    herbs = set()
    for r in rows[1:]:
        if r[col_idx] and str(r[col_idx]).strip():
            herbs.add(str(r[col_idx]).strip())
    print(f'[文献库] 药物名称: {len(herbs)} 味（去重）')
    return herbs


def load_candidate_compounds(comp_xlsx, sheet_name=None,
                             name_col='英文名', cas_col='CAS号',
                             cid_col='PubChem CID', source_col='来源药材'):
    """读取候选小分子库（自动识别附加列：中文名/SMILES/OB/DL，缺失则留空）

    Returns:
        list[dict]: 每条成分 {name, cas, cid, source, name_cn, smiles, ob, dl, row}
    """
    wb = openpyxl.load_workbook(comp_xlsx, read_only=True)
    ws = wb[sheet_name] if sheet_name else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() if h else '' for h in rows[0]]

    def find_col(*aliases):
        for a in aliases:
            if a in header:
                return header.index(a)
        return None

    col = {
        'name': find_col(name_col), 'cas': find_col(cas_col),
        'cid': find_col(cid_col), 'source': find_col(source_col),
        'name_cn': find_col('中文名', '中文名称', '成分中文名'),
        'smiles': find_col('SMILES', 'Canonical SMILES'),
        'ob': find_col('OB(%)', 'OB', 'OB值'),
        'dl': find_col('DL', 'DL值'),
    }
    compounds = []
    for r in rows[1:]:
        def get(k):
            i = col.get(k)
            v = r[i] if i is not None and i < len(r) else None
            return str(v).strip() if v is not None and str(v).strip() else ''
        compounds.append({
            'name': get('name'), 'cas': get('cas'),
            'cid': get('cid'), 'source': get('source'),
            'name_cn': get('name_cn'), 'smiles': get('smiles'),
            'ob': get('ob'), 'dl': get('dl'),
            'row': r, 'header': header,
        })
    print(f'[小分子库] 候选成分: {len(compounds)} 条')
    return compounds


def build_herb_to_compounds(compounds):
    """成分「来源药材」列（支持 、/,/;/｜ 多分隔符）→ 药材→成分列表 映射"""
    herb_map = defaultdict(list)
    for c in compounds:
        src = c['source']
        for sep in ('、', ',', '，', ';', '；', '|'):
            src = src.replace(sep, '|')
        for h in src.split('|'):
            h = h.strip()
            if h:
                herb_map[h].append(c['name'])
    return dict(herb_map)


# ============================================================
# 第三步：参考靶点集构建
# ============================================================
def build_reference_targets(covered_herbs, herb_to_comps, comp_to_ctd, chem_targets):
    """构建参考靶点集：覆盖药材成分的 CTD 人类靶点

    Args:
        covered_herbs: 文献库 ∩ 成分库来源药材
        herb_to_comps: 药材 → 成分名列表
        comp_to_ctd: 成分名 → 裸 ChemicalID
        chem_targets: ChemicalID → {GeneSymbol: [...]}

    Returns:
        dict: {GeneSymbol: set(来源药材)}，即参考靶点集（含覆盖度）
    """
    ref_targets = defaultdict(set)
    comp_used = set()
    for herb in covered_herbs:
        for comp in herb_to_comps.get(herb, []):
            ctd_id = comp_to_ctd.get(comp)
            if ctd_id and ctd_id in chem_targets:
                comp_used.add(comp)
                for gene in chem_targets[ctd_id]:
                    ref_targets[gene].add(herb)
    print(f'[参考靶点] 有交互数据成分: {len(comp_used)}, 参考靶点基因: {len(ref_targets):,}')
    return dict(ref_targets), comp_used


# ============================================================
# 第四步：参考靶点集质量验证（冒烟测试）
# ============================================================
def validate_reference_targets(ref_targets, chem_targets,
                               positive_drugs=POSITIVE_DRUGS,
                               key_targets=('TNF', 'IL12B', 'IL23A', 'JAK1',
                                            'JAK2', 'JAK3', 'IL6', 'IL10',
                                            'IL1B', 'IFNG', 'NFKB1', 'PTGS2',
                                            'PPARG', 'AHR', 'IL17A')):
    """阳性药物 + 关键治疗靶点双重验证

    Returns:
        dict: 验证报告（药物重叠率、靶点命中表）
    """
    ref_genes = set(ref_targets.keys())
    report = {'drugs': {}, 'key_targets': {}}
    print('\n[验证] 阳性药物 CTD 靶点 vs 参考集:')
    for name, cid in positive_drugs.items():
        tg = set(chem_targets.get(cid, {}).keys())
        if not tg:
            print(f'  {name} ({cid}): CTD 无人类交互数据（请核对 ChemicalID）')
            report['drugs'][name] = {'error': 'no_data'}
            continue
        overlap = tg & ref_genes
        rate = len(overlap) / len(tg)
        report['drugs'][name] = {
            'ctd_targets': len(tg), 'overlap': len(overlap), 'rate': round(rate, 3)}
        print(f'  {name}: {len(tg)} 靶点, 重叠 {len(overlap)} ({rate*100:.0f}%)')
    print('[验证] 关键治疗靶点命中:')
    for t in key_targets:
        hit = t in ref_genes
        n = len(ref_targets.get(t, ()))
        report['key_targets'][t] = {'in_ref': hit, 'n_herbs': n}
        print(f'  {t}: {"✅" if hit else "❌"} (来源中药 {n} 味)')
    return report


# ============================================================
# 第五步：候选小分子筛选评分
# ============================================================
import math

def score_compound(ctd_id, chem_targets, ref_targets, max_herb_coverage):
    """单个小分子综合评分（0-1）

    评分维度（权重见 WEIGHTS）:
      coverage: 分子CTD靶点∩参考集 / 分子全部CTD靶点
      hits: 命中数 log 归一化
      core_targets: 命中靶点的来源中药数加权（核心靶点如 TNF/IL6 权重高）
      action: InteractionActions 含明确调控词的比例
    """
    tg = chem_targets.get(ctd_id, {})
    if not tg:
        return None
    ref_genes = ref_targets
    hit_genes = [g for g in tg if g in ref_genes]
    if not hit_genes:
        return {'total': 0.0, 'coverage': 0.0, 'hits': 0.0,
                'core_targets': 0.0, 'action': 0.0,
                'n_ctd_targets': len(tg), 'n_hits': 0, 'hit_genes': []}
    coverage = len(hit_genes) / len(tg)
    hits_norm = min(1.0, math.log1p(len(hit_genes)) / math.log1p(50))
    core_score = sum(min(len(ref_genes[g]), max_herb_coverage) for g in hit_genes)
    core_norm = core_score / (len(hit_genes) * max_herb_coverage) if hit_genes else 0
    action_hit = 0
    action_total = 0
    for g in hit_genes:
        for actions, _ in tg[g]:
            action_total += 1
            low = actions.lower()
            if any(k in low for k in ACTION_KEYWORDS):
                action_hit += 1
    action_norm = action_hit / action_total if action_total else 0
    total = (WEIGHTS['coverage'] * coverage +
             WEIGHTS['hits'] * hits_norm +
             WEIGHTS['core_targets'] * core_norm +
             WEIGHTS['action'] * action_norm)
    return {'total': round(total, 4), 'coverage': round(coverage, 4),
            'hits': round(hits_norm, 4), 'core_targets': round(core_norm, 4),
            'action': round(action_norm, 4),
            'n_ctd_targets': len(tg), 'n_hits': len(hit_genes),
            'hit_genes': hit_genes}


# ============================================================
# 主工作流
# ============================================================
def run_screening(lit_xlsx, comp_xlsx, output_xlsx,
                  disease_mesh_id=DISEASE_MESH_ID,
                  ctd_cache_dir=CTD_CACHE_DIR, top_n_report=50):
    """中药筛选小分子全流程

    Args:
        lit_xlsx: 有效中药文献库 Excel
        comp_xlsx: 候选小分子库 Excel
        output_xlsx: 输出 Excel 路径
        disease_mesh_id: 疾病 MeSH ID（仅记录用）
        ctd_cache_dir: CTD 本地缓存目录

    Returns:
        dict: 运行摘要
    """
    t0 = time.time()
    print(f'=== 中药筛选小分子工作流（疾病 MeSH: {disease_mesh_id}）===\n')

    # --- 第零步：CTD 数据 ---
    ixns_path = download_ctd(CTD_FILES['chem_ixns'], ctd_cache_dir)
    chem_path = download_ctd(CTD_FILES['chemicals'], ctd_cache_dir)
    chem_targets = load_human_chem_gene_ixns(ixns_path)
    name_map, syn_map, cas_map = load_chemical_vocab(chem_path)

    # --- 第一步：文献库中药 → 小分子映射 ---
    lit_herbs = load_literature_herbs(lit_xlsx)
    compounds = load_candidate_compounds(comp_xlsx)
    herb_to_comps = build_herb_to_compounds(compounds)
    covered = lit_herbs & set(herb_to_comps.keys())
    print(f'[覆盖] 文献库中药被成分库覆盖: {len(covered)}/{len(lit_herbs)}')

    # --- 第二步：成分 → CTD ID ---
    comp_to_ctd = {}
    match_stats = {'cas': 0, 'name': 0, 'norm': 0, 'syn': 0, 'miss': 0}
    for c in compounds:
        ctd_id = match_compound_to_ctd(c['name'], c['cas'], name_map, syn_map, cas_map)
        if ctd_id:
            comp_to_ctd[c['name']] = ctd_id
            # 统计匹配方式（简化：不重复区分）
            if c['cas'] and c['cas'] in cas_map:
                match_stats['cas'] += 1
            elif c['name'].lower() in name_map:
                match_stats['name'] += 1
            elif normalize_name(c['name']) in name_map:
                match_stats['norm'] += 1
            else:
                match_stats['syn'] += 1
        else:
            match_stats['miss'] += 1
    print(f'[匹配] 成分→CTD: {len(comp_to_ctd)}/{len(compounds)} '
          f'({len(comp_to_ctd)/len(compounds)*100:.1f}%), 方式分布: {match_stats}')

    # --- 第三步：参考靶点集 ---
    ref_targets, comp_used = build_reference_targets(
        covered, herb_to_comps, comp_to_ctd, chem_targets)
    if not ref_targets:
        raise RuntimeError('参考靶点集为空！检查：1) ID 前缀是否去除；2) 药材覆盖是否为空')
    max_herb_cov = max(len(v) for v in ref_targets.values())

    # --- 第四步：质量验证 ---
    report = validate_reference_targets(ref_targets, chem_targets)

    # --- 第五步：全库评分 ---
    print('\n[评分] 对候选小分子库评分...')
    scored = []
    for c in compounds:
        ctd_id = comp_to_ctd.get(c['name'])
        if not ctd_id:
            continue
        s = score_compound(ctd_id, chem_targets, ref_targets, max_herb_cov)
        if s and s['n_hits'] > 0:
            # 附加来源中药（该成分覆盖的文献库药材）
            src_herbs = set()
            for h, comps in herb_to_comps.items():
                if c['name'] in comps and h in covered:
                    src_herbs.add(h)
            scored.append({**c, 'ctd_id': ctd_id, 'score': s,
                           'lit_herbs': sorted(src_herbs)})
    scored.sort(key=lambda x: -x['score']['total'])
    print(f'[评分] 有参考靶点命中的分子: {len(scored)}')

    # --- 第六步：输出 Excel ---
    _write_output(scored, output_xlsx, ref_targets, report,
                  lit_herbs, covered, match_stats, disease_mesh_id, top_n_report)

    elapsed = time.time() - t0
    print(f'\n=== 完成: {len(scored)} 个候选分子, 输出 {output_xlsx}, 耗时 {elapsed:.0f}s ===')
    return {'n_scored': len(scored), 'output': output_xlsx,
            'ref_targets': len(ref_targets), 'covered_herbs': len(covered)}


def _write_output(scored, output_xlsx, ref_targets, report,
                  lit_herbs, covered, match_stats, disease_mesh_id, top_n):
    """写结果 Excel：Top汇总表 + 参考靶点表 + 验证报告表"""
    wb = openpyxl.Workbook()

    # Sheet1: 候选分子排序
    ws1 = wb.active
    ws1.title = '候选小分子排序'
    ws1.append(['排名', '综合评分', '靶点覆盖率(0.4)', '命中数分(0.3)',
                '核心靶点分(0.2)', '交互强度分(0.1)',
                'CTD靶点总数', '参考集命中数', '命中靶点(前20)',
                '核心命中靶点(来源中药≥50味, 前10)', '来源文献中药',
                '中文名', '英文名', 'CAS', 'PubChem CID', 'SMILES',
                'OB(%)', 'DL', 'CTD ChemicalID'])
    for rank, item in enumerate(scored[:2000], 1):
        s = item['score']
        core_hits = [g for g in s['hit_genes']
                     if len(ref_targets.get(g, ())) >= 50][:10]
        ws1.append([
            rank, s['total'], s['coverage'], s['hits'], s['core_targets'],
            s['action'], s['n_ctd_targets'], s['n_hits'],
            ', '.join(s['hit_genes'][:20]), ', '.join(core_hits),
            ', '.join(item['lit_herbs']),
            item.get('name_cn', ''), item['name'], item['cas'], item['cid'],
            item.get('smiles', ''), item.get('ob', ''), item.get('dl', ''),
            item['ctd_id'],
        ])

    # Sheet2: 参考靶点集（按覆盖中药数降序）
    ws2 = wb.create_sheet('参考靶点集')
    ws2.append(['靶点基因', '来源中药数', '来源中药(前30)'])
    for gene, herbs in sorted(ref_targets.items(), key=lambda x: -len(x[1])):
        ws2.append([gene, len(herbs), ', '.join(sorted(herbs)[:30])])

    # Sheet3: 验证报告
    ws3 = wb.create_sheet('验证报告')
    ws3.append(['疾病 MeSH ID', disease_mesh_id])
    ws3.append(['文献库中药数', len(lit_herbs)])
    ws3.append(['被成分库覆盖', len(covered)])
    ws3.append(['匹配统计', json.dumps(match_stats, ensure_ascii=False)])
    ws3.append(['参考靶点数', len(ref_targets)])
    ws3.append([])
    ws3.append(['阳性药物验证', 'CTD靶点数', '重叠数', '重叠率'])
    for name, r in report['drugs'].items():
        if 'error' in r:
            ws3.append([name, r['error'], '', ''])
        else:
            ws3.append([name, r['ctd_targets'], r['overlap'], r['rate']])
    ws3.append([])
    ws3.append(['关键治疗靶点', '是否在参考集', '来源中药数'])
    for t, r in report['key_targets'].items():
        ws3.append([t, '是' if r['in_ref'] else '否', r['n_herbs']])

    wb.save(output_xlsx)


# ============================================================
# 入口
# ============================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="文献参考靶点法中药小分子筛选")
    parser.add_argument("--lit", required=True, help="文献库 Excel（含药材-成分-靶点关联）")
    parser.add_argument("--comp", required=True, help="成分分子库 Excel（含 OB/DL/结构）")
    parser.add_argument("--output", required=True, help="结果输出 Excel 路径")
    args = parser.parse_args()

    run_screening(args.lit, args.comp, args.output)
