# -*- coding: utf-8 -*-
"""
TCM 分子库构建工作流模板（Agent A / B / C 三段式）
====================================================
依据导师需求实现「分子库」标准流程（14 步）：

  Agent A - Identity Agent：这个分子是谁？
    名称解析 → 同义词识别 → CAS/PubChem CID 匹配 → 数据库交叉验证 → 确定 SMILES_Original
  Agent B - Structure QC Agent：这个结构可靠吗？
    RDKit 解析 → 化学合理性 → Canonical/Isomeric SMILES → 电荷/片段数 →
    盐检测（初筛+语义判定）→ 立体化学检查 → 互变异构检测
  Agent C - Library Manager Agent：这个化合物最终应该怎么进入库？
    多层去重（Name→CAS→CID→Canonical SMILES，来源不丢失）→ 母体化学实体原则 →
    QC Score（0.25I+0.25S+0.2C+0.15R+0.15E）→ 生成 Compound_ID → 输出 4 个文件

输出文件（英文表头，AF3 兼容）：
  01_Compound_Master.xlsx      13 字段主库（化合物层面唯一）
  02_Compound_Source_Map.xlsx  来源表（化合物×中药多对多）
  03_QC_Report.xlsx            质控表（SMILES_Parse/Stereo/Identity/Salt/Tautomer/
                                Duplicate/QC_Status/QC_Reason）
  04_AF3_Input.txt             AF3 输入（SMILES_Isomeric_RDKit）

判定标准（导师定义）：
  PASS   ：SMILES 可解析、名称与结构一致、结构来源可靠、无歧义
  REVIEW ：手性缺失、盐状态不确定、同分异构不明确、名称可能对应多个化合物
  REJECT ：SMILES 无法解析、明显错误结构、非化学实体、重复且无保留价值

QC Score = 0.25*I + 0.25*S + 0.2*C + 0.15*R + 0.15*E
  I: Identity consistency  S: Structure validity  C: Chemical consistency
  R: Source reliability    E: Evidence consistency
  ≥90 → PASS；70–90 → REVIEW；<70 → REJECT

环境依赖：rdkit、requests、openpyxl
"""
import os
import re
import time
import math
from collections import defaultdict

import requests
import openpyxl

from rdkit import Chem
from rdkit import RDLogger
from rdkit.Chem import Descriptors, rdMolDescriptors

RDLogger.DisableLog('rdApp.*')

# ============================================================
# 配置
# ============================================================
PUBCHEM_BASE = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound'
HTTP_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
PUBCHEM_TIMEOUT = 30

# 盐/平衡离子规则（导师步骤 10）：常见反荷离子与常见有机酸盐/无机酸盐母体
SALT_COUNTERIONS = {
    # 金属阳离子
    '[Na+]', '[K+]', '[Li+]', '[Ca+2]', '[Mg+2]', '[Zn+2]', '[Fe+3]', '[Fe+2]',
    '[Al+3]', '[Cu+2]', '[Ba+2]', '[Mn+2]', '[Co+2]', '[Ni+2]',
    # 卤素/无机阴离子
    '[Cl-]', '[Br-]', '[I-]', '[F-]', '[O-]S(=O)(=O)[O-]',  # 硫酸根
    'Cl', 'Br', 'I',
    # 质子
    '[H+]',
}
# 有机母体常见酸根 SMILES（去质子化后形态）
ORGANIC_ACID_ANIONS = {
    'CC(=O)[O-]': 'acetate',
    'OC(=O)[O-]': 'carbonate',
    'OC(=O)CO': 'glycolate',
}
# 无机酸根（孤离子形态）
INORGANIC_ANIONS = {
    '[O-]C(=O)C': 'acetate_ion',
    'O=S(=O)([O-])[O-]': 'sulfate',
    'OP(=O)([O-])[O-]': 'phosphate',
    '[N+](=O)([O-])[O-]': 'nitrate',
    'Cl': 'hydrochloride_ion',
}

# QC Score 权重（导师定义）
QC_WEIGHTS = {'I': 0.25, 'S': 0.25, 'C': 0.20, 'R': 0.15, 'E': 0.15}
QC_PASS, QC_REVIEW = 90, 70


# ============================================================
# Agent A - Identity Agent
# ============================================================
def _pubchem_get(url, retries=2, timeout=PUBCHEM_TIMEOUT):
    """PubChem GET 请求（带重试；503 限流时快速放弃）"""
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None  # 未找到是正常业务结果
            if r.status_code in (502, 503, 504):
                if r.status_code == 503:
                    return None  # 限流：直接放弃，避免重试风暴
                time.sleep(2 * (i + 1))
                continue
            return None
        except requests.RequestException:
            if i < retries:
                time.sleep(2 * (i + 1))
                continue
    return None


def _pubchem_post_batch_cids(cids, props=('CanonicalSMILES,IsomericSMILES,'
                                          'MolecularFormula,MolecularWeight')):
    """PubChem 批量属性查询（一次最多 100 CID，POST）

    Returns:
        dict: {cid(str): {property...}}
    """
    if not cids:
        return {}
    result = {}
    n_batches = (len(cids) + 99) // 100
    n_503 = 0
    for bi, i in enumerate(range(0, len(cids), 100)):
        batch = cids[i:i+100]
        try:
            r = requests.post(
                f'{PUBCHEM_BASE}/property/{props}/JSON',
                json={'ids': [int(c) for c in batch]},
                headers=HTTP_HEADERS, timeout=PUBCHEM_TIMEOUT + 30)
            if r.status_code == 200:
                for prop in r.json().get('PropertyTable', {}).get('Properties', []):
                    result[str(prop.get('CID'))] = prop
            elif r.status_code == 503:
                n_503 += 1  # 限流：跳过该批，不退回逐条（防重试风暴）
            else:
                # 非限流失败时退回逐条 GET
                for c in batch:
                    data = _pubchem_get(f'{PUBCHEM_BASE}/cid/{c}/property/{props}/JSON')
                    if data and data.get('PropertyTable', {}).get('Properties'):
                        result[str(c)] = data['PropertyTable']['Properties'][0]
        except requests.RequestException:
            continue
        if (bi + 1) % 5 == 0:
            print(f'  [PubChem批量] {bi+1}/{n_batches} 批, 已命中 {len(result)}')
    if n_503:
        print(f'  [PubChem批量] {n_503}/{n_batches} 批被限流(503)跳过，对应记录按离线基线评分')
    return result


