#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
化合物去重 + RDKit 结构分类模板脚本

用途：给定药材-成分关联数据（CSV/JSON），执行去重（按英文名）并使用 RDKit 按 SMILES 自动分类。
输出：去重分类后的 Excel 文件，含"来源药材"合并列和"化学结构大类"列。

分类逻辑（优先级自上而下）：
  1. 黄酮（三层检测：芳香色酮核 SMARTS / 黄烷酮核 SMARTS / 环特征兜底，
     含 2/3-位环外芳基约束以区分香豆素）
  2. 甾体（环戊烷多氢菲四环骨架）
  3. 生物碱（含氮骨架优先于萜类碳数规则：环内N或多环含氮；排除氨基酸、含氮糖苷）
  4. 糖及苷类（含吡喃糖/呋喃糖环，优先于萜类碳数规则）
  5. 萜类（不含氮且无芳香环时按碳数：C8-12 单萜 / C13-17 倍半萜 / C18-24 二萜 /
     C25-32 三萜 / >C32 四萜；芳香族如阿魏酸不适用碳数规则）
  6. 有机酸（含羧基且 ≤20 重原子）
  7. 芳香族/脂肪族兜底

依赖：pip install rdkit pandas openpyxl

使用方法：
  python dedup_classify_template.py <输入CSV> <输出Excel>
  CSV 需含列：Ingredient_name, Canonical_smiles, 来源药材, 以及其他需保留的字段
