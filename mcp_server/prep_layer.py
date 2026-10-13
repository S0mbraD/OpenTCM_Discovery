#!/usr/bin/env python3
"""prep_layer: local table, identifier, structure, gene, disease, and dose cleanup.

Six tools. None of them call the network. Alias hits and dose ratios are
bookkeeping for the next retrieval step. They are not recorded targets,
MeSH coverage, or efficacy.
"""
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tcm_core.prep import (  # noqa: E402
    PrepError,
    clean_table,
    convert_doses,
    normalize_disease,
    normalize_genes,
    normalize_ids,
    normalize_structure,
)

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase

from pydantic import BaseModel, ConfigDict, Field

mcp = _MCPBase("tcm_prep_layer")

_READ = {"readOnlyHint": True, "destructiveHint": False,
         "idempotentHint": True, "openWorldHint": False}


def _show(title: str, data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return f"# {title}\n\n{data.get('note', '')}\n\n```json\n{body}\n```"


class CleanTableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., min_length=1, max_length=100000,
                      description="粘贴的 CSV、TSV 或用分号分隔的表")
    delimiter: str | None = Field(
        None, max_length=20,
        description="tab、comma、semicolon、pipe；留空则自动判断")


class NormalizeIdsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., min_length=1, max_length=20000,
                      description="含 CID、CAS、InChIKey、UniProt 或 Ensembl 号的文本")


class NormalizeStructureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smiles: str = Field(..., min_length=1, max_length=4020,
                        description="一条 SMILES。盐用点号连接时会保留")


class NormalizeGenesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: str = Field(..., min_length=1, max_length=20000,
                         description="基因符号，可用逗号、分号、换行或空格分隔")


class NormalizeDiseaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    names: str = Field(..., min_length=1, max_length=8000,
                       description="疾病名，用分号、顿号或换行分隔。英文逗号留给 MeSH 标题")


class ConvertDoseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doses: str = Field(..., min_length=1, max_length=8000,
                       description="剂量，用分号或换行分隔。逗号留给千分位")
    convention: str = Field(
        ..., min_length=1, max_length=40,
        description="metric、market_1959、han_tang、song_yuan_2025、"
                    "ming_qing_2025、song_jin_yuan_2024、ming_qing_2024")


@mcp.tool(name="tcm_clean_table", annotations={
    "title": "Clean a pasted research table", **_READ})
async def tcm_clean_table(params: CleanTableInput) -> str:
    '''把粘贴的表整理成表头和行。

    全角标点会换成半角。空行去掉。第一行是表头。某一行列数对不上就报错，
    不补空列。

    Args:
        params (CleanTableInput): 表文本，分隔符可省略。

    Returns:
        str: 列名、数据行，以及一句说明。
    '''
    try:
        data = clean_table(params.text, params.delimiter)
    except PrepError as exc:
        return f"Error: {exc}"
    return _show("表格整理", data)


@mcp.tool(name="tcm_normalize_ids", annotations={
    "title": "Check compound and gene identifier shapes", **_READ})
async def tcm_normalize_ids(params: NormalizeIdsInput) -> str:
    '''从文本里认出 CID、CAS、InChIKey、UniProt 登录号和 Ensembl 基因号。

    只检查格式。CAS 会核对校验位。裸数字不会当成 PubChem CID，因为同一个
    数字也可能是 NCBI Gene ID。本工具不访问数据库。

    Args:
        params (NormalizeIdsInput): 一段标识文本。

    Returns:
        str: 识别出的标识、校验结果，以及未能归类的词。
    '''
    try:
        data = normalize_ids(params.text)
    except PrepError as exc:
        return f"Error: {exc}"
    return _show("标识整理", data)


@mcp.tool(name="tcm_normalize_structure", annotations={
    "title": "Check SMILES and optionally canonicalize with RDKit", **_READ})
async def tcm_normalize_structure(params: NormalizeStructureInput) -> str:
    '''检查一条 SMILES。装了 RDKit 时补上 RDKit 规范 SMILES 和 InChIKey。

    没有 RDKit 时只做字符和括号检查，并写明没有生成 InChIKey。点号后面的
    抗衡离子会保留。RDKit 的规范 SMILES 和 PubChem 的 CanonicalSMILES 不是
    同一套算法。

    Args:
        params (NormalizeStructureInput): 一条 SMILES。

    Returns:
        str: 清理后的字符串、可选的规范 SMILES 和 InChIKey。
    '''
    try:
        data = normalize_structure(params.smiles)
    except PrepError as exc:
        return f"Error: {exc}"
    return _show("结构式整理", data)


@mcp.tool(name="tcm_normalize_genes", annotations={
    "title": "Collapse gene-symbol aliases", **_READ})
async def tcm_normalize_genes(params: NormalizeGenesInput) -> str:
    '''把常见别名收成一个 HGNC 符号，并去掉重复。

    TNF-α、COX-2、IL-8、Nrf2 这类写法会归到 TNF、PTGS2、CXCL8、NFE2L2。
    NF-κB、COX、VEGF、AKT 这类家族名保持歧义，不猜其中一个。本地表里没有的
    符号按大写保留，并标成未收录。这不是 HGNC 全表，也不说明该基因是靶点。

    Args:
        params (NormalizeGenesInput): 基因符号列表。

    Returns:
        str: 归并后的符号、歧义项和未收录项。
    '''
    try:
        data = normalize_genes(params.symbols)
    except PrepError as exc:
        return f"Error: {exc}"
    return _show("基因符号整理", data)


@mcp.tool(name="tcm_normalize_disease", annotations={
    "title": "Map common disease names to a checked MeSH heading", **_READ})
async def tcm_normalize_disease(params: NormalizeDiseaseInput) -> str:
    '''把少量常见病名对到已经核对过的 MeSH 标题。

    溃疡性结肠炎和 ulcerative colitis 会落到同一条 Colitis, Ulcerative。
    UC 这类缩写、癌症这类过宽的词，以及表里没有的病名，都不会得到编造的
    MeSH 号。这不是 MeSH 全表，也不是诊断。

    Args:
        params (NormalizeDiseaseInput): 疾病名。分号、顿号或换行分隔。

    Returns:
        str: MeSH 标题，或 unmapped / ambiguous / nonspecific。
    '''
    try:
        data = normalize_disease(params.names)
    except PrepError as exc:
        return f"Error: {exc}"
    return _show("疾病名整理", data)


@mcp.tool(name="tcm_convert_dose", annotations={
    "title": "Convert a stated dose convention into grams", **_READ})
async def tcm_convert_dose(params: ConvertDoseInput) -> str:
    '''按写明的度量衡把剂量换成克，并给出这次输入里的重量比。

    公制用 metric。市制 1 两 = 50 g 用 market_1959，这不是古方剂量。
    汉唐一两 = 13.8 g 用 han_tang。宋元、明清各有 2025 年网络药理学用表和
    2024 年经典名方考证两套数字，必须点名其中一套。体积不会换成克。
    重量比不是药效。

    Args:
        params (ConvertDoseInput): 剂量文本和口径名。

    Returns:
        str: 每条的克数、相对最小剂量的比例，以及口径出处。
    '''
    try:
        data = convert_doses(params.doses, params.convention)
    except PrepError as exc:
        return f"Error: {exc}"
    return _show("剂量换算", data)