def identity_agent_batch(records, name_query_limit=0, cas_verify_limit=0):
    """Agent A（批量版）：对一批 records 做 PubChem 交叉验证

    策略（速度优先，限速接口按需开启）：
      1. 无 CID 且开启 name 查询 → 逐条 name→CID（限速，受 name_query_limit 控制）
      2. 汇总全部 CID（库内 + name 查到）→ 批量属性查询（100/批，快，全量做）
      3. CAS 验证（synonyms 接口不支持批量且逐条慢）→ 受 cas_verify_limit 控制
      4. 逐记录判定（_identity_finalize）

    Args:
        records: 输入记录列表
        name_query_limit: 无 CID 记录的 name→CID 查询上限（0=不查，None=全部）
        cas_verify_limit: CAS→synonyms 逐条验证上限（0=不查，None=全部）
    """
    # --- 1. 无 CID 记录：name→CID（限速逐条，可选） ---
    n_name = 0
    for rec in records:
        if rec.get('PubChem_CID'):
            continue
        if name_query_limit is not None and n_name >= name_query_limit:
            break
        name_en = (rec.get('Name_EN') or '').strip()
        if not name_en:
            continue
        n_name += 1
        data = _pubchem_get(f'{PUBCHEM_BASE}/name/{requests.utils.quote(name_en)}/cids/JSON')
        if data and data.get('IdentifierList', {}).get('CID'):
            rec['_name_cids'] = data['IdentifierList']['CID']
        time.sleep(0.35)  # name 接口限速 5/s
    if n_name:
        print(f'  [Agent A] name→CID 查询 {n_name} 条')

    # --- 2. 汇总全部 CID（库内 + name 查到）→ 批量属性查询 ---
    all_cids = set()
    for rec in records:
        cid = str(rec.get('PubChem_CID') or '').strip()
        if cid:
            all_cids.add(cid)
        elif rec.get('_name_cids'):
            all_cids.add(str(rec['_name_cids'][0]))
    all_props = _pubchem_post_batch_cids(list(all_cids))
    print(f'  [Agent A] CID 批量属性: {len(all_cids)} 个 CID, 命中 {len(all_props)}')

    # --- 3. CAS 验证（可选，逐条限速） ---
    cas_pattern = re.compile(r'^\d{2,7}-\d{2}-\d$')
    n_cas = 0
    for rec in records:
        if cas_verify_limit is not None and n_cas >= cas_verify_limit:
            break
        cas_in = str(rec.get('CAS') or '').strip()
        pc_cid = str(rec.get('PubChem_CID') or '').strip()
        if not pc_cid and rec.get('_name_cids'):
            pc_cid = str(rec['_name_cids'][0])
        if not (cas_in and pc_cid and pc_cid in all_props):
            continue
        n_cas += 1
        syn = _pubchem_get(f'{PUBCHEM_BASE}/cid/{pc_cid}/synonyms/JSON')
        if syn and syn.get('InformationList', {}).get('Information'):
            pc_synonyms = syn['InformationList']['Information'][0].get('Synonym', [])
            pc_cas_list = [s for s in pc_synonyms if cas_pattern.match(str(s))]
            rec['_cas_matched'] = (cas_in in pc_cas_list)
        time.sleep(0.3)
    if n_cas:
        n_hit = sum(1 for r in records if r.get('_cas_matched'))
        print(f'  [Agent A] CAS synonyms 验证 {n_cas} 条, 命中 {n_hit}')

    # --- 4. 逐记录判定 ---
    for rec in records:
        _identity_finalize(rec, all_props)
    return records


def _strip_counterions_smiles(canon_smiles):
    """结构比较用：剥离反荷离子，仅保留唯一有机主体

    （无有机主体或多个有机主体则原样返回；用于盐形态 SMILES 的比较容错）
    """
    m = Chem.MolFromSmiles(canon_smiles)
    if m is None:
        return canon_smiles
    frags = Chem.GetMolFrags(m, asMols=True)
    if len(frags) <= 1:
        return canon_smiles
    organic = [f for f in frags if any(a.GetSymbol() == 'C' for a in f.GetAtoms())]
    if len(organic) == 1:
        return Chem.MolToSmiles(organic[0], canonical=True, isomericSmiles=False)
    return canon_smiles