"""

import sys
import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors

# ============================================================
# 1. RDKit 化合物结构分类函数
# ============================================================

def classify_compound(smiles: str) -> str:
    """根据 SMILES 自动分类化合物结构大类。

    分类优先级（自上而下，先命中先归类）：
      1. 黄酮（色酮/色满酮骨架，三层检测：芳香 SMARTS / 黄烷酮 SMARTS / 环特征兜底）
      2. 甾体（环戊烷多氢菲四环骨架）
      3. 生物碱（含氮骨架，优先于萜类碳数规则：环内N或多环含氮；
         排除氨基酸、含氮糖苷）
      4. 糖及苷类（含吡喃糖/呋喃糖环，优先于萜类碳数规则——
         糖基碳数会干扰萜类碳数判断，如人参皂苷）
      5. 萜类（仅不含氮骨架且无芳香环时按碳数分类；
         芳香族如阿魏酸/大黄酸不得因碳数落入单萜区间）
      6. 有机酸（含羧基且 ≤20 重原子）
      7. 芳香族/脂肪性兜底
      8. 无法解析 → 未分类
    """
    if not smiles or pd.isna(smiles) or str(smiles).strip() == "":
        return "未分类"

    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None:
        return "未分类"

    # --- 基本信息 ---
    n_rings = rdMolDescriptors.CalcNumRings(mol)
    n_aromatic = rdMolDescriptors.CalcNumAromaticRings(mol)
    n_atoms = mol.GetNumHeavyAtoms()
    has_nitrogen = any(atom.GetAtomicNum() == 7 for atom in mol.GetAtoms())
    has_carboxyl = _has_carboxyl(mol)
    has_sugar = _has_sugar(mol)

    # --- 1. 特征骨架（先于碳数规则） ---

    # 黄酮类：C6-C3-C6 骨架，含色酮环（三层检测，详见 _is_flavonoid）
    if _is_flavonoid(mol):
        return "黄酮类"

    # 甾体类：环戊烷多氢菲骨架（四环稠合）
    if _is_steroid(mol, n_rings):
        return "甾体类"

    # 生物碱：含氮骨架优先于萜类碳数判断（小檗碱 C20 含氮 → 生物碱，非二萜）
    # 排除：氨基酸（脯氨酸/色氨酸）、含氮糖苷等非生物碱化合物
    if has_nitrogen and _is_alkaloid_like(mol, has_sugar, has_carboxyl, n_rings):
        alkaloid_type = _classify_alkaloid(mol, n_rings, n_aromatic)
        if alkaloid_type:
            return alkaloid_type
        return "其他生物碱"

    # 糖及苷类：含糖基优先于萜类碳数规则（苷元+糖的碳数会误判，如人参皂苷）
    if has_sugar:
        return "糖及苷类"

    # --- 2. 萜类：按碳数分类（仅不含氮骨架时） ---
    # 芳香族不适用碳数规则：阿魏酸(C10芳香)/大黄酸(C15二苯环)曾误判为单萜
    if not has_nitrogen and n_aromatic == 0 and n_atoms >= 10:
        terpene_type = _classify_terpene(mol, n_atoms)
        if terpene_type:
            return terpene_type

    # --- 3. 官能团 ---

    # 有机酸类
    if has_carboxyl and n_atoms <= 20:
        return "有机酸类"

    # --- 4. 芳香性/脂肪性 ---

    if n_aromatic > 0:
        return "芳香族"
    else:
        return "脂肪族"


def _has_carboxyl(mol) -> bool:
    """检测是否含羧基（-COOH）"""
    patt = Chem.MolFromSmarts("[CX3](=O)[OX1H0,OX2H1]")
    return mol.HasSubstructMatch(patt) if patt else False


def _has_sugar(mol) -> bool:
    """检测是否含糖基（吡喃糖/呋喃糖环）"""
    patt_pyranose = Chem.MolFromSmarts("[C]1[C][C][C][O][C]1")
    patt_furanose = Chem.MolFromSmarts("[C]1[C][C][O][C]1")
    has_p = mol.HasSubstructMatch(patt_pyranose) if patt_pyranose else False
    has_f = mol.HasSubstructMatch(patt_furanose) if patt_furanose else False
    return has_p or has_f


# 氨基酸特征（α-氨基羧酸）：用于从生物碱中排除氨基酸
_AA_PATT = Chem.MolFromSmarts("[NX3,NX4+][CX4H][CX3](=O)[OX1H0,OX2H1]")
# 酰胺键：含酰胺的含氮化合物不是氨基酸（如石杉碱甲的吡啶酮环），不应用氨基酸模式排除
_AMIDE_PATT = Chem.MolFromSmarts("[NX3][CX3](=O)")


def _is_flavonoid(mol) -> bool:
    """检测黄酮类骨架（C6-C3-C6，色酮/色满酮核 + 2/3-位芳基），三层检测。

    L1 芳香色酮核 SMARTS（标准芳香式 SMILES，覆盖黄酮/黄酮醇/异黄酮/芳香式苷）
    L2 黄烷酮核 SMARTS（2,3-二氢，覆盖黄烷酮/二氢黄酮醇）
    L3 环特征兜底：6~7 元含单 O 杂环 + 环内 C=O 酮 + 稠合芳香环 +
       杂环碳连【环外】芳香环（2-苯基/3-芳基特征，区分香豆素）

    已知局限：儿茶素等黄烷醇（无环酮）与芳香型二萜（丹参酮）不入黄酮类。
    """
    # L1: 全芳香色酮核（4H-chromen-4-one）
    patt_ar = Chem.MolFromSmarts("c1ccc2c(c1)occc2=O")
    if patt_ar and mol.HasSubstructMatch(patt_ar):
        return True
    # L2: 黄烷酮核（2,3-二氢色原酮）
    patt_flavanone = Chem.MolFromSmarts("O=C1CCc2ccccc2O1")
    if patt_flavanone and mol.HasSubstructMatch(patt_flavanone):
        return True
    # L3: 环特征兜底（兼容半芳香/非标准写法）
    return _flavonoid_ring_features(mol)


def _flavonoid_ring_features(mol) -> bool:
    """环系统特征检测黄酮核（兜底层，不依赖 SMILES 表示风格）"""
    ri = mol.GetRingInfo()
    rings = [list(r) for r in ri.AtomRings()]
    ring_sets = [set(r) for r in rings]

    def _has_exo_aromatic(ring, rs):
        """杂环上的碳连接【环外】芳香环（排除稠合环原子，区分香豆素）"""
        # 收集所有与当前环共享 ≥2 原子的环（稠合环）成员，全部排除
        fused = set()
        for other in ring_sets:
            if other != rs and len(other & rs) >= 2:
                fused |= other
        for i in ring:
            a = mol.GetAtomWithIdx(i)
            if a.GetAtomicNum() != 6:
                continue
            for nb in a.GetNeighbors():
                if nb.GetIdx() in rs or nb.GetIdx() in fused:
                    continue
                if nb.GetAtomicNum() == 6 and nb.GetIsAromatic():
                    for other in ring_sets:
                        if nb.GetIdx() in other and len(other & rs) < 2:
                            n_ar_c = sum(
                                1 for j in other
                                if mol.GetAtomWithIdx(j).GetAtomicNum() == 6
                                and mol.GetAtomWithIdx(j).GetIsAromatic()
                            )
                            if n_ar_c >= 5:
                                return True
        return False

    for ring, rs in zip(rings, ring_sets):
        # 6~7 元环（部分非标准 SMILES 的色酮核被 SSSR 报为 7 元）
        if len(ring) not in (6, 7):
            continue
        # 恰好 1 个环 O
        if sum(1 for i in ring if mol.GetAtomWithIdx(i).GetAtomicNum() == 8) != 1:
            continue
        # 找稠合芳香环（≥5 个芳香碳）
        fused_ar = None
        for other in ring_sets:
            if other == rs or len(other & rs) < 2:
                continue
            n_ar_c = sum(
                1 for j in other
                if mol.GetAtomWithIdx(j).GetAtomicNum() == 6
                and mol.GetAtomWithIdx(j).GetIsAromatic()
            )
            if n_ar_c >= 5:
                fused_ar = other
                break
        if not fused_ar:
            continue
        # 环内碳连环外双键 O（酮）；酮碳允许在杂环或稠合环内
        # （异黄酮如葛根素的 4-酮碳与 A 环共轭，落在稠合苯环表示内）
        scope = rs | fused_ar
        has_ketone = any(
            b.GetBondType() == Chem.BondType.DOUBLE
            and mol.GetAtomWithIdx(b.GetOtherAtomIdx(i)).GetSymbol() == "O"
            and b.GetOtherAtomIdx(i) not in scope
            for i in scope if mol.GetAtomWithIdx(i).GetAtomicNum() == 6
            for b in mol.GetAtomWithIdx(i).GetBonds()
        )
        if not has_ketone:
            continue
        if _has_exo_aromatic(ring, rs):
            return True
    return False


def _is_alkaloid_like(mol, has_sugar: bool, has_carboxyl: bool, n_rings: int) -> bool:
    """判断含氮化合物是否更像生物碱（而非氨基酸/核苷/糖苷等）。

    判据（满足其一即认为是生物碱骨架）：
      1. 氮原子在环内（环内 N 是生物碱最典型特征，如小檗碱异喹啉稠环）
      2. 多环含氮且非羧酸（部分无环内氮的生物碱）
    排除：
      - α-氨基酸（脯氨酸/色氨酸等，N 在环内但属氨基酸）
      - 含糖基的含氮化合物（多为苷，优先走糖及苷类）
    """
    if has_sugar:
        # 含氮糖苷优先走糖及苷类
        return False
    # 氨基酸排除：α-氨基羧酸模式（脯氨酸/色氨酸等，N 在环内也属氨基酸）；
    # 但含酰胺键的分子（如石杉碱甲的吡啶酮稠环）不是氨基酸，不排除
    if _AA_PATT and mol.HasSubstructMatch(_AA_PATT):
        if not (_AMIDE_PATT and mol.HasSubstructMatch(_AMIDE_PATT)):
            return False
    ring_info = mol.GetRingInfo()
    n_in_ring = any(
        atom.GetAtomicNum() == 7 and ring_info.NumAtomRings(atom.GetIdx()) > 0
        for atom in mol.GetAtoms()
    )
    if n_in_ring:
        return True
    # 无环内氮但多环含氮且非羧酸（如部分萜类生物碱）仍按生物碱处理；
    # 单环/无环的小分子（生物胺、氨基酸）不划入生物碱
    return n_rings >= 2 and not has_carboxyl


def _is_steroid(mol, n_rings) -> bool:
    """检测甾体骨架（四环稠合的环戊烷多氢菲）"""
    if n_rings < 4:
        return False
    patt = Chem.MolFromSmarts("C1CCC2C(C1)CCC3C2CCC4C3CCC4")
    return bool(patt and mol.HasSubstructMatch(patt))


def _classify_terpene(mol, n_atoms):
    """按碳数分类萜类（标准异戊二烯单元：单萜 C10 / 倍半萜 C15 / 二萜 C20 / 三萜 C30）

    边界容忍 ±2~3 碳（含氧取代/脱甲基变体）。调用方已保证：
    不含氮骨架且无芳香环（芳香族如阿魏酸/大黄酸不适用碳数规则）。
    """
    c_count = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 6)
    if 8 <= c_count <= 12:
        return "单萜类"          # C10 典型
    elif 13 <= c_count <= 17:
        return "倍半萜类"        # C15 典型（青蒿素）
    elif 18 <= c_count <= 24:
        return "二萜类"          # C20 典型
    elif 25 <= c_count <= 32:
        return "三萜类"          # C30 典型
    elif c_count > 32:
        return "四萜类"          # C40 典型（胡萝卜素）
    return None


def _classify_alkaloid(mol, n_rings, n_aromatic):
    """生物碱子类分类（基于典型骨架 SMARTS 匹配）"""
    subpatterns = [
        # 异喹啉类（含小檗碱型、原小檗碱型）
        ("异喹啉类生物碱", Chem.MolFromSmarts("c1ccc2cnccc2c1")),
        # 喹啉类
        ("喹啉类生物碱", Chem.MolFromSmarts("c1ccc2nccccc2c1")),
        # 吲哚类（含色胺、马钱子碱型等）
        ("吲哚类生物碱", Chem.MolFromSmarts("c1ccc2[nH]ccc2c1")),
        # 吡啶类
        ("吡啶类生物碱", Chem.MolFromSmarts("ccccn")),
        # 莨菪烷/托品烷（莨菪碱、东莨菪碱）
        ("莨菪烷类生物碱", Chem.MolFromSmarts("CN1C2CCC1CC(C2)OC(=O)")),
    ]
    for name, patt in subpatterns:
        if patt and mol.HasSubstructMatch(patt):
            return name
    return None


# ============================================================
# 2. 去重函数
# ============================================================

def dedup_by_name(df: pd.DataFrame, name_col: str = "Ingredient_name",
                 source_col: str = "来源药材") -> pd.DataFrame:
    """按成分英文名去重，合并来源药材。

    策略：
      - 同一英文名的多行合并为一行
      - 来源药材列合并为分号分隔
      - 其他字段取首次出现的值
    """
    # 确保来源药材列存在
    if source_col not in df.columns:
        df[source_col] = ""

    # 按英文名分组，合并来源药材
    def merge_sources(group):
        sources = group[source_col].dropna().astype(str)
        sources = sources[sources.str.strip() != ""]
        merged = ";".join(sorted(set(sources)))
        row = group.iloc[0].copy()
        row[source_col] = merged
        return row

    deduped = df.groupby(name_col, sort=False).apply(merge_sources).reset_index(drop=True)
    return deduped


# ============================================================
# 4. 分子量补算函数
# ============================================================

import re

# 常见元素原子量
ATOMIC_WEIGHTS = {
    'H': 1.008, 'C': 12.011, 'N': 14.007, 'O': 15.999, 'P': 30.974,
    'S': 32.06, 'F': 18.998, 'Cl': 35.45, 'Br': 79.904, 'I': 126.904,
    'Na': 22.990, 'K': 39.098, 'Ca': 40.078, 'Mg': 24.305, 'Fe': 55.845,
    'Zn': 65.38, 'Cu': 63.546, 'Mn': 54.938, 'Se': 78.971, 'Si': 28.085,
    'B': 10.81, 'Al': 26.982, 'As': 74.922,
}


def backfill_mw_from_smiles(df: pd.DataFrame, smiles_col: str = "Canonical_smiles",
                            mw_col: str = "Molecular_weight") -> pd.DataFrame:
    """对有 SMILES 但无分子量的化合物，用 RDKit 补算分子量。

    实测：15,891 条中 2,660 条通过此方法补算，分子量覆盖率从 78% → ~100%。
    """
    from rdkit import Chem
    from rdkit.Chem import Descriptors

    def _calc_mw(smiles):
        if not smiles or pd.isna(smiles) or str(smiles).strip() == "":
            return None
        mol = Chem.MolFromSmiles(str(smiles))
        if mol is None:
            return None
        return round(Descriptors.MolWt(mol), 2)

    mask = (df[mw_col].isna()) | (df[mw_col] == "") | (df[mw_col] == 0)
    to_fill = df.loc[mask & df[smiles_col].notna() & (df[smiles_col] != "")]
    if len(to_fill) > 0:
        df.loc[to_fill.index, mw_col] = to_fill[smiles_col].apply(_calc_mw)
        filled = df.loc[to_fill.index, mw_col].notna().sum()
        print(f"  RDKit 补算分子量: {filled} 条")
    return df


def backfill_mw_from_formula(df: pd.DataFrame, formula_col: str = "Molecular_formula",
                             mw_col: str = "Molecular_weight") -> pd.DataFrame:
    """对有分子式但无 SMILES 且无分子量的化合物，解析分子式补算分子量。

    实测：652 条通过此方法补算。
    """
    def _parse_formula(formula):
        if not formula or pd.isna(formula) or str(formula).strip() == "":
            return None
        formula = str(formula).strip()
        # 去除电荷标记（如 +、-、2+）
        formula = re.sub(r'[+\-]\d*$', '', formula)
        # 去除水合物标记（如 ·2H2O）
        formula = re.sub(r'·\d*H2O', '', formula)
        # 匹配元素符号和原子数
        matches = re.findall(r'([A-Z][a-z]?)(\d*)', formula)
        total = 0.0
        for elem, count in matches:
            if elem in ATOMIC_WEIGHTS:
                total += ATOMIC_WEIGHTS[elem] * (int(count) if count else 1)
            else:
                return None  # 含未知元素，放弃
        return round(total, 2) if total > 0 else None

    mask = (df[mw_col].isna()) | (df[mw_col] == "") | (df[mw_col] == 0)
    to_fill = df.loc[mask & df[formula_col].notna() & (df[formula_col] != "")]
    if len(to_fill) > 0:
        df.loc[to_fill.index, mw_col] = to_fill[formula_col].apply(_parse_formula)
        filled = df.loc[to_fill.index, mw_col].notna().sum()
        print(f"  分子式解析补算分子量: {filled} 条")
    return df


# ============================================================
# 5. 主流程
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="多基原中药化合物合并去重 + RDKit 结构分类")
    parser.add_argument("input_csv", help="输入 CSV（需含列：Ingredient_name, Canonical_smiles, 来源药材）")
    parser.add_argument("output_xlsx", help="输出 Excel 路径")
    args = parser.parse_args()

    input_csv = args.input_csv
    output_xlsx = args.output_xlsx

    # 1. 读取数据
    print(f"[1/4] 读取 {input_csv} ...")
    df = pd.read_csv(input_csv, encoding="utf-8")
    print(f"  原始记录: {len(df)} 条")

    # 2. 去重
    print(f"[2/4] 按英文名去重 ...")
    deduped = dedup_by_name(df)
    print(f"  去重后: {len(deduped)} 条唯一化合物")
    multi_source = deduped[deduped["来源药材"].str.contains(";", na=False)]
    print(f"  多来源化合物: {len(multi_source)} 条")

    # 3. RDKit 分类
    print(f"[3/4] RDKit 结构分类 ...")
    smiles_col = "Canonical_smiles" if "Canonical_smiles" in deduped.columns else "canonical_smiles"
    deduped["化学结构大类"] = deduped[smiles_col].apply(classify_compound)

    # 分类统计
    print("\n  分类分布：")
    for cat, count in deduped["化学结构大类"].value_counts().items():
        print(f"    {cat}: {count}")

    # 4. 输出 Excel
    print(f"\n[4/4] 输出 {output_xlsx} ...")
    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
        deduped.to_excel(writer, sheet_name="去重分类结果", index=False)
    print(f"  完成！共 {len(deduped)} 行")


if __name__ == "__main__":
    main()