def _identity_finalize(rec, all_props):
    """Agent A 判定阶段（拿到 PubChem 属性后的统一处理）

    Args:
        rec: 单条记录（含 PubChem_CID / _name_cids）
        all_props: {cid: properties} 批量查询结果
    """
    name_en = (rec.get('Name_EN') or '').strip()
    cid_in = str(rec.get('PubChem_CID') or '').strip()
    cas_in = str(rec.get('CAS') or '').strip()
    smiles_in = str(rec.get('SMILES_Original') or '').strip()

    cross = {
        'cid_matched': False, 'cas_matched': False, 'name_lookup': False,
        'smiles_consistent': None, 'formula_consistent': None,
        'mw_consistent': None, 'ambiguity': 0, 'note': '',
    }
    pc_cid = None
    pc_smiles = None
    pc_formula = None
    pc_mw = None

    # --- CID 来源：库内 CID 优先，否则 name 查询结果 ---
    if cid_in:
        pc_cid = cid_in
    elif rec.get('_name_cids'):
        cids = rec['_name_cids']
        cross['name_lookup'] = True
        if len(cids) == 1:
            pc_cid = str(cids[0])
        else:
            cross['ambiguity'] = len(cids)
            cross['note'] = f'name→{len(cids)} CIDs'
            pc_cid = str(cids[0])

    # --- PubChem 属性（来自批量结果） ---
    if pc_cid and pc_cid in all_props:
        prop = all_props[pc_cid]
        pc_smiles = prop.get('CanonicalSMILES') or prop.get('IsomericSMILES')
        pc_formula = prop.get('MolecularFormula')
        pc_mw = float(prop.get('MolecularWeight', 0)) if prop.get('MolecularWeight') else None
        cross['cid_matched'] = True

    # --- CAS 交叉验证（batch 层已查好 synonyms，这里只读取结果） ---
    cross['cas_matched'] = bool(rec.get('_cas_matched'))

    # --- SMILES 一致性（库内 SMILES vs PubChem Canonical，结构级比较） ---
    # 盐形态容错：直接比较失败时，双方都剥离反荷离子后再比一次
    if smiles_in and pc_smiles:
        m1, m2 = Chem.MolFromSmiles(smiles_in), Chem.MolFromSmiles(pc_smiles)
        if m1 is not None and m2 is not None:
            c1 = Chem.MolToSmiles(m1, canonical=True, isomericSmiles=False)
            c2 = Chem.MolToSmiles(m2, canonical=True, isomericSmiles=False)
            if c1 == c2:
                cross['smiles_consistent'] = True
            else:
                cross['smiles_consistent'] = (
                    _strip_counterions_smiles(c1) == _strip_counterions_smiles(c2))

    # --- 分子式 / 分子量一致性 ---
    lib_formula = (rec.get('Formula') or '').strip()
    lib_mw = rec.get('MW')
    if pc_formula and lib_formula:
        cross['formula_consistent'] = (
            re.sub(r'\s', '', lib_formula) == re.sub(r'\s', '', pc_formula))
    if pc_mw and lib_mw:
        try:
            cross['mw_consistent'] = abs(float(lib_mw) - pc_mw) <= max(1.0, pc_mw * 0.01)
        except (ValueError, TypeError):
            pass

    # --- 身份状态判定 ---
    evidence = sum([cross['cid_matched'], cross['cas_matched'],
                    bool(cross['smiles_consistent'])])
    if cross['ambiguity'] > 1:
        identity_status = 'ambiguous'
    elif evidence >= 2 and (cross['smiles_consistent'] or cross['cid_matched']):
        identity_status = 'confirmed'
    elif evidence == 1:
        identity_status = 'unconfirmed'
    else:
        identity_status = 'no_data'

    # I 得分（身份一致性）：基线考虑库内已有 CID+SMILES 本身就是可靠身份证据
    # （库内数据来自 HERB/TCMSP 交叉，非纯文本）
    i_score = 55.0  # 基线：有库内 CID 或 CAS 或 SMILES 任一即算有效身份
    if not (cid_in or cas_in or smiles_in):
        i_score = 10.0
    else:
        i_score += 15 * cross['cid_matched']
        i_score += 10 * cross['cas_matched']
        i_score += 15 * bool(cross['smiles_consistent'])
        i_score += 5 * bool(cross['formula_consistent'])
        if cross['ambiguity'] > 1:
            i_score *= 0.7

    record['identity_status'] = identity_status
    record['PubChem_CID_Verified'] = str(pc_cid) if pc_cid else ''
    record['cross_check'] = cross
    record['structure_score_I'] = round(i_score, 1)
    # Structure_Source：记录结构最终采纳的来源链
    sources = []
    if smiles_in:
        sources.append(record.get('DB_Source') or 'input_lib')
    if cross['cid_matched'] and pc_smiles:
        sources.append('PubChem')
    record['structure_source'] = ';'.join(dict.fromkeys(sources))
    record['PC_SMILES'] = pc_smiles or ''
    record['PC_Formula'] = pc_formula or ''
    record['PC_MW'] = pc_mw
    return record


def _identity_offline(rec):
    """Agent A 离线模式：不联网，库内 CID/CAS/SMILES 即身份证据基线

    （库内数据来自 HERB/TCMSP 交叉，非纯文本，本身即为可靠身份证据）
    """
    cc = {'cid_matched': bool(rec.get('PubChem_CID')),
          'cas_matched': False, 'name_lookup': False,
          'smiles_consistent': None, 'formula_consistent': None,
          'mw_consistent': None, 'ambiguity': 0, 'note': 'pubchem_disabled'}
    rec['identity_status'] = 'unconfirmed' if rec.get('PubChem_CID') else 'no_data'
    rec['cross_check'] = cc
    i = 55.0  # 基线：有库内 CID 或 CAS 或 SMILES 任一即算有效身份
    if not (rec.get('PubChem_CID') or rec.get('CAS') or rec.get('SMILES_Original')):
        i = 10.0
    else:
        i += 15 if rec.get('PubChem_CID') else 0
        i += 10 if rec.get('CAS') else 0
        i += 15 if rec.get('SMILES_Original') else 0
    rec['structure_score_I'] = round(i, 1)
    rec['structure_source'] = rec.get('DB_Source') or 'input_lib'
    rec['PubChem_CID_Verified'] = ''
    return rec


# ============================================================
# Agent B - Structure QC Agent
# ============================================================
def _is_counterion(smiles):
    """判断片段是否为反荷离子/小离子（Na+/K+/Cl-/硫酸根等）"""
    if smiles in SALT_COUNTERIONS:
        return True
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        return False
    n_atoms = m.GetNumAtoms()
    if n_atoms == 1:
        # 单原子离子（金属/卤素/质子）
        return True
    if smiles in INORGANIC_ANIONS or smiles in ORGANIC_ACID_ANIONS:
        return True
    # 双原子以下无机（HCl、HF 等）
    if n_atoms <= 2 and not any(a.GetSymbol() == 'C' for a in m.GetAtoms()):
        return True
    return False


def _is_small_organic_acid(smiles):
    """常见小分子有机酸（乙酸/苯甲酸/酒石酸/柠檬酸等成盐配体）"""
    if smiles in ORGANIC_ACID_ANIONS:
        return True
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        return False
    n_c = sum(1 for a in m.GetAtoms() if a.GetSymbol() == 'C')
    n_o = sum(1 for a in m.GetAtoms() if a.GetSymbol() == 'O')
    return n_c <= 8 and n_o >= 2 and n_c <= n_o  # 小有机酸经验阈值


def structure_qc_agent(record):
    """Agent B：结构质控（RDKit 全套）

    更新 record：
      SMILES_Parse, SMILES_Canonical_RDKit, SMILES_Isomeric_RDKit,
      Formal_Charge, Num_Fragments, Salt_Status,
      Stereo_Status（含未定义手性中心计数）, Tautomer_Status,
      chem_reasonable, structure_score_S, chem_score_C
    """
    smiles = record.get('SMILES_Original') or ''
    record['SMILES_Parse'] = ''
    record['SMILES_Canonical_RDKit'] = ''
    record['SMILES_Isomeric_RDKit'] = ''
    record['Formal_Charge'] = None
    record['Num_Fragments'] = None
    record['Salt_Status'] = 'none'
    record['Stereo_Status'] = 'no_stereo'
    record['Num_Unspecified_Stereocenters'] = 0
    record['Tautomer_Status'] = 'none'
    record['chem_reasonable'] = False
    record['structure_score_S'] = 0
    record['chem_score_C'] = 0
    record['parent_smiles'] = smiles  # 盐剥离后的母体（Agent C 用）

    # --- Step1：SMILES 解析 ---
    mol = Chem.MolFromSmiles(smiles) if smiles else None
    if mol is None or mol.GetNumAtoms() == 0:
        record['SMILES_Parse'] = 'fail'
        return record
    record['SMILES_Parse'] = 'ok'

    # --- Step3：基本结构完整性（原子/键/环计数异常检测） ---
    n_atoms = mol.GetNumAtoms()
    n_bonds = mol.GetNumBonds()
    n_rings = rdMolDescriptors.CalcNumRings(mol)
    structural_anomaly = (n_atoms < 3 or n_bonds < 2)

    # --- Step6/7：Canonical & Isomeric SMILES ---
    record['SMILES_Canonical_RDKit'] = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=False)
    record['SMILES_Isomeric_RDKit'] = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)

    # --- Step8：Formal Charge ---
    record['Formal_Charge'] = Chem.GetFormalCharge(mol)

    # --- Step9：Num_Fragments ---
    frags = Chem.GetMolFrags(mol, asMols=False)
    record['Num_Fragments'] = len(frags)

    # --- Step10：Salt Detection（初筛 + 语义判定） ---
    if len(frags) > 1:
        frag_smiles = [_frag_to_smiles(mol, fi) for fi in frags]
        counter_ions = [s for s in frag_smiles if _is_counterion(s)]
        organic_acids = [s for s in frag_smiles if _is_small_organic_acid(s)]
        organic_main = [s for s in frag_smiles
                        if s not in counter_ions and s not in organic_acids]
        if counter_ions and len(organic_main) == 1:
            record['Salt_Status'] = 'salt'
            # 盐剥离：保留母体（导师：盐删除，保留母体）
            parent = organic_main[0]
            pm = Chem.MolFromSmiles(parent)
            if pm is not None:
                record['parent_smiles'] = parent
                record['SMILES_Original'] = parent  # 更新为母体
                # 重新生成三件套
                record['SMILES_Canonical_RDKit'] = Chem.MolToSmiles(pm, canonical=True, isomericSmiles=False)
                record['SMILES_Isomeric_RDKit'] = Chem.MolToSmiles(pm, canonical=True, isomericSmiles=True)
                record['Formal_Charge'] = Chem.GetFormalCharge(pm)
                record['Num_Fragments'] = 1
        elif organic_acids and len(organic_main) == 1:
            record['Salt_Status'] = 'possible_salt'
        elif len(organic_main) > 1:
            record['Salt_Status'] = 'mixture'
        else:
            record['Salt_Status'] = 'unknown'

    # --- 母体分子对象（盐剥离后） ---
    mol_p = Chem.MolFromSmiles(record['parent_smiles'])
    if mol_p is None:
        record['chem_reasonable'] = False
        return record

    # --- Step2：化学合理性（原子价态/芳香性/键类型/电荷） ---
    try:
        Chem.SanitizeMol(mol_p)
        sanitize_ok = True
    except Exception:
        sanitize_ok = False
    valence_ok = all(a.GetImplicitValence() + a.GetExplicitValence()
                     <= Chem.GetPeriodicTable().GetDefaultValence(a.GetAtomicNum())
                     or a.GetFormalCharge() != 0 for a in mol_p.GetAtoms())
    weird_bonds = any(b.GetBondType() not in
                      (Chem.BondType.SINGLE, Chem.BondType.DOUBLE, Chem.BondType.TRIPLE,
                       Chem.BondType.AROMATIC) for b in mol_p.GetBonds())
    record['chem_reasonable'] = sanitize_ok and valence_ok and not weird_bonds and not structural_anomaly

    # --- Step7 补充：立体化学检查（未定义手性中心标记） ---
    try:
        from rdkit.Chem import FindMolChiralCenters
        centers = FindMolChiralCenters(mol_p, includeUnassigned=True, useLegacyImplementation=False)
        unassigned = [c for c in centers if c[1] == '?']
        n_stereo = len(centers)
    except Exception:
        unassigned, n_stereo = [], 0
    record['Num_Unspecified_Stereocenters'] = len(unassigned)
    if n_stereo == 0:
        record['Stereo_Status'] = 'no_stereo'
    elif unassigned:
        record['Stereo_Status'] = 'unspecified_stereo'
    else:
        record['Stereo_Status'] = 'fully_specified'

    # --- Step11：互变异构检测（大分子跳过 + 枚举上限，防超时） ---
    try:
        if mol_p.GetNumAtoms() > 80:
            record['Tautomer_Status'] = 'skipped_large'
        else:
            from rdkit.Chem.MolStandardize import rdMolStandardize
            params = rdMolStandardize.CleanupParameters()
            params.maxTautomers = 100
            enum = rdMolStandardize.TautomerEnumerator(params)
            tauts = enum.Enumerate(mol_p)
            record['Tautomer_Status'] = 'multiple_tautomers' if len(tauts) > 1 else 'single'
    except Exception:
        record['Tautomer_Status'] = 'unknown'

    # --- S 得分（结构有效性） ---
    s_score = 100.0
    if not record['chem_reasonable']:
        s_score -= 60
    if record['SMILES_Parse'] != 'ok':
        s_score = 0
    if record['Stereo_Status'] == 'unspecified_stereo':
        s_score -= 15
    if record['Salt_Status'] in ('salt',):
        pass  # 已剥离母体，不扣分
    if record['Salt_Status'] in ('mixture', 'unknown'):
        s_score -= 25
    record['structure_score_S'] = max(0, round(s_score, 1))

    # --- C 得分（化学一致性：分子式/分子量 vs 库内值） ---
    c_score = 100.0
    try:
        rd_formula = rdMolDescriptors.CalcMolFormula(mol_p)
        rd_mw = Descriptors.MolWt(mol_p)
        lib_f = re.sub(r'\s', '', record.get('Formula') or '')
        record['RDKit_Formula'] = rd_formula
        record['RDKit_MW'] = round(rd_mw, 2)
        if lib_f:
            if lib_f != rd_formula:
                c_score -= 45
                record.setdefault('qc_notes', []).append(f'formula mismatch: lib={lib_f} rdkit={rd_formula}')
        if record.get('MW'):
            try:
                if abs(float(record['MW']) - rd_mw) > max(1.0, rd_mw * 0.01):
                    c_score -= 35
                    record.setdefault('qc_notes', []).append(
                        f'MW mismatch: lib={record["MW"]} rdkit={rd_mw:.2f}')
            except (ValueError, TypeError):
                pass
        # PubChem 一致性（Agent A 结果）
        cc = record.get('cross_check') or {}
        if cc.get('formula_consistent') is False:
            c_score -= 10
        if cc.get('mw_consistent') is False:
            c_score -= 10
    except Exception:
        pass
    record['chem_score_C'] = max(0, round(c_score, 1))
    return record


def _frag_to_smiles(mol, frag_atom_indices):
    """片段原子索引集合 → 片段 SMILES"""
    try:
        from rdkit import Chem as _Chem
        em = _Chem.RWMol()
        idx_map = {}
        for i in frag_atom_indices:
            idx_map[i] = em.AddAtom(mol.GetAtomWithIdx(i))
        for b in mol.GetBonds():
            a1, a2 = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
            if a1 in idx_map and a2 in idx_map:
                em.AddBond(idx_map[a1], idx_map[a2], b.GetBondType())
        m = em.GetMol()
        _Chem.SanitizeMol(m)
        return _Chem.MolToSmiles(m, canonical=True)
    except Exception:
        return ''


# ============================================================
# Agent C - Library Manager Agent
# ============================================================
def normalize_name_for_dedup(name):
    """名称规范化（去重层1）：小写、去手性前缀/后缀修饰、去空格标点"""
    if not name:
        return ''
    s = str(name).strip().lower()
    s = re.sub(r'^\(\d*[+-eprsz,]*\)-', '', s)
    s = re.sub(r'^[+-]\s*', '', s)
    s = re.sub(r'[_ ]qt$', '', s)
    s = re.sub(r',\s*\(.*\)$', '', s)
    s = re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', s)  # 只留字母数字与中文
    return s


def source_score(record):
    """R 得分（来源可靠性）：库来自 HERB/TCMSP/文献本身就是可靠来源
    PubChem 验证/多源交叉为加分项"""
    r = 75.0  # 基线：库内数据源可靠（HERB/TCMSP/PubChem 交叉库）
    cc = record.get('cross_check') or {}
    if cc.get('cid_matched'):
        r += 15
    if cc.get('cas_matched'):
        r += 10
    return min(100, r)


def evidence_score(record):
    """E 得分（证据一致性）：分子式/分子量自洽是基线证据
    PubChem 结构一致为强加分"""
    e = 75.0  # 基线：有可验证的分子式/分子量/结构即有基本证据链
    cc = record.get('cross_check') or {}
    if not (record.get('Formula') or record.get('MW') or record.get('SMILES_Original')):
        e = 20.0
    else:
        if cc.get('smiles_consistent'):
            e += 15
        if cc.get('formula_consistent'):
            e += 5
        if cc.get('mw_consistent'):
            e += 5
    if record.get('identity_status') == 'ambiguous':
        e -= 25
    if cc.get('ambiguity', 0) > 1:
        e -= 10
    return max(0, min(100, e))


def _review_reasons(rec):
    """REVIEW 原因列表（导师判定标准映射）"""
    reasons = []
    if rec.get('Stereo_Status') == 'unspecified_stereo':
        reasons.append(f'手性未定义({rec.get("Num_Unspecified_Stereocenters")}个)')
    if rec.get('Salt_Status') in ('possible_salt', 'mixture', 'unknown'):
        reasons.append(f'盐状态不确定({rec.get("Salt_Status")})')
    if rec.get('Tautomer_Status') == 'multiple_tautomers':
        reasons.append('存在互变异构')
    if rec.get('identity_status') == 'ambiguous':
        reasons.append('名称对应多个化合物')
    if rec.get('identity_status') in ('unconfirmed', 'no_data'):
        reasons.append('身份证据不足')
    cc = rec.get('cross_check') or {}
    if cc.get('formula_consistent') is False or cc.get('mw_consistent') is False:
        reasons.append('分子式/分子量不一致')
    return reasons


def library_manager_agent(records, cid_prefix='C', start_id=1):
    """Agent C：多层去重 + QC 决策 + Compound_ID 生成

    去重键优先级（导师步骤12）：Name_EN 规范化 > CAS > PubChem CID > Canonical SMILES
    合并策略：命中已有键 → 合并来源（Structure_Source 并集），不丢弃任何来源行

    Returns:
        (compounds, source_map_rows, qc_rows)
    """
    # --- 索引：4 层去重键 ---
    key_by = {'name': {}, 'cas': {}, 'cid': {}, 'smiles': {}}
    compounds = []          # Compound Master 行
    source_map_rows = []    # Compound Source Map 行
    qc_rows = []            # QC Report 行
    next_id = start_id

    def _find_existing(rec):
        """返回已存在的 Compound 索引（4 层键任一命中）"""
        nkey = normalize_name_for_dedup(rec.get('Name_EN'))
        ckey = (rec.get('CAS') or '').strip()
        dkey = (rec.get('PubChem_CID_Verified') or rec.get('PubChem_CID') or '').strip()
        skey = rec.get('SMILES_Canonical_RDKit') or ''
        for key, val in (('name', nkey), ('cas', ckey), ('cid', dkey), ('smiles', skey)):
            if val and val in key_by[key]:
                return key_by[key][val], key
        return None, None

    for rec in records:
        # --- QC Score（导师公式） ---
        qc_score = (QC_WEIGHTS['I'] * (rec.get('structure_score_I') or 0) +
                    QC_WEIGHTS['S'] * (rec.get('structure_score_S') or 0) +
                    QC_WEIGHTS['C'] * (rec.get('chem_score_C') or 0) +
                    QC_WEIGHTS['R'] * source_score(rec) +
                    QC_WEIGHTS['E'] * evidence_score(rec))
        rec['QC_Score'] = round(qc_score, 1)

        # 硬性 REVIEW 特征（导师判定标准）：手性缺失/盐状态不确定/混合物/名称歧义
        # 分数再高，命中这些特征也必须人工复核（天然产物手性未定义属常态，非缺陷）
        hard_review = (rec.get('Stereo_Status') == 'unspecified_stereo'
                       or rec.get('Salt_Status') in ('possible_salt', 'mixture', 'unknown')
                       or rec.get('identity_status') == 'ambiguous')

        if rec.get('SMILES_Parse') != 'ok':
            rec['QC_Status'] = 'REJECT'
            rec['QC_Reason'] = 'SMILES不可解析'
        elif hard_review and qc_score >= QC_REVIEW:
            rec['QC_Status'] = 'REVIEW'
            rec['QC_Reason'] = '; '.join(_review_reasons(rec)) or '存在需人工复核特征'
        elif qc_score >= QC_PASS:
            rec['QC_Status'] = 'PASS'
            rec['QC_Reason'] = ''
        elif qc_score >= QC_REVIEW:
            rec['QC_Status'] = 'REVIEW'
            rec['QC_Reason'] = '; '.join(_review_reasons(rec)) or '综合评分偏低'
        else:
            rec['QC_Status'] = 'REJECT'
            reasons = []
            if not rec.get('chem_reasonable'):
                reasons.append('化学结构异常')
            cc = rec.get('cross_check') or {}
            if cc.get('formula_consistent') is False:
                reasons.append('分子式不一致')
            if cc.get('smiles_consistent') is False:
                reasons.append('结构与PubChem不一致')
            rec['QC_Reason'] = '; '.join(reasons) or '综合评分过低'

        # --- 去重与合并 ---
        existing_idx, matched_key = _find_existing(rec)
        if existing_idx is not None:
            comp = compounds[existing_idx]
            # 母体化学实体原则：同一实体只保留一个 Compound_ID
            # 结构来源合并（不丢失）
            if rec.get('structure_source'):
                existing_sources = set(comp['Structure_Source'].split(';')) if comp['Structure_Source'] else set()
                existing_sources.update(rec['structure_source'].split(';'))
                comp['Structure_Source'] = ';'.join(sorted(existing_sources))
            # QC 保留较优
            if rec['QC_Score'] > comp.get('_qc_score', 0):
                comp['_qc_score'] = rec['QC_Score']
                comp['QC_Status'] = rec['QC_Status']
                comp['QC_Reason'] = rec['QC_Reason']
            # 来源映射行仍然逐条记录
            rec['Compound_ID'] = comp['Compound_ID']
            rec['Duplicate_Status'] = f'merged({matched_key})'
        else:
            comp_id = f'{cid_prefix}{next_id:04d}'
            next_id += 1
            rec['Compound_ID'] = comp_id
            rec['Duplicate_Status'] = 'unique'
            compounds.append({
                'Compound_ID': comp_id,
                'Name_CN': rec.get('Name_CN', ''),
                'Name_EN': rec.get('Name_EN', ''),
                'CAS': rec.get('CAS', ''),
                'Formula': rec.get('RDKit_Formula') or rec.get('Formula', ''),
                'SMILES_Original': rec.get('SMILES_Original', ''),
                'SMILES_Canonical_RDKit': rec.get('SMILES_Canonical_RDKit', ''),
                'SMILES_Isomeric_RDKit': rec.get('SMILES_Isomeric_RDKit', ''),
                'Formal_Charge': rec.get('Formal_Charge'),
                'Num_Fragments': rec.get('Num_Fragments'),
                'Salt_Status': rec.get('Salt_Status', 'none'),
                'Tautomer_Status': rec.get('Tautomer_Status', 'none'),
                'Structure_Source': rec.get('structure_source', ''),
                '_qc_score': rec['QC_Score'],
                'QC_Status': rec['QC_Status'],
                'QC_Reason': rec['QC_Reason'],
                '_rec': rec,  # 暂存原始记录
            })
            # 建立去重索引
            nkey = normalize_name_for_dedup(rec.get('Name_EN'))
            ckey = (rec.get('CAS') or '').strip()
            dkey = (rec.get('PubChem_CID_Verified') or rec.get('PubChem_CID') or '').strip()
            skey = rec.get('SMILES_Canonical_RDKit') or ''
            if nkey:
                key_by['name'][nkey] = len(compounds) - 1
            if ckey:
                key_by['cas'][ckey] = len(compounds) - 1
            if dkey:
                key_by['cid'][dkey] = len(compounds) - 1
            if skey:
                key_by['smiles'][skey] = len(compounds) - 1

        # --- Source Map 行（每条输入记录都保留：槲皮素→Herb A/B/C） ---
        source_map_rows.append({
            'Compound_ID': rec['Compound_ID'],
            'Name_EN': rec.get('Name_EN', ''),
            'Herb_Name': rec.get('Herb_Name', ''),
            'Herb_ID': rec.get('Herb_ID', ''),
            'Species': rec.get('Species', ''),
            'Database': rec.get('DB_Source', ''),
            'Source_ID': rec.get('Source_ID', ''),
            'Literature': rec.get('Literature', ''),
            'Duplicate_Status': rec.get('Duplicate_Status', ''),
            'QC_Status': rec['QC_Status'],
        })
        # --- QC Report 行 ---
        qc_rows.append({
            'Compound_ID': rec['Compound_ID'],
            'Name_EN': rec.get('Name_EN', ''),
            'SMILES_Parse': rec.get('SMILES_Parse', ''),
            'Stereo_Status': rec.get('Stereo_Status', ''),
            'Identity_Status': rec.get('identity_status', ''),
            'Salt_Status': rec.get('Salt_Status', ''),
            'Tautomer_Status': rec.get('Tautomer_Status', ''),
            'Duplicate_Status': rec.get('Duplicate_Status', ''),
            'QC_Score': rec.get('QC_Score'),
            'QC_Status': rec.get('QC_Status', ''),
            'QC_Reason': rec.get('QC_Reason', ''),
        })
    return compounds, source_map_rows, qc_rows


# ============================================================
# 输入读取
# ============================================================
def load_input_records(xlsx_path, sheet_name=None):
    """读取原始分子库输入（input Acquisition）

    识别列（多别名自动匹配）：
      Name_CN(中文名), Name_EN(英文名), CAS( CAS号), PubChem_CID,
      Formula(分子式), MW(分子量), SMILES_Original(Canonical SMILES),
      Herb_Name(来源药材), Species(来源药材基原), Herb_ID(HERB ID),
      Source_ID(TCMSP Mol ID), DB_Source, Literature
    """
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb[sheet_name] if sheet_name else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() if h else '' for h in rows[0]]

    alias = {
        'Name_CN': ['中文名', 'name_cn'],
        'Name_EN': ['英文名', 'name_en', 'molecule_name', 'ingredient'],
        'CAS': ['cas号', 'cas'],
        'PubChem_CID': ['pubchem cid', 'pubchem_cid', 'cid'],
        'Formula': ['分子式', 'formula', 'molecular formula'],
        'MW': ['分子量', 'mw', 'molecular weight'],
        'SMILES_Original': ['canonical smiles', 'smiles', 'smiles_original'],
        'Herb_Name': ['来源药材', 'herb_name', 'herb'],
        'Species': ['来源药材基原(物种拉丁学名)', 'species', '基原'],
        'Herb_ID': ['herb id', 'herb_id'],
        'Source_ID': ['tcmsp mol id', 'tcmsp_mol_id', 'source_id'],
        'Literature': ['uc相关文献/支撑化合物', 'literature', '文献'],
    }
    col_map = {}
    for key, aliases in alias.items():
        for i, h in enumerate(header):
            if h.lower() in aliases:
                col_map[key] = i
                break

    # Excel 数值单元格清理：12345.0 → 12345（CID/CAS/ID 类字段专用）
    def _clean_id(v):
        s = str(v).strip() if v is not None else ''
        if s.endswith('.0') and s[:-2].isdigit():
            s = s[:-2]
        return s

    records = []
    for r in rows[1:]:
        def get(key, cleaner=None):
            i = col_map.get(key)
            if i is None or i >= len(r) or r[i] is None:
                return ''
            v = str(r[i]).strip()
            return cleaner(v) if cleaner else v
        # MW 数值化
        mw_s = get('MW')
        try:
            mw = float(mw_s) if mw_s else None
        except ValueError:
            mw = None
        records.append({
            'Name_CN': get('Name_CN'),
            'Name_EN': get('Name_EN'),
            'CAS': get('CAS', _clean_id),
            'PubChem_CID': get('PubChem_CID', _clean_id),
            'Formula': get('Formula'),
            'MW': mw,
            'SMILES_Original': get('SMILES_Original'),
            'Herb_Name': get('Herb_Name'),
            'Species': get('Species'),
            'Herb_ID': get('Herb_ID', _clean_id),
            'Source_ID': get('Source_ID', _clean_id),
            'Literature': get('Literature'),
            'DB_Source': 'input_lib',
        })
    return records


# ============================================================
# 输出
# ============================================================
def write_outputs(compounds, source_map_rows, qc_rows, out_dir, af3_only_pass=True):
    """输出 4 个文件：01_Master / 02_Source_Map / 03_QC_Report / 04_AF3_Input"""
    os.makedirs(out_dir, exist_ok=True)
    p1 = os.path.join(out_dir, '01_Compound_Master.xlsx')
    p2 = os.path.join(out_dir, '02_Compound_Source_Map.xlsx')
    p3 = os.path.join(out_dir, '03_QC_Report.xlsx')
    p4 = os.path.join(out_dir, '04_AF3_Input.txt')

    # 01 Master（13 字段，英文表头）
    wb1 = openpyxl.Workbook()
    ws1 = wb1.active
    ws1.title = 'Compound_Master'
    ws1.append(['Compound_ID', 'Name_CN', 'Name_EN', 'CAS',
                'Formula', 'SMILES_Original', 'SMILES_Canonical_RDKit',
                'SMILES_Isomeric_RDKit', 'Formal_Charge', 'Num_Fragments',
                'Salt_Status', 'Tautomer_Status', 'Structure_Source',
                'QC_Score', 'QC_Status', 'QC_Reason'])
    for c in compounds:
        ws1.append([c['Compound_ID'], c['Name_CN'], c['Name_EN'], c['CAS'],
                    c['Formula'], c['SMILES_Original'], c['SMILES_Canonical_RDKit'],
                    c['SMILES_Isomeric_RDKit'], c['Formal_Charge'], c['Num_Fragments'],
                    c['Salt_Status'], c['Tautomer_Status'], c['Structure_Source'],
                    c.get('_qc_score'), c['QC_Status'], c['QC_Reason']])
    wb1.save(p1)

    # 02 Source Map
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.title = 'Source_Map'
    ws2.append(['Compound_ID', 'Name_EN', 'Herb_Name', 'Herb_ID', 'Species',
                'Database', 'Source_ID', 'Literature', 'Duplicate_Status', 'QC_Status'])
    for r in source_map_rows:
        ws2.append([r['Compound_ID'], r['Name_EN'], r['Herb_Name'], r['Herb_ID'],
                    r['Species'], r['Database'], r['Source_ID'], r['Literature'],
                    r['Duplicate_Status'], r['QC_Status']])
    wb2.save(p2)

    # 03 QC Report
    wb3 = openpyxl.Workbook()
    ws3 = wb3.active
    ws3.title = 'QC_Report'
    ws3.append(['Compound_ID', 'Name_EN', 'SMILES_Parse', 'Stereo_Status',
                'Identity_Status', 'Salt_Status', 'Tautomer_Status',
                'Duplicate_Status', 'QC_Score', 'QC_Status', 'QC_Reason'])
    for r in qc_rows:
        ws3.append([r['Compound_ID'], r['Name_EN'], r['SMILES_Parse'], r['Stereo_Status'],
                    r['Identity_Status'], r['Salt_Status'], r['Tautomer_Status'],
                    r['Duplicate_Status'], r['QC_Score'], r['QC_Status'], r['QC_Reason']])
    wb3.save(p3)

    # 04 AF3 Input（每行一个分子：ID + Isomeric SMILES）
    n_af3 = 0
    with open(p4, 'w', encoding='utf-8') as f:
        for c in compounds:
            if af3_only_pass and c['QC_Status'] != 'PASS':
                continue
            smi = c['SMILES_Isomeric_RDKit'] or c['SMILES_Canonical_RDKit']
            if smi:
                f.write(f"{c['Compound_ID']}\t{smi}\n")
                n_af3 += 1
    return {'master': p1, 'source_map': p2, 'qc_report': p3, 'af3_input': p4,
            'n_master': len(compounds), 'n_source_map': len(source_map_rows),
            'n_af3': n_af3}


# ============================================================
# 主流程
# ============================================================
def build_molecular_library(input_xlsx, out_dir, sheet_name=None,
                            use_pubchem=True, name_query_limit=0,
                            cas_verify_limit=0, cid_prefix='C', start_id=1):
    """分子库构建主流程（Agent A → B → C）

    Args:
        input_xlsx: 输入分子库（含 Name/CAS/CID/SMILES/来源药材 等列）
        out_dir: 输出目录
        use_pubchem: 是否调用 PubChem API 交叉验证（无网/大批量可关）
        name_query_limit: 无 CID 记录的 name→CID 逐条查询上限（0=不查，None=全部）
        cas_verify_limit: CAS→synonyms 逐条验证上限（0=不查，None=全部；全量很慢慎用）
        cid_prefix / start_id: Compound_ID 前缀与起始编号
    """
    t0 = time.time()
    print('=== 分子库构建工作流（Agent A/B/C）===')
    records = load_input_records(input_xlsx, sheet_name)
    print(f'[输入] {len(records)} 条原始记录')

    # --- Agent A：Identity ---
    if use_pubchem:
        print('[Agent A] Identity 解析（PubChem 批量交叉验证）...')
        try:
            identity_agent_batch(records, name_query_limit=name_query_limit,
                                 cas_verify_limit=cas_verify_limit)
        except Exception as e:
            print(f'  [Agent A] 批量验证异常({e})，回退离线模式')
            for rec in records:
                _identity_offline(rec)
    else:
        print('[Agent A] Identity 解析（离线模式，库内证据基线）...')
        for rec in records:
            _identity_offline(rec)
    t_a = time.time()

    # --- Agent B：Structure QC ---
    print(f'[Agent B] Structure QC（RDKit）...')
    for rec in records:
        try:
            structure_qc_agent(rec)
        except Exception as e:
            rec['SMILES_Parse'] = 'fail'
            rec.setdefault('qc_notes', []).append(f'B-agent error: {e}')
    t_b = time.time()

    # --- Agent C：Library Manager ---
    print(f'[Agent C] Library Manager（去重/QC/编号）...')
    compounds, source_map_rows, qc_rows = library_manager_agent(
        records, cid_prefix=cid_prefix, start_id=start_id)
    t_c = time.time()

    # --- 输出 ---
    paths = write_outputs(compounds, source_map_rows, qc_rows, out_dir)

    # --- 摘要 ---
    n_parse_ok = sum(1 for r in records if r.get('SMILES_Parse') == 'ok')
    qc_dist = defaultdict(int)
    for q in qc_rows:
        qc_dist[q['QC_Status']] += 1
    print(f'''
[摘要]
  输入记录: {len(records)}
  SMILES解析成功: {n_parse_ok}
  化合物数(去重后): {len(compounds)}
  来源映射行: {len(source_map_rows)}
  QC分布: {dict(qc_dist)}
  AF3 输入分子: {paths['n_af3']}
  耗时: Agent A {t_a-t0:.0f}s | Agent B {t_b-t_a:.0f}s | Agent C {t_c-t_b:.0f}s
[输出]
  {paths['master']}
  {paths['source_map']}
  {paths['qc_report']}
  {paths['af3_input']}
=== 完成 ===''')
    return {'records': records, 'compounds': compounds,
            'source_map': source_map_rows, 'qc': qc_rows, 'paths': paths}


# ============================================================
# 入口
# ============================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="分子库构建（14 步标准流程）")
    parser.add_argument("--input", required=True, help="输入分子库 Excel（筛选版）")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--no-pubchem", action="store_true", help="跳过 PubChem 在线验证（离线模式）")
    parser.add_argument("--name-query-limit", type=int, default=0, help="逐条限速名称查询条数上限（0=不查，None=全部）")
    parser.add_argument("--cas-verify-limit", type=int, default=0, help="CAS 校验查询条数上限（0=不查，None=全部）")
    args = parser.parse_args()

    build_molecular_library(args.input, args.out, use_pubchem=not args.no_pubchem,
                            name_query_limit=args.name_query_limit,
                            cas_verify_limit=args.cas_verify_limit)
