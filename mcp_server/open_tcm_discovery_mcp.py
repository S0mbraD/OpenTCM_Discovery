#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
open_tcm_discovery_mcp —— 中药药物发现 MCP 服务器
====================================================

检索、网络、富集与自迭代工具族。完整技能的 63 工具入口是同目录
unified_mcp.py（StreamableHTTP 默认 127.0.0.1:8002）。单独运行本文件
只暴露本文件内注册的工具，默认端口 8000，不是 63 工具服务器。
TCMSP 旧站查询在 tcmsp_layer.py，不在本文件。

数据源（2026-09-22 实测可用性，详见技能 references/database_availability_2026-09.md）：
    - 中药库：HERB 2.0 / TCMBank / NPASS / SymMap
    - 化学库：PubChem PUG-REST（1s 限流 + 重试）/ Cactus / KEGG / ChEMBL（2026-09 恢复）
    - 靶点疾病库：Open Targets GraphQL / CTD / DGIdb GraphQL / UniProt SPARQL（DrugBank 替代）
    - 生信库：UniProt REST / STRING / NCBI EUtils / Reactome / PDB / AlphaFold /
      InterPro / g:Profiler / Enrichr / JASPAR / GWAS Catalog / ClinVar / dbSNP /
      Ensembl REST（2026-09 恢复）
    - 2026-09-22 探测：当时的 TCMSP 入口 502；2026-10-13 起成分表走 old.tcmsp-e.com（tcmsp_layer.py）。ETCM 2.0（/ETCM/api/ 404，主站活）/
      DCABM-TCM（SPA 活，API 未定位）/ DisGeNET（需 Key，公开端点回退 SPA）

运行：
    python open_tcm_discovery_mcp.py                # StreamableHTTP, 127.0.0.1:8000/mcp
    python open_tcm_discovery_mcp.py --port 8001    # 自定义端口
    python open_tcm_discovery_mcp.py --stdio        # stdio 模式（本地子进程）

依赖：pip install mcp httpx pydantic
'''
import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import quote as _urlencode

import httpx
from pydantic import BaseModel, Field, ConfigDict

# MCP SDK：兼容 1.x（FastMCP）与 2.x（MCPServer，FastMCP 更名）
try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

# ================= 服务器初始化 =================

mcp = _MCPBase("open_tcm_discovery_mcp")

HTTP_UA = {"User-Agent": "open-tcm-discovery-mcp/1.0 (research)"}
DEFAULT_TIMEOUT = 30.0

# 限流策略（来自既往通道实战：PubChem 1s 间隔 + 4 次重试 + 5xx 退避）
PUBCHEM_MIN_INTERVAL = 1.0
PUBCHEM_MAX_RETRIES = 4
RETRY_BACKOFF = 5.0
_last_pubchem_ts = 0.0


# ================= 熔断器 =================
# 针对间歇断连上游（如 TCMBank 实测 3 次调用 1 次失败）：
# 连续 N 次失败 → 熔断 OPEN（直接拒绝请求，避免堆积超时）→ 冷却期后半开探测
_CIRCUIT_CONFIG = {
    "tcmbank.cn": {"fail_threshold": 3, "cooldown": 60.0},   # 3 连败熔断 60s
    "default": {"fail_threshold": 5, "cooldown": 30.0},
}
_circuit_state = {}   # host -> {"fails": int, "open_until": float}


def _circuit_check(host: str) -> Optional[str]:
    '''熔断检查：OPEN 状态且未过冷却期则返回错误消息，否则 None（允许请求）'''
    st = _circuit_state.get(host)
    if not st:
        return None
    if st.get("open_until", 0) > time.monotonic():
        remain = int(st["open_until"] - time.monotonic())
        return (f"Error: upstream {host} circuit breaker OPEN "
                f"({remain}s cooldown remaining; recent consecutive failures). "
                f"Retry later or use tcm_check_database_status for alternatives.")
    return None


def _circuit_record(host: str, ok: bool) -> None:
    '''记录请求结果：成功重置计数；失败累计，达阈值后进入 OPEN'''
    cfg = _CIRCUIT_CONFIG.get(host, _CIRCUIT_CONFIG["default"])
    st = _circuit_state.setdefault(host, {"fails": 0, "open_until": 0.0})
    if ok:
        st["fails"] = 0
        st["open_until"] = 0.0
    else:
        st["fails"] += 1
        if st["fails"] >= cfg["fail_threshold"]:
            st["open_until"] = time.monotonic() + cfg["cooldown"]


# ================= 输入模型 =================

class ResponseFormat(str, Enum):
    '''Tool response output format.'''
    MARKDOWN = "markdown"
    JSON = "json"


class PubChemLookupInput(BaseModel):
    '''Lookup compound info in PubChem by name or CID.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: Optional[str] = Field(default=None, description="Compound name, e.g. 'berberine' or 'quercetin'", max_length=200)
    cid: Optional[int] = Field(default=None, description="PubChem CID, e.g. 2244 (aspirin)", ge=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")

    @property
    def has_query(self) -> bool:
        return bool(self.name or self.cid)


class KeggCompoundInput(BaseModel):
    '''Query KEGG compound entry by ID or name.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description="KEGG compound ID (e.g. 'C00757', uppercase required) or name (e.g. 'berberine')", min_length=2, max_length=100)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class OpenTargetsInput(BaseModel):
    '''Search Open Targets: disease (by MONDO) or target (by Ensembl gene ID).'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    disease_name: Optional[str] = Field(default=None, description="Disease search term, e.g. 'ulcerative colitis' (returns MONDO IDs)", max_length=200)
    mondo_id: Optional[str] = Field(default=None, description="Disease MONDO ID, e.g. 'MONDO_0005101' (UC). CAUTION: MONDO_0004989 is breast cancer", max_length=50)
    ensembl_id: Optional[str] = Field(default=None, description="Target Ensembl ID, e.g. 'ENSG00000168610' (STAT3)", max_length=50)
    limit: int = Field(default=10, description="Max targets to return", ge=1, le=50)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class ChEMBLInput(BaseModel):
    '''Lookup compound + mechanism targets in ChEMBL (2026-09: recovered, was 500).'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description="Compound name or ChEMBL ID (e.g. 'berberine' or 'CHEMBL251414')", min_length=2, max_length=200)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class EnsemblLookupInput(BaseModel):
    '''Look up a human gene in Ensembl REST (symbol or Ensembl ID).'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    gene: str = Field(..., description="Gene symbol (e.g. 'TNF') or Ensembl gene ID (e.g. 'ENSG00000232810')", min_length=2, max_length=40)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class StringPpiInput(BaseModel):
    '''STRING PPI network query for a gene list.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    genes: List[str] = Field(..., description="Gene symbols, e.g. ['TNF','IL6','PTGS2']", min_length=1, max_length=50)
    species: str = Field(default="9606", description="NCBI taxon ID, 9606=human", max_length=10)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class TcmBankInput(BaseModel):
    '''TCMBank search across ingredient/herb/gene/disease entities.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entity: str = Field(..., description="Entity type", pattern="^(ingredient|herb|gene|disease)$")
    keyword: str = Field(..., description="Search keyword, e.g. 'berberine' / 'astragalus' / 'TNF' / 'colitis'", min_length=1, max_length=100)
    page: int = Field(default=1, description="Page number", ge=1)
    per_page: int = Field(default=10, description="Items per page", ge=1, le=50)


class CtdGeneInput(BaseModel):
    '''CTD chemical-gene interaction report via public batch query.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    chemical: str = Field(..., description="Chemical name or MeSH ID, e.g. 'berberine'", min_length=2, max_length=100)
    gene: Optional[str] = Field(default=None, description="Gene symbol to filter, e.g. 'TNF'", max_length=50)
    tax: Optional[str] = Field(default=None, description="Taxon ID filter, e.g. '9606' (human) or '10090' (mouse). Default: all species", max_length=10)
    limit: int = Field(default=10, description="Max rows", ge=1, le=50)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class UniProtInput(BaseModel):
    '''UniProt entry query by accession (e.g. P01308) or gene symbol search.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    accession: Optional[str] = Field(default=None, description="UniProt accession, e.g. 'P01308' (insulin)", max_length=20)
    gene: Optional[str] = Field(default=None, description="Gene symbol to search, e.g. 'TNF' (uses search API)", max_length=50)
    organism: str = Field(default="human", description="Organism filter for gene search", max_length=50)
    limit: int = Field(default=5, description="Max search results", ge=1, le=20)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class NcbiGeneInput(BaseModel):
    '''NCBI Gene database search via EUtils.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    term: str = Field(..., description="Search term, e.g. 'TNF[Gene Name] AND human[orgn]' or 'TP53'", min_length=1, max_length=300)
    limit: int = Field(default=10, description="Max results", ge=1, le=50)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class EnrichmentInput(BaseModel):
    '''Functional enrichment analysis (g:Profiler gost, human default).'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    genes: List[str] = Field(..., description="Gene symbols to enrich, e.g. ['TNF','IL6','PTGS2']", min_length=2, max_length=500)
    sources: List[str] = Field(default=["GO:BP", "KEGG", "REAC"],
                               description="Data sources: GO:MF GO:CC GO:BP KEGG REAC TF HP CORUM MIRNA")
    user_threshold: float = Field(default=0.05, description="Significance threshold", ge=0.001, le=1.0)
    limit: int = Field(default=15, description="Max enriched terms to return", ge=1, le=100)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class NpassSearchInput(BaseModel):
    '''NPASS natural product search by compound name (recovered availability 2026-09).'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    compound_name: str = Field(..., description="Compound name, e.g. 'quercetin'", min_length=2, max_length=100)
    limit: int = Field(default=10, description="Max results", ge=1, le=30)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class StructureInput(BaseModel):
    '''Protein structure lookup: PDB entry or AlphaFold prediction.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    pdb_id: Optional[str] = Field(default=None, description="PDB entry ID, e.g. '4HHB'", max_length=10)
    uniprot: Optional[str] = Field(default=None, description="UniProt accession for AlphaFold prediction, e.g. 'P01308'", max_length=20)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class DgidbInput(BaseModel):
    '''Query DGIdb (Drug-Gene Interaction Database) for gene-drug interactions.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    genes: List[str] = Field(..., description="Gene symbols, e.g. ['BRCA1','BRCA2'] or ['TNF','IL6']", min_length=1, max_length=20)
    limit_per_gene: int = Field(default=10, description="Max drugs per gene", ge=1, le=50)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class GwasInput(BaseModel):
    '''Query GWAS Catalog (EBI) for variant/gene associations by disease keyword or gene.'''
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    keyword: Optional[str] = Field(default=None, description="Disease/trait keyword, e.g. 'ulcerative colitis' or 'ovarian cancer'", max_length=200)
    gene: Optional[str] = Field(default=None, description="Gene symbol, e.g. 'BRCA1' or 'IL23R'", max_length=50)
    limit: int = Field(default=10, description="Max results", ge=1, le=50)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class DbStatusInput(BaseModel):
    """Check availability of supported databases (cached probe results)."""
    model_config = ConfigDict(extra="forbid")

    refresh: bool = Field(default=False, description="True to re-probe live endpoints (slower); False to return cached 2026-09 results")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class AuditEvidenceInput(BaseModel):
    """Audit the evidence chain of a completed workflow run (local artifacts dir)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    artifacts_dir: str = Field(..., description="Local path to one run's artifacts directory (contains evidence_log.json and *临床锚定评分.xlsx)", min_length=1, max_length=500)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


# ================= 共享工具函数 =================

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


async def _make_api_request_core(url: str, method: str = "GET", *,
                            headers: Dict = None, params: Dict = None,
                            data: Any = None, json_body: Any = None,
                            timeout: float = DEFAULT_TIMEOUT,
                            max_retries: int = 2,
                            respect_pubchem_limit: bool = False,
                            use_circuit_breaker: bool = False) -> httpx.Response:
    '''Shared async HTTP request with retry on 5xx/timeouts.

    PUG-REST policy (respect_pubchem_limit=True): 1s min interval between calls,
    4 retries with 5s*attempt backoff on 500/502/503.

    Circuit breaker (use_circuit_breaker=True, for flaky upstreams like
    TCMBank): per-host consecutive-failure tracking; when the threshold is
    reached the host is "opened" (requests rejected immediately) for a
    cooldown window, after which one probe request is allowed (half-open).
    Success closes the breaker; failure re-opens it.
    '''
    global _last_pubchem_ts
    hdrs = dict(HTTP_UA)
    if headers:
        hdrs.update(headers)

    host = httpx.URL(url).host
    if use_circuit_breaker:
        blocked = _circuit_check(host)
        if blocked:
            raise httpx.ConnectError(blocked)

    last_exc = None
    ok = False
    try:
        for attempt in range(max_retries + 1):
            if respect_pubchem_limit:
                wait = PUBCHEM_MIN_INTERVAL - (time.monotonic() - _last_pubchem_ts)
                if wait > 0:
                    await asyncio.sleep(wait)
                _last_pubchem_ts = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    resp = await client.request(method, url, headers=hdrs,
                                                params=params, data=data, json=json_body)
                    if resp.status_code >= 500 and attempt < max_retries:
                        await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                        continue
                    ok = resp.status_code < 500
                    return resp
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exc = e
                if attempt < max_retries:
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
        raise last_exc or httpx.ConnectError(f"request failed: {url}")
    finally:
        if use_circuit_breaker:
            _circuit_record(host, ok)


# ================= 运行时自迭代（2026-10-12：飞行记录仪+分层洞察库） =================
# 机制参考：AgentLens（全调用捕获）/ Self-Improving（错误-知识分层沉淀）/
# Hermes（经验→技能资产化）/ TeleAgent 技能自进化（分析结果→建议更新→平台确认流）
# 遥测仅存本地状态目录（默认 ~/.tcm_mcp_state，可用 TCM_MCP_STATE_DIR 覆盖），
# 绝不上传；单事件写入失败静默跳过，绝不影响工具本身。

from pathlib import Path as _Path

STATE_DIR = _Path(os.environ.get("TCM_MCP_STATE_DIR", str(_Path.home() / ".tcm_mcp_state")))
CALL_LOG_PATH = STATE_DIR / "call_log.jsonl"
INSIGHTS_PATH = STATE_DIR / "insights.json"
_INSIGHT_CATS = ("dead_end", "endpoint_change", "quirk", "performance",
                 "new_channel", "methodology")
_MAX_LOG_BYTES = 2_000_000
_insights_lock = asyncio.Lock()


def _state_init():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _log_event(rec: Dict):
    """Append one telemetry event; rotate log at 2MB; never raise."""
    try:
        _state_init()
        if CALL_LOG_PATH.exists() and CALL_LOG_PATH.stat().st_size > _MAX_LOG_BYTES:
            rot = CALL_LOG_PATH.with_suffix(".jsonl.1")
            try:
                rot.unlink()
            except Exception:
                pass
            CALL_LOG_PATH.replace(rot)
        with open(CALL_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _load_insights() -> Dict:
    try:
        if INSIGHTS_PATH.exists():
            with open(INSIGHTS_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"version": 1, "insights": []}


def _save_insights(data: Dict):
    _state_init()
    tmp = str(INSIGHTS_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, INSIGHTS_PATH)


async def _make_api_request(url: str, method: str = "GET", *,
                            headers: Dict = None, params: Dict = None,
                            data: Any = None, json_body: Any = None,
                            timeout: float = DEFAULT_TIMEOUT,
                            max_retries: int = 2,
                            respect_pubchem_limit: bool = False,
                            use_circuit_breaker: bool = False) -> httpx.Response:
    """Telemetry wrapper around _make_api_request_core: records every external
    DB call (host, method, status/error, latency) to the local flight recorder
    so tcm_analyze_runtime can detect dead ends and latency regressions."""
    t0 = time.monotonic()
    host = httpx.URL(url).host
    try:
        resp = await _make_api_request_core(
            url, method, headers=headers, params=params, data=data,
            json_body=json_body, timeout=timeout, max_retries=max_retries,
            respect_pubchem_limit=respect_pubchem_limit,
            use_circuit_breaker=use_circuit_breaker)
        _log_event({"ts": _now_iso(), "type": "db_call", "host": host,
                    "method": method, "status": resp.status_code,
                    "ok": resp.status_code < 400,
                    "ms": round((time.monotonic() - t0) * 1000)})
        return resp
    except Exception as e:
        # consult recorded insights for a hint about this host
        hint = ""
        try:
            host_l = (host or "").lower()
            for ins in _load_insights().get("insights", []):
                blob = (ins.get("title", "") + " " + ins.get("detail", "")).lower()
                if host_l and host_l in blob:
                    hint = f" | insight {ins['id']} [{ins['category']}]: {ins['title']}"
                    break
        except Exception:
            hint = ""
        _log_event({"ts": _now_iso(), "type": "db_call", "host": host,
                    "method": method,
                    "error": type(e).__name__ + hint,
                    "ok": False,
                    "ms": round((time.monotonic() - t0) * 1000)})
        raise


def _handle_api_error(e: Exception) -> str:
    '''Consistent, actionable error formatting across all tools.'''
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 404:
            return "Error: Resource not found. Check the ID/name (e.g. KEGG compound IDs must be uppercase like C00757)."
        if code == 400:
            return f"Error: Bad request (400). Check parameter formats. Server said: {e.response.text[:150]}"
        if code == 429:
            return "Error: Rate limit exceeded. Wait before retrying."
        return f"Error: API request failed with status {code}. The database may be temporarily down."
    if isinstance(e, httpx.TimeoutException):
        return ("Error: Request timed out. The database may be slow; retry, or "
                "use tcm_check_database_status to verify availability. "
                "Run tcm_analyze_runtime to check recorded channel health.")
    if isinstance(e, httpx.ConnectError):
        return ("Error: Connection failed. The database may be down or blocked "
                "by network policy. Use tcm_check_database_status for "
                "alternatives; tcm_analyze_runtime reports accumulated health.")
    return f"Error: Unexpected error: {type(e).__name__}: {str(e)[:200]}"


def _fmt(resp_data: Dict, fmt: ResponseFormat, title: str) -> str:
    '''Render dict as markdown or JSON.'''
    if fmt == ResponseFormat.JSON:
        return json.dumps(resp_data, ensure_ascii=False, indent=2, default=str)
    lines = [f"# {title}", ""]
    for k, v in resp_data.items():
        if isinstance(v, list):
            lines.append(f"## {k} ({len(v)})")
            for item in v:
                lines.append(f"- {json.dumps(item, ensure_ascii=False, default=str)[:300]}")
        else:
            lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


def _valid_cas(s: str) -> bool:
    '''CAS registry number check-digit validation (e.g. 77-81-6).'''
    import re
    m = re.match(r"^(\d{2,7})-(\d{2})-(\d)$", s)
    if not m:
        return False
    digits = m.group(1) + m.group(2)
    total = sum((i + 1) * int(d) for i, d in enumerate(reversed(digits)))
    return total % 10 == int(m.group(3))


# ================= 工具定义（17 个） =================

@mcp.tool(
    name="tcm_pubchem_lookup",
    annotations={"title": "PubChem Compound Lookup",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_pubchem_lookup(params: PubChemLookupInput) -> str:
    '''Look up a chemical compound in PubChem by name or CID.

    Returns canonical SMILES, molecular formula, molecular weight, InChIKey, and
    (from the synonyms endpoint) CAS registry numbers when available. Enforces
    PUG-REST rate limiting (1 req/s, 4 retries, 5xx backoff).

    Args:
        params (PubChemLookupInput): exactly one of:
            - name (str): compound name, e.g. "berberine"
            - cid (int): PubChem CID, e.g. 2244
            - response_format: "markdown" (default) or "json"

    Returns:
        str: compound properties. Success (markdown): fields including
        CID, Title, SMILES, Formula, MW, InChIKey, CAS (if found).
        Error: "Error: <message>" — 404 means no such compound.

    Examples:
        - "Find the SMILES and InChIKey of berberine" -> name="berberine"
        - "What is the CAS number of PubChem CID 2244?" -> cid=2244
    '''
    if not params.has_query:
        return "Error: Provide either 'name' or 'cid' (one is required)."
    if params.name and params.cid:
        return "Error: Provide only ONE of 'name' or 'cid', not both."
    try:
        if params.cid:
            cid = params.cid
        else:
            r = await _make_api_request(
                "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                f"{params.name}/cids/JSON",
                respect_pubchem_limit=True)
            if r.status_code == 404:
                return f"No compound found for name '{params.name}' in PubChem."
            r.raise_for_status()
            cid = r.json()["IdentifierList"]["CID"][0]

        r = await _make_api_request(
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/"
            f"{cid}/property/MolecularFormula,MolecularWeight,CanonicalSMILES,"
            "SMILES,ConnectivitySMILES,InChIKey,Title/JSON",
            respect_pubchem_limit=True)
        if r.status_code == 404:
            return f"Error: CID {cid} not found in PubChem."
        r.raise_for_status()
        props = r.json()["PropertyTable"]["Properties"][0]

        # synonyms 提取 CAS（既往实战：synonyms 端点可提取带校验位的 CAS）
        cas_numbers = []
        try:
            rs = await _make_api_request(
                "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/"
                f"{cid}/synonyms/JSON",
                respect_pubchem_limit=True)
            if rs.status_code == 200:
                syns = rs.json().get("InformationList", {}).get("Information", [])
                if syns:
                    for s in syns[0].get("Synonym", []):
                        if _valid_cas(s):
                            cas_numbers.append(s)
                            break
        except Exception:
            pass

        # 带电离子（如 berberine 阳离子）不返回 CanonicalSMILES，回退到 SMILES/ConnectivitySMILES
        smiles = (props.get("CanonicalSMILES") or props.get("SMILES")
                  or props.get("ConnectivitySMILES") or "")

        result = {
            "CID": cid, "Title": props.get("Title", ""),
            "MolecularFormula": props.get("MolecularFormula", ""),
            "MolecularWeight": props.get("MolecularWeight", ""),
            "CanonicalSMILES": smiles,
            "InChIKey": props.get("InChIKey", ""),
            "CAS": cas_numbers[0] if cas_numbers else "not in PubChem synonyms",
            "source": "PubChem PUG-REST", "queried_at": _now_iso()}
        return _fmt(result, params.response_format, f"PubChem: {result['Title'] or cid}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_kegg_compound",
    annotations={"title": "KEGG Compound Query",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_kegg_compound(params: KeggCompoundInput) -> str:
    '''Query a KEGG compound entry (formula, pathways, related enzymes).

    Accepts either a KEGG compound ID (MUST be uppercase, e.g. "C00757" is
    berberine chloride; lowercase fails with 404) or a compound name
    (resolved via the find endpoint, e.g. "berberine").

    Args:
        params (KeggCompoundInput):
            - query (str): "C00757" or "berberine"
            - response_format: "markdown" (default) or "json"

    Returns:
        str: KEGG entry text (formula, mass, pathways, enzymes). The raw entry
        is returned as text because KEGG REST returns text/plain.

    Examples:
        - "Get the KEGG entry for berberine" -> query="berberine" or "C00757"
        - "Which KEGG pathways involve C00257?" (omega-3) -> query="C00257"
    '''
    try:
        q = params.query
        if q[:1].upper() in ("C", "D") and q[1:].isdigit():
            entry_id = q.upper()
        else:
            r = await _make_api_request(
                f"https://rest.kegg.jp/find/compound/{q}")
            r.raise_for_status()
            lines = [ln for ln in r.text.strip().split("\n") if ln]
            if not lines:
                return f"No KEGG compound found for '{q}'."
            entry_id = lines[0].split("\t")[0].split(":")[-1]

        r = await _make_api_request(f"https://rest.kegg.jp/get/{entry_id}")
        if r.status_code == 404:
            return f"Error: KEGG entry {entry_id} not found (IDs are case-sensitive: use {entry_id.upper()})."
        r.raise_for_status()
        return _fmt({"entry_id": entry_id, "entry_text": r.text[:3000],
                     "source": "KEGG REST", "queried_at": _now_iso()},
                    params.response_format, f"KEGG Compound: {entry_id}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_chembl_lookup",
    annotations={"title": "ChEMBL Compound & Mechanism",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_chembl_lookup(params: ChEMBLInput) -> str:
    '''Look up a compound in ChEMBL and return its bioactivity mechanism targets.

    2026-09-22 实测恢复（此前持续 500）：molecule / search / mechanism 三个
    业务端点全部可用。机制接口直接给出化合物→蛋白靶点映射（action_type、
    mechanism_of_action），是成分到靶点的高价值独立通道，可交叉验证
    Open Targets / DGIdb 结果。

    Args:
        params (ChEMBLInput):
            - query (str): compound name ("berberine") or ChEMBL ID ("CHEMBL251414")
            - response_format: "markdown" (default) or "json"

    Returns:
        str: compound properties (SMILES / InChIKey / formula / ATC) plus up to
        10 mechanism-based targets (target name, action type, mechanism of action).

    Examples:
        - "What targets does berberine hit in ChEMBL?" -> query="berberine"
        - "Mechanism of aspirin" -> query="CHEMBL25"
    '''
    try:
        q = params.query.strip()
        # 1) 解析 ChEMBL ID 或按名称搜索
        if re.fullmatch(r"CHEMBL\d+", q, re.I):
            chembl_id = q.upper()
        else:
            r = await _make_api_request(
                "https://www.ebi.ac.uk/chembl/api/data/molecule/search.json?q="
                + _urlencode(q))
            r.raise_for_status()
            hits = r.json().get("molecules", [])
            if not hits:
                return f"No ChEMBL molecule found for '{q}'."
            chembl_id = hits[0].get("molecule_chembl_id")
        # 2) 分子详情（结构 + 理化属性）
        rm = await _make_api_request(
            f"https://www.ebi.ac.uk/chembl/api/data/molecule/{chembl_id}.json")
        if rm.status_code == 404:
            return f"Error: ChEMBL ID {chembl_id} not found."
        rm.raise_for_status()
        mol = rm.json()
        props = mol.get("molecule_properties", {}) or {}
        structs = mol.get("molecule_structures", {}) or {}
        # 3) 机制靶点（化合物→靶点）；名称搜索时部分条目无机制，
        #    回退探测前 5 个命中条目，取第一个有机制记录的
        mechanisms = []
        ids_tried = [chembl_id]
        rk = await _make_api_request(
            f"https://www.ebi.ac.uk/chembl/api/data/mechanism.json?molecule_chembl_id={chembl_id}")
        if rk.status_code == 200:
            mechanisms = rk.json().get("mechanisms", [])
        if not mechanisms and not re.fullmatch(r"CHEMBL\d+", q, re.I):
            for alt in hits[1:6]:
                alt_id = alt.get("molecule_chembl_id")
                if not alt_id or alt_id in ids_tried:
                    continue
                ids_tried.append(alt_id)
                try:
                    ra = await _make_api_request(
                        f"https://www.ebi.ac.uk/chembl/api/data/mechanism.json?molecule_chembl_id={alt_id}")
                    if ra.status_code == 200:
                        alt_m = ra.json().get("mechanisms", [])
                        if alt_m:
                            mechanisms = alt_m
                            chembl_id = alt_id
                            # 用有机制的条目更新分子详情
                            rm2 = await _make_api_request(
                                f"https://www.ebi.ac.uk/chembl/api/data/molecule/{alt_id}.json")
                            if rm2.status_code == 200:
                                mol = rm2.json()
                                props = mol.get("molecule_properties", {}) or {}
                                structs = mol.get("molecule_structures", {}) or {}
                            break
                except Exception:
                    continue
        target_rows = []
        for mm in mechanisms[:10]:
            target_rows.append({
                "target_chembl_id": mm.get("target_chembl_id"),
                "target_name": mm.get("target_name"),
                "action_type": mm.get("action_type"),
                "mechanism": (mm.get("mechanism_of_action") or "")[:80],
                "target_components": [c.get("component_id") for c in (mm.get("target_components") or [])],
            })
        result = {
            "ChEMBL_ID": chembl_id,
            "pref_name": mol.get("pref_name") or mol.get("molecule_name", ""),
            "formula": props.get("full_molformula") or mol.get("full_molformula", ""),
            "SMILES": structs.get("canonical_smiles") or "",
            "InChIKey": structs.get("standard_inchi_key") or "",
            "MW": props.get("full_mwt"),
            "ATC": [a for a in mol.get("atc_classifications", [])][:5],
            "targets": target_rows,
            "source": "ChEMBL API", "queried_at": _now_iso()}
        return _fmt(result, params.response_format,
                    f"ChEMBL: {result['pref_name'] or chembl_id} ({len(target_rows)} targets)")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_ensembl_lookup",
    annotations={"title": "Ensembl Gene Lookup",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_ensembl_lookup(params: EnsemblLookupInput) -> str:
    '''Look up a human gene via Ensembl REST (symbol -> gene ID / biotype / description).

    2026-09-22 恢复（此前 3x 超时 0 bytes）：lookup/symbol 与 xrefs/id 端点实测可用
    （gene ~5.4s，xrefs ~18s，偶发超时需重试）。用于基因符号规范化与
    UniProt/NCBI 交叉验证。

    Args:
        params (EnsemblLookupInput):
            - gene (str): gene symbol ("TNF") or Ensembl ID ("ENSG00000232810")
            - response_format: "markdown" (default) or "json"

    Returns:
        str: gene id, symbol, biotype, description, source, version, xrefs count.

    Examples:
        - "Normalize gene symbol TNF to Ensembl ID" -> gene="TNF"
        - "What is ENSG00000232810?" -> gene="ENSG00000232810"
    '''
    try:
        g = params.gene.strip()
        if g.upper().startswith("ENSG"):
            url = (f"https://rest.ensembl.org/lookup/id/{g}?"
                   "content-type=application/json")
        else:
            url = (f"https://rest.ensembl.org/lookup/symbol/homo_sapiens/{g}?"
                   "content-type=application/json")
        r = await _make_api_request(url, timeout=45, max_retries=1)
        if r.status_code in (404, 400):
            return f"Error: gene '{g}' not found in Ensembl (or symbol is not in human genome)."
        r.raise_for_status()
        j = r.json()
        result = {
            "id": j.get("id"), "display_name": j.get("display_name"),
            "biotype": j.get("biotype"),
            "description": (j.get("description") or "").strip(),
            "source": j.get("source"),
            "assembly": j.get("assembly_name"),
            "chr": j.get("seq_region_name"), "strand": j.get("strand"),
            "version": j.get("version"),
            "xrefs": None,
            "source": "Ensembl REST", "queried_at": _now_iso()}
        # 附带 xrefs 统计（同 ID 查询时合并）
        if j.get("id"):
            try:
                rx = await _make_api_request(
                    f"https://rest.ensembl.org/xrefs/id/{j['id']}?content-type=application/json",
                    timeout=45, max_retries=1)
                if rx.status_code == 200:
                    x = rx.json()
                    result["xrefs"] = len(x)
            except Exception:
                pass
        return _fmt(result, params.response_format,
                    f"Ensembl: {result['display_name'] or g}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_search_disease_targets",
    annotations={"title": "Open Targets Disease/Target Search",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_search_disease_targets(params: OpenTargetsInput) -> str:
    '''Query Open Targets Platform for disease-associated targets.

    Three modes (exactly one of the three query fields):
      1. disease_name: search diseases, returns MONDO IDs + descriptions.
         Useful first step to resolve a disease name to its MONDO ID.
      2. mondo_id: top associated targets for that disease (overall association
         score, sorted). Example: MONDO_0005101 is ulcerative colitis.
         CAUTION: MONDO IDs are unforgiving — MONDO_0004989 is breast cancer.
      3. ensembl_id: target info (approved symbol, associated diseases count).

    Args:
        params (OpenTargetsInput): one of disease_name / mondo_id / ensembl_id,
            plus limit (default 10, max 50) and response_format.

    Returns:
        str: mode 1: list of diseases (id, name, description, score);
             mode 2: list of targets (symbol, name, overall score);
             mode 3: target details.

    Examples:
        - "What is the MONDO ID of rheumatoid arthritis?" -> disease_name="rheumatoid arthritis"
        - "Top 10 genetic targets for ulcerative colitis" -> mondo_id="MONDO_0005101", limit=10
        - "Info about STAT3 as a drug target" -> ensembl_id="ENSG00000168610"
    '''
    n_q = sum(bool(x) for x in (params.disease_name, params.mondo_id, params.ensembl_id))
    if n_q != 1:
        return "Error: Provide exactly ONE of disease_name / mondo_id / ensembl_id."
    try:
        if params.disease_name:
            gql = {"query": """
                query($q: String!) { search(queryString: $q, entityNames: ["disease"]) {
                  hits { id name entity description score } } }""",
                   "variables": {"q": params.disease_name}}
        elif params.mondo_id:
            gql = {"query": """
                query($id: String!, $size: Int!) {
                  disease(efoId: $id) { id name associatedTargets(page: {size: $size, index: 0}) {
                    count rows { target { approvedSymbol } score } } } }""",
                   "variables": {"id": params.mondo_id, "size": params.limit}}
        else:
            # 2026-09 实测：Open Targets GraphQL v4 的 Target 类型已移除 'name' 与
            # 'knownDrugs' 字段（原查询 400）；introspection 确认可用字段为
            # approvedSymbol / approvedName / biotype / associatedDiseases / dbXrefs
            gql = {"query": """
                query($id: String!, $size: Int!) { target(ensemblId: $id) {
                  approvedSymbol approvedName biotype
                  associatedDiseases(page: {size: $size, index: 0}) {
                    count rows { disease { id name } score } }
                  dbXrefs { source } } }""",
                   "variables": {"id": params.ensembl_id, "size": params.limit}}

        r = await _make_api_request(
            "https://api.platform.opentargets.org/api/v4/graphql",
            method="POST", json_body=gql, max_retries=1)
        r.raise_for_status()
        data = r.json().get("data", {})

        if params.disease_name:
            hits = data.get("search", {}).get("hits", [])
            items = [{"id": h["id"], "name": h["name"],
                      "description": (h.get("description") or "")[:120],
                      "score": h.get("score")} for h in hits[:params.limit]]
            return _fmt({"query": params.disease_name, "diseases": items,
                         "source": "Open Targets GraphQL", "queried_at": _now_iso()},
                        params.response_format, f"Open Targets diseases: {params.disease_name}")
        elif params.mondo_id:
            dis = data.get("disease")
            if not dis:
                return f"Error: Disease {params.mondo_id} not found in Open Targets. Verify the MONDO ID (e.g. search by disease_name first)."
            at = dis.get("associatedTargets", {})
            items = [{"symbol": row["target"]["approvedSymbol"],
                      "score": round(row.get("score", 0), 3)}
                     for row in at.get("rows", [])[:params.limit]]
            return _fmt({"disease": dis["name"], "mondo": params.mondo_id,
                         "total_associated_targets": at.get("count"),
                         "top_targets": items,
                         "source": "Open Targets GraphQL", "queried_at": _now_iso()},
                        params.response_format, f"Open Targets: {dis['name']}")
        else:
            t = data.get("target")
            if not t:
                return f"Error: Target {params.ensembl_id} not found. Example valid ID: ENSG00000168610 (STAT3)."
            ad = t.get("associatedDiseases", {})
            xrefs = [x.get("source") for x in (t.get("dbXrefs") or []) if x.get("source")][:8]
            return _fmt({"approvedSymbol": t.get("approvedSymbol"),
                         "approvedName": t.get("approvedName"),
                         "biotype": t.get("biotype"),
                         "dbXrefs": xrefs,
                         "associated_disease_count": ad.get("count"),
                         "top_associated_diseases": [
                             {"id": row["disease"]["id"],
                              "name": row["disease"]["name"],
                              "score": round(row.get("score", 0), 3)}
                             for row in ad.get("rows", [])[:params.limit]],
                         "source": "Open Targets GraphQL", "queried_at": _now_iso()},
                        params.response_format, f"Open Targets: {t.get('approvedSymbol')}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_string_ppi",
    annotations={"title": "STRING PPI Network",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_string_ppi(params: StringPpiInput) -> str:
    '''Query the STRING protein-protein interaction network for a gene list.

    Returns interaction pairs with combined_score (0-1000), plus the degree
    (connectivity) of each protein in the result — useful for hub gene
    identification in network pharmacology workflows.

    Args:
        params (StringPpiInput):
            - genes (List[str]): 1-50 gene symbols, e.g. ["TNF","IL6","PTGS2"]
            - species (str): NCBI taxon ID, default "9606" (human)
            - response_format

    Returns:
        str: interaction pairs (combined_score >= 0.4 medium confidence kept
        for brevity) and per-protein degrees sorted descending.

    Examples:
        - "PPI network for TNF, IL6, IL1B, PTGS2, NFKB1" -> genes=[...]
        - "Which of these genes is the hub?" -> same call; hub = highest degree
    '''
    try:
        # STRING 官方分隔符：回车（URL 编码后为 %0d），httpx 会自动正确编码
        identifiers = "\r".join(params.genes)
        r = await _make_api_request(
            "https://version-12-0.string-db.org/api/tsv/network",
            params={"identifiers": identifiers, "species": params.species})
        r.raise_for_status()
        rows = [ln.split("\t") for ln in r.text.strip().split("\n") if ln]
        if len(rows) < 2:
            return f"No STRING interactions found for {params.genes}."
        header = rows[0]
        pairs, degrees = [], {}
        for row in rows[1:]:
            d = dict(zip(header, row))
            a, b = d.get("preferredName_A", ""), d.get("preferredName_B", "")
            score = float(d.get("score", 0))
            # STRING tsv 返回 0-1 小数；旧接口归一化为 0-1000，兼容两种
            threshold = 0.4 if score <= 1.0 else 400
            if score >= threshold:
                pairs.append({"A": a, "B": b, "score": round(score, 3)})
            degrees[a] = degrees.get(a, 0) + 1
            degrees[b] = degrees.get(b, 0) + 1
        hubs = sorted(degrees.items(), key=lambda x: -x[1])[:10]
        return _fmt({"input_genes": params.genes, "species": params.species,
                     "interactions_medium_plus": len(pairs),
                     "top_pairs": pairs[:25],
                     "hub_ranking": [{"gene": g, "degree": d} for g, d in hubs],
                     "source": "STRING v12", "queried_at": _now_iso()},
                    params.response_format, f"STRING PPI ({len(params.genes)} genes)")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_tcmbank_search",
    annotations={"title": "TCMBank Search",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_tcmbank_search(params: TcmBankInput) -> str:
    '''Search TCMBank (61,965 TCM ingredients/herbs/targets/diseases) across four entity types.

    NOTE: TCMBank is HTTP-only (https fails). This tool handles that internally.

    Args:
        params (TcmBankInput):
            - entity (str): "ingredient" | "herb" | "gene" | "disease"
            - keyword (str): e.g. "berberine" / "astragalus" / "TNF" / "colitis"
            - page, per_page: pagination (per_page max 50)

    Returns:
        str: total count + item list (JSON format; fields vary by entity).

    Examples:
        - "Find TCM ingredients named berberine" -> entity="ingredient", keyword="berberine"
        - "Which herbs match astragalus?" -> entity="herb", keyword="astragalus"
        - "Diseases related to colitis in TCMBank" -> entity="disease", keyword="colitis"
    '''
    try:
        r = await _make_api_request(
            f"http://tcmbank.cn/api/{params.entity}/search",
            params={f"{params.entity}_name": params.keyword,
                    "cur_page": params.page, "per_page": params.per_page},
            use_circuit_breaker=True)
        r.raise_for_status()
        d = r.json().get("data", {})
        total = (d.get("total_ingredients") or d.get("total_herbs")
                 or d.get("total_genes") or d.get("total_diseases") or 0)
        items = d.get("list", [])[:params.per_page]
        return _fmt({"entity": params.entity, "keyword": params.keyword,
                     "total": total, "page": params.page, "items": items,
                     "source": "TCMBank (http)", "queried_at": _now_iso()},
                    ResponseFormat.JSON, f"TCMBank {params.entity}: {params.keyword}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_ctd_gene_interactions",
    annotations={"title": "CTD Chemical-Gene Interactions",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_ctd_gene_interactions(params: CtdGeneInput) -> str:
    '''Query CTD (Comparative Toxicogenomics Database) chemical-gene interactions.

    Returns curated interactions with InteractionActions (e.g. "increases^expression")
    and PubMed reference counts — a key evidence source for compound→target mapping.
    Note: this uses the lightweight batch query; for genome-scale batch use the
    monthly .tsv.gz bulk downloads (see skill references).

        Args:
        params (CtdGeneInput):
            - chemical (str): chemical name, e.g. "berberine"
            - gene (str, optional): filter to one gene, e.g. "TNF"
            - tax (str, optional): NCBI taxon ID filter, e.g. "9606" human
            - limit (int): max rows (default 10)
            - limit (int): max rows (default 10)
            - response_format

    Returns:
        str: curated interaction rows: ChemicalName, GeneSymbol, Organism,
        GeneId, PubMed IDs / count.

    Examples:
        - "Which genes does berberine interact with?" -> chemical="berberine"
        - "Does berberine affect TNF in humans?" -> chemical="berberine", gene="TNF", tax="9606"
    '''
    try:
        url = "https://ctdbase.org/tools/batchQuery.go"
        payload = {"inputType": "chem", "inputTerms": params.chemical,
                   "report": "genes_curated", "format": "json",
                   "action": "", "or": "", "tax": params.tax or "", "processLevel": ""}
        r = await _make_api_request(url, method="POST", data=payload, timeout=45)
        # 2026-09-22 实测：batchQuery 偶发被 ALTCHA/反爬拦截（302/HTML 验证页）。
        # 拦截时给出明确的替代通道指引，而不是模糊的 HTTP 错误。
        ctype = (r.headers.get("content-type") or "")
        if r.status_code == 302 or (r.status_code == 200 and "html" in ctype.lower() and "altcha" in r.text.lower()):
            return ("Error: CTD batchQuery blocked by anti-bot (ALTCHA) check. "
                    "Use DGIdb instead: tcm_dgidb_interactions(chemical=...), "
                    "or ChEMBL: tcm_chembl_lookup(query=...), or CTD monthly .tsv.gz bulk downloads "
                    "(see skill references).")
        r.raise_for_status()
        try:
            body = r.json()
        except Exception:
            return f"Error: CTD returned non-JSON response (possible rate limit or CAPTCHA). Use tcm_dgidb_interactions as the live alternative, or bulk .tsv.gz downloads."
        rows = body if isinstance(body, list) else body.get("rows", [])
        if params.gene:
            rows = [x for x in rows if x.get("GeneSymbol", "").upper() == params.gene.upper()]
        items = [{"chemical": x.get("ChemicalName"), "gene": x.get("GeneSymbol"),
                  "organism": x.get("Organism"),
                  "gene_id": x.get("GeneId"),
                  "pubmed_ids": x.get("PubMedIds", ""),
                  "pubmed_count": len([p for p in x.get("PubMedIds", "").replace("|", ",").split(",") if p.strip()])}
                 for x in rows[:params.limit]]
        if not items:
            return f"No CTD curated interactions found for '{params.chemical}'" + (f" x {params.gene}" if params.gene else "")
        return _fmt({"chemical": params.chemical, "rows_returned": len(items),
                     "interactions": items,
                     "source": "CTD batchQuery", "queried_at": _now_iso()},
                    params.response_format, f"CTD: {params.chemical}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_get_gene_info",
    annotations={"title": "UniProt Gene/Protein Info",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_get_gene_info(params: UniProtInput) -> str:
    '''Get protein/gene annotation from UniProt by accession or gene symbol.

    Mode 1: accession (e.g. "P01308") — full entry: protein name, function,
    GO terms, PDB cross-references.
    Mode 2: gene (e.g. "TNF") — search reviewed human entries by gene symbol.

    Args:
        params (UniProtInput): one of accession / gene; organism default "human";
            limit for search mode; response_format.

    Returns:
        str: accession, protein name, gene, function summary, links.

    Examples:
        - "Function of insulin protein" -> accession="P01308"
        - "UniProt entry for human TNF gene" -> gene="TNF"
    '''
    if bool(params.accession) == bool(params.gene):
        return "Error: Provide exactly ONE of 'accession' or 'gene'."
    try:
        if params.accession:
            r = await _make_api_request(
                f"https://rest.uniprot.org/uniprotkb/{params.accession}.json")
            if r.status_code == 404:
                return f"Error: UniProt accession {params.accession} not found."
            r.raise_for_status()
            e = r.json()
            func = ""
            for c in e.get("comments", []):
                if c.get("commentType") == "FUNCTION":
                    func = c.get("texts", [{}])[0].get("value", "")[:400]
                    break
            xref_pdb = [x["id"] for x in e.get("uniProtKBCrossReferences", [])
                        if x.get("database") == "PDB"][:5]
            return _fmt({"accession": e.get("primaryAccession"),
                         "protein": e.get("proteinDescription", {}).get(
                             "recommendedName", {}).get("fullName", {}).get("value", ""),
                         "gene": [g.get("geneName", {}).get("value")
                                  for g in e.get("genes", [])],
                         "organism": e.get("organism", {}).get("scientificName", ""),
                         "function": func, "pdb_structures": xref_pdb,
                         "source": "UniProt REST", "queried_at": _now_iso()},
                        params.response_format, f"UniProt: {params.accession}")
        else:
            r = await _make_api_request(
                "https://rest.uniprot.org/uniprotkb/search",
                params={"query": f'gene_exact:{params.gene} AND organism_name:{params.organism} AND reviewed:true',
                        "format": "json", "size": params.limit})
            r.raise_for_status()
            results = r.json().get("results", [])
            items = [{"accession": e.get("primaryAccession"),
                      "protein": e.get("proteinDescription", {}).get(
                          "recommendedName", {}).get("fullName", {}).get("value", ""),
                      "length": e.get("sequence", {}).get("length")}
                     for e in results]
            if not items:
                return f"No reviewed UniProt entries for gene '{params.gene}' in organism '{params.organism}'."
            return _fmt({"gene": params.gene, "entries": items,
                         "source": "UniProt REST", "queried_at": _now_iso()},
                        params.response_format, f"UniProt search: {params.gene}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_ncbi_gene_search",
    annotations={"title": "NCBI Gene Search",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_ncbi_gene_search(params: NcbiGeneInput) -> str:
    '''Search NCBI Gene database via EUtils (gene IDs, synonyms, map locations).

    Args:
        params (NcbiGeneInput):
            - term (str): search term, e.g. "TP53" or "TNF[Gene Name] AND human[orgn]"
            - limit (int): max results (default 10, max 50)
            - response_format

    Returns:
        str: gene ID, name, description, synonyms, map location per hit.

    Examples:
        - "NCBI Gene ID for human PTGS2" -> term="PTGS2 AND human[orgn]"
        - "Aliases of the IL6 gene" -> term="IL6[Gene Name] AND human[orgn]"
    '''
    try:
        r = await _make_api_request(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "gene", "term": params.term, "retmode": "json",
                    "retmax": params.limit})
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return f"No NCBI gene hits for '{params.term}'."
        r2 = await _make_api_request(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "gene", "id": ",".join(ids[:params.limit]),
                    "retmode": "json"})
        r2.raise_for_status()
        doc = r2.json().get("result", {})
        items = []
        for uid in doc.get("uids", []):
            g = doc.get(uid, {})
            items.append({"gene_id": g.get("uid"), "name": g.get("name"),
                          "description": g.get("description", "")[:100],
                          "synonyms": (g.get("otheraliases") or "")[:80],
                          "maplocation": g.get("maplocation", "")})
        return _fmt({"term": params.term, "hits": items,
                     "source": "NCBI EUtils", "queried_at": _now_iso()},
                    params.response_format, f"NCBI Gene: {params.term}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_enrichment_analysis",
    annotations={"title": "g:Profiler Enrichment",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_enrichment_analysis(params: EnrichmentInput) -> str:
    '''Run functional enrichment analysis on a gene list via g:Profiler (human).

    Sources: GO (MF/CC/BP), KEGG, Reactome (REAC), TRANSFAC (TF), Human
    Phenotype (HP), CORUM, miRNA. Returns enriched terms with p-values and
    matching-gene counts, sorted by adjusted p-value.

    Args:
        params (EnrichmentInput):
            - genes (List[str]): 2-500 gene symbols
            - sources (List[str]): default ["GO:BP","KEGG","REAC"]
            - user_threshold (float): default 0.05
            - limit (int): max terms (default 15)
            - response_format

    Returns:
        str: enriched terms: source, term name, p_adj, overlap size/term size.

    Examples:
        - "Enrich these UC targets: TNF, IL6, IL1B, PTGS2, NFKB1" -> genes=[...]
        - "KEGG and Reactome enrichment only" -> sources=["KEGG","REAC"]
    '''
    try:
        payload = {"organism": "hsapiens", "query": params.genes,
                   "sources": params.sources,
                   "user_threshold": params.user_threshold,
                   "significance_threshold_method": "g_SCS"}
        r = await _make_api_request(
            "https://biit.cs.ut.ee/gprofiler/api/gost/profile/",
            method="POST", json_body=payload, timeout=60, max_retries=1)
        r.raise_for_status()
        results = r.json().get("result", [])
        items = [{"source": x.get("source"), "term": x.get("name"),
                  "p_adj": x.get("p_value"),
                  "genes": f"{x.get('intersection_size')}/{x.get('term_size')}"}
                 for x in results[:params.limit]]
        if not items:
            return f"No significant enriched terms (threshold {params.user_threshold}) for the {len(params.genes)} input genes."
        return _fmt({"input_size": len(params.genes),
                     "sources": params.sources, "enriched_terms": items,
                     "source": "g:Profiler gost", "queried_at": _now_iso()},
                    params.response_format, f"Enrichment ({len(params.genes)} genes)")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_npass_search_compound",
    annotations={"title": "NPASS Natural Product Search",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_npass_search_compound(params: NpassSearchInput) -> str:
    '''Search NPASS (204k natural products) by compound name.

    Returns NPASS entries (NPC ID, name, MW, #source organisms, #targets,
    #activity records) parsed from the server-rendered search result table.
    NOTE: NPASS search now returns HTML (JSON endpoint retired); this tool
    parses the table directly. If it fails again, fall back to PubChem
    (tcm_pubchem_lookup).

    Args:
        params (NpassSearchInput):
            - compound_name (str): e.g. "quercetin"
            - limit (int): default 10
            - response_format

    Returns:
        str: NPASS compound entries (NPC ID, name, MW, #species, #targets).

    Examples:
        - "NPASS entries for quercetin" -> compound_name="quercetin"
    '''
    try:
        d = {"CompoundName": params.compound_name, "search_by_property": "SEARCH"}
        r = await _make_api_request(
            "https://bidd.group/NPASS/search_compound.php",
            method="POST", data=d, timeout=45)
        r.raise_for_status()
        # NPASS 搜索结果为 server-rendered HTML 表格（JSON 端点已下线）
        items = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
            m = re.search(r'compound\.php\?compoundID=(NPC\d+)', row)
            if not m:
                continue
            cells = [re.sub(r"<[^>]+>", "", c).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            # 表列：NPC ID / 名称 / MW / #来源物种 / #靶点 / #活性记录 / 定量可用
            items.append({
                "npc_id": m.group(1),
                "name": cells[1] if len(cells) > 1 else "",
                "molecular_weight": cells[2] if len(cells) > 2 else "",
                "n_source_organisms": cells[3] if len(cells) > 3 else "",
                "n_targets": cells[4] if len(cells) > 4 else "",
                "n_activity_records": cells[5] if len(cells) > 5 else ""})
            if len(items) >= params.limit:
                break
        if not items:
            return f"No NPASS entries for '{params.compound_name}'. (NPASS response format may have changed; try tcm_pubchem_lookup as fallback.)"
        return _fmt({"compound": params.compound_name, "entries": items,
                     "source": "NPASS", "queried_at": _now_iso()},
                    params.response_format, f"NPASS: {params.compound_name}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_get_structure",
    annotations={"title": "PDB / AlphaFold Structure Lookup",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_get_structure(params: StructureInput) -> str:
    '''Look up a protein structure: experimental PDB entry or AlphaFold prediction.

    Mode 1: pdb_id (e.g. "4HHB") — RCSB entry: title, method, resolution,
    polymer entities.
    Mode 2: uniprot accession (e.g. "P01308") — AlphaFold predicted structure:
    model URL, PAE URL, creation date.

    Args:
        params (StructureInput): exactly one of pdb_id / uniprot; response_format.

    Returns:
        str: structure metadata (download URLs included).

    Examples:
        - "Details of PDB 4HHB" -> pdb_id="4HHB"
        - "AlphaFold model for TNF protein P01375" -> uniprot="P01375"
    '''
    if bool(params.pdb_id) == bool(params.uniprot):
        return "Error: Provide exactly ONE of 'pdb_id' or 'uniprot'."
    try:
        if params.pdb_id:
            pid = params.pdb_id.upper()
            r = await _make_api_request(
                f"https://data.rcsb.org/rest/v1/core/entry/{pid}")
            if r.status_code == 404:
                return f"Error: PDB entry {pid} not found."
            r.raise_for_status()
            e = r.json()
            res = e.get("rcsb_entry_info", {}).get("resolution_combined", ["n/a"])
            return _fmt({"pdb_id": pid,
                         "title": e.get("struct", {}).get("title"),
                         "method": (e.get("exptl") or [{}])[0].get("method"),
                         "resolution": res[0] if res else "n/a",
                         "polymer_entities": e.get("rcsb_entry_info", {}).get(
                             "polymer_entity_count"),
                         "download": f"https://files.rcsb.org/download/{pid}.pdb",
                         "source": "RCSB PDB", "queried_at": _now_iso()},
                        params.response_format, f"PDB: {pid}")
        else:
            acc = params.uniprot.upper()
            r = await _make_api_request(
                f"https://alphafold.ebi.ac.uk/api/prediction/{acc}")
            if r.status_code == 404:
                return f"Error: No AlphaFold prediction for {acc} (it may be a fragment or non-standard entry)."
            r.raise_for_status()
            entries = r.json()
            if not entries:
                return f"Error: Empty AlphaFold response for {acc}."
            e = entries[0]
            return _fmt({"uniprot": acc,
                         "model": e.get("pdbUrl"),
                         "pae": e.get("paeUrl"),
                         "date_created": e.get("modelCreatedDate"),
                         "source": "AlphaFold DB", "queried_at": _now_iso()},
                        params.response_format, f"AlphaFold: {acc}")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_dgidb_interactions",
    annotations={"title": "DGIdb Drug-Gene Interactions",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_dgidb_interactions(params: DgidbInput) -> str:
    '''Query DGIdb (Drug-Gene Interaction Database) for drug-gene interactions.

    Useful to answer "which drugs/compounds act on these genes?" — e.g. for
    BRCA1/BRCA2 in ovarian cancer, or TNF/IL6 in UC. Uses the new GraphQL
    endpoint dgidb.org/api/graphql (old.dgidb.org is dead).

    Args:
        params (DgidbInput):
            - genes (List[str]): 1-20 gene symbols, e.g. ["BRCA1","BRCA2"]
            - limit_per_gene (int): max drugs per gene (default 10)
            - response_format

    Returns:
        str: per-gene drug interactions (drug name, conceptId, interaction
        types with directionality, publications count).

    Examples:
        - "DGIdb drug interactions for BRCA1 and BRCA2" -> genes=["BRCA1","BRCA2"]
        - "Which drugs target TNF?" -> genes=["TNF"]
    '''
    try:
        gql = {
            "query": """
            query($names: [String!]!) {
              genes(names: $names) {
                nodes { name interactions { drug { name conceptId }
                  interactionScore interactionTypes { type directionality }
                  publications { pmid } } } } }""",
            "variables": {"names": params.genes}}
        r = await _make_api_request(
            "https://dgidb.org/api/graphql", method="POST",
            json_body=gql, timeout=60, max_retries=1)
        r.raise_for_status()
        nodes = r.json().get("data", {}).get("genes", {}).get("nodes", [])
        items = []
        for node in nodes:
            gene = node.get("name", "")
            ints = node.get("interactions", [])[:params.limit_per_gene]
            drugs = [{
                "drug": i.get("drug", {}).get("name", ""),
                "concept_id": i.get("drug", {}).get("conceptId", ""),
                "interaction_score": i.get("interactionScore"),
                "interaction_types": [
                    f"{t.get('type')}{' (' + t.get('directionality') + ')' if t.get('directionality') else ''}"
                    for t in i.get("interactionTypes", [])],
                "n_publications": len(i.get("publications", [])),
                "pmids": [p.get("pmid") for p in i.get("publications", [])[:5]]}
                for i in ints]
            items.append({"gene": gene, "n_interactions": len(ints),
                          "drugs": drugs})
        if not items:
            return f"No DGIdb interactions found for {params.genes}."
        return _fmt({"genes": params.genes, "results": items,
                     "source": "DGIdb GraphQL", "queried_at": _now_iso()},
                    params.response_format, f"DGIdb ({len(items)} genes)")
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="tcm_gwas_associations",
    annotations={"title": "GWAS Catalog Associations",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_gwas_associations(params: GwasInput) -> str:
    '''Query GWAS Catalog (EBI) for disease/trait associations.

    Two modes (provide exactly one):
      1. keyword: search studies by disease/trait keyword (endpoint
         studies/search/findByDiseaseTrait?diseaseTrait=...), e.g.
         "ulcerative colitis" or "ovarian cancer" -> studies with reported genes.
      2. gene: associations for a specific gene, e.g. "BRCA1" or "IL23R" ->
         SNPs/traits/p-values.

    Args:
        params (GwasInput): exactly one of keyword / gene; limit; response_format.

    Returns:
        str: mode 1: studies (title, trait, gene hits, disease mapping);
             mode 2: associations (SNP, risk allele, p-value, trait).

    Examples:
        - "GWAS studies for ovarian cancer" -> keyword="ovarian cancer"
        - "SNP associations for gene BRCA1" -> gene="BRCA1"
    '''
    if bool(params.keyword) == bool(params.gene):
        return "Error: Provide exactly ONE of 'keyword' or 'gene'."
    headers = {"Accept": "application/json"}
    try:
        if params.keyword:
            r = await _make_api_request(
                "https://www.ebi.ac.uk/gwas/rest/api/studies/search/findByDiseaseTrait",
                params={"diseaseTrait": params.keyword, "size": params.limit},
                headers=headers, timeout=60, max_retries=1)
            r.raise_for_status()
            emb = r.json().get("_embedded", {}).get("studies", [])
            items = [{
                "study": s.get("accessionId"),
                "title": (s.get("title") or "")[:140],
                "trait": (s.get("diseaseTrait", {}).get("trait") or "") if s.get("diseaseTrait") else "",
                "reported_genes": [g.get("geneName") for g in s.get("gene2", [])][:8],
                "n_associations": s.get("associationCount"),
                "n_snps": s.get("snpCount"),
                "initial_sample": (s.get("initialSampleSize") or "")[:80]}
                for s in emb[:params.limit]]
            return _fmt({"keyword": params.keyword, "studies": items,
                         "source": "GWAS Catalog", "queried_at": _now_iso()},
                        params.response_format, f"GWAS studies: {params.keyword}")
        else:
            r = await _make_api_request(
                "https://www.ebi.ac.uk/gwas/rest/api/associations",
                params={"gene": params.gene, "size": params.limit},
                headers=headers, timeout=60, max_retries=1)
            r.raise_for_status()
            emb = r.json().get("_embedded", {}).get("associations", [])
            items = []
            for a in emb[:params.limit]:
                # 最强风险等位基因嵌套在 loci[].strongestRiskAlleles[]（新版结构）
                risk = ""
                for loci in a.get("loci", []):
                    for sra in loci.get("strongestRiskAlleles", []):
                        risk = sra.get("riskAlleleName", "")
                        if risk:
                            break
                    if risk:
                        break
                items.append({
                    "gene": params.gene,
                    "snp": (a.get("loci", [{}])[0].get("strongestRiskAlleles", [{}])[0]
                            .get("riskAlleleName", "").split("-")[0]),
                    "risk_allele": risk,
                    "pvalue": f"{a.get('pvalueMantissa', '')}e{a.get('pvalueExponent', '')}" if a.get("pvalueMantissa") is not None else "NR",
                    "trait": (a.get("diseaseTrait", {}).get("trait") or "") if a.get("diseaseTrait") else "",
                    "pubmed_id": a.get("pubmedId")})
            if not items:
                return f"No GWAS associations found for gene '{params.gene}'."
            return _fmt({"gene": params.gene, "associations": items,
                         "source": "GWAS Catalog", "queried_at": _now_iso()},
                        params.response_format, f"GWAS: {params.gene}")
    except Exception as e:
        return _handle_api_error(e)


# ---- 数据库可用性快照（2026-09-22 深度验证更新，refresh=True 可重探） ----

_DB_STATUS_CACHE = {
    "TCMSP": {"status": "partial", "note": "2026-10-13: old.tcmsp-e.com returns herb and ingredient tables (ob, dl) via tcm_tcmsp_search. tcmspw.com TLS fails. This page has no targets."},
    "HERB 2.0": {"status": "up", "note": "SPA alive; use XHR detail API"},
    "ETCM 2.0": {"status": "partial", "note": "主站 index 0.7s 活，但 /ETCM/api/ 404（API 未恢复）"},
    "SymMap": {"status": "up", "note": "nginx normal, no key needed"},
    "TCMBank": {"status": "up", "note": "http only, JSON API verified with berberine"},
    "NPASS": {"status": "up", "note": "RECOVERED 2026-09 (was 404 earlier)"},
    "DCABM-TCM": {"status": "partial", "note": "SPA index 活 0.3s，含 API key 但 API 路径未定位（chunk JS 内）"},
    "PubChem": {"status": "up", "note": "PUG-REST stable; keep 1s rate limit"},
    "Cactus": {"status": "up", "note": "RECOVERED (was deprecated); high latency ~8s"},
    "ChEMBL": {"status": "up", "note": "2026-09-22 恢复：molecule/search/mechanism 三端点全通（~1-2s），机制接口可用"},
    "Open Targets": {"status": "up", "note": "GraphQL v4；2026-09 修复 ensembl 模式（name 字段已删，改用 associatedDiseases）"},
    "CTD": {"status": "up", "note": "web + downloads OK; batchQuery 被 ALTCHA 拦截(302)，成分→靶点改走 DGIdb"},
    "DGIdb": {"status": "up", "note": "use NEW endpoint dgidb.org/api/graphql (old.dgidb.org dead)"},
    "DrugBank": {"status": "partial", "note": "go.drugbank.com/releases/downloads 200(~42s)；www 受 Cloudflare 保护仍 403"},
    "DisGeNET": {"status": "key_required", "note": "公开端点返回 SPA HTML，需 API key（免费注册）才能取 JSON"},
    "KEGG": {"status": "up", "note": "REST stable; compound IDs case-sensitive (uppercase)"},
    "UniProt": {"status": "up", "note": "REST ~4s latency, normal"},
    "STRING": {"status": "up", "note": "v12 endpoint verified"},
    "NCBI EUtils": {"status": "up", "note": "fastest probed endpoint (~0.7s)"},
    "Ensembl REST": {"status": "up", "note": "2026-09-22 恢复：lookup/symbol ~5.4s、xrefs/id ~18s（偶发超时需重试）"},
    "PDB RCSB": {"status": "up", "note": "core API verified"},
    "AlphaFold": {"status": "up", "note": "prediction API verified"},
    "Reactome": {"status": "up", "note": "ContentService OK; use internal IDs not accessions"},
    "g:Profiler": {"status": "up", "note": "gost POST verified via Python (PSL curl quoting issue only)"},
    "Enrichr": {"status": "up", "note": "datasetStatistics OK"},
    "InterPro": {"status": "up", "note": "entry API verified"},
    "JASPAR": {"status": "up", "note": "matrix API OK, high latency ~7s"},
    "dbSNP": {"status": "up", "note": "via eutils, verified"},
    "gnomAD": {"status": "partial", "note": "REST 2.1 endpoints broken (400); use GraphQL"},
    "GWAS Catalog": {"status": "up", "note": "REST verified"},
    "ClinVar": {"status": "up", "note": "via eutils, verified"},
    "ENCODE": {"status": "down", "note": "persistent 502 on portal"},
    "MONDO/OLS": {"status": "up", "note": "2026-09-22 新增：OLS API 搜索 disease→MONDO ID，UC 命中 MONDO_0005101（~1.7s）"},
    "BindingDB": {"status": "up", "note": "2026-09-22 新增：/rwd/bind/index.jsp 活（旧 /bind/ 404）"},
    "MSigDB": {"status": "up", "note": "2026-09-22 新增：index 1.2s 可达（基因集下载需登录）"},
    "SwissTargetPrediction": {"status": "partial", "note": "2026-09-22 新增：表单 POST /predict.php 返回 200 页面；异步结果轮询适配待跟进"},
}

_PROBE_ENDPOINTS = {
    "TCMBank": "http://tcmbank.cn/api/ingredient/search?ingredient_name=berberine&cur_page=1&per_page=1",
    "PubChem": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/2244/property/MolecularFormula/JSON",
    "Open Targets": "https://api.platform.opentargets.org/api/v4/graphql",
    "KEGG": "https://rest.kegg.jp/get/c00757",
    "UniProt": "https://rest.uniprot.org/uniprotkb/P01308.json",
    "STRING": "https://version-12-0.string-db.org/api/tsv/network?identifiers=TP53&species=9606",
    "g:Profiler": "https://biit.cs.ut.ee/gprofiler/api/gost/profile/",
    "PDB RCSB": "https://data.rcsb.org/rest/v1/core/entry/4HHB",
    "AlphaFold": "https://alphafold.ebi.ac.uk/api/prediction/P01308",
    "ChEMBL": "https://www.ebi.ac.uk/chembl/api/data/molecule/CHEMBL25.json",
    "Ensembl REST": "https://rest.ensembl.org/lookup/symbol/homo_sapiens/TNF?content-type=application/json",
}


@mcp.tool(
    name="tcm_check_database_status",
    annotations={"title": "Database Availability Check",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_check_database_status(params: DbStatusInput) -> str:
    '''Check availability of the 32 databases covered by this toolkit.

    Default (refresh=False) returns the cached 2026-09-14 probe snapshot —
    instant. With refresh=True, re-probes 9 key live endpoints (~30-60s) and
    merges results into the snapshot.

    Use this BEFORE running batch pipelines to pick working channels, or when a
    tool returns connection errors, to find alternatives.

    Args:
        params (DbStatusInput):
            - refresh (bool): default False (cached); True re-probes live
            - response_format

    Returns:
        str: per-database status (up / down / partial / key_required) + notes.

    Examples:
        - "Is ChEMBL back up?" -> refresh=True, then look for ChEMBL
        - "Which TCM databases can I use right now?" -> refresh=False
    '''
    status = {k: dict(v) for k, v in _DB_STATUS_CACHE.items()}
    if params.refresh:
        for name, url in _PROBE_ENDPOINTS.items():
            try:
                method = "POST" if ("graphql" in url or "gprofiler" in url) else "GET"
                body = ({"query": '{ target(ensemblId: "ENSG00000168610") { approvedSymbol } }'}
                        if "graphql" in url else
                        ({"organism": "hsapiens", "query": ["TP53"], "sources": ["GO:MF"]}
                         if "gprofiler" in url else None))
                r = await _make_api_request(url, method=method, json_body=body,
                                            timeout=20, max_retries=0)
                status[name] = {"status": "up" if r.status_code < 400 else f"http_{r.status_code}",
                                "note": f"live probe {r.status_code} at {datetime.now().strftime('%H:%M:%S')}"}
            except Exception as e:
                status[name] = {"status": "unreachable",
                                "note": f"live probe failed: {type(e).__name__} at {datetime.now().strftime('%H:%M:%S')}"}
    n_up = sum(1 for v in status.values() if v["status"] == "up")
    return _fmt({"snapshot_date": "2026-09-22",
                 "refreshed": params.refresh,
                 "summary": f"{n_up}/{len(status)} up",
                 "databases": status,
                 "queried_at": _now_iso()},
                params.response_format, f"Database Status ({n_up}/{len(status)} up)")


@mcp.tool(
    name="tcm_audit_evidence",
    annotations={"title": "Audit Workflow Evidence (Local)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_audit_evidence(params: AuditEvidenceInput) -> str:
    '''Audit the evidence chain of a completed workflow run (local artifacts dir).

    Runs the skill's evidence_audit.py checks on a workflow_engine artifacts
    directory: (A) evidence log covers all 11 steps, (B) core artifacts exist,
    (C) traceability rows have sources, (D) positive-control completeness=100%,
    (E) Top10 compounds traceable. Returns PASS / REVIEW / FAIL.

    Args:
        params (AuditEvidenceInput):
            - artifacts_dir (str): local path to one run's artifacts directory
              (contains evidence_log.json and *临床锚定评分.xlsx)
            - response_format: "markdown" (default) or "json"

    Returns:
        str: audit verdict with per-check details.

    Examples:
        - "Audit my last run at workflow_engine/artifacts/run_20260914"
          -> params={"artifacts_dir": "workflow_engine/artifacts/run_20260914"}
    '''
    import os
    import sys
    artifacts_dir = params.artifacts_dir
    if not os.path.isdir(artifacts_dir):
        return f"Error: directory not found: {artifacts_dir}. Provide the path to a single run's artifacts folder."
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    audit_script = os.path.join(skill_root, "scripts", "evidence_audit.py")
    if not os.path.exists(audit_script):
        return "Error: scripts/evidence_audit.py not found next to the skill root."
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, audit_script, artifacts_dir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1"})
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        text = out.decode("utf-8", errors="replace")
        if params.response_format == ResponseFormat.JSON:
            verdict = ("PASS" if "结论：PASS" in text
                       else "REVIEW" if "结论：REVIEW" in text else "FAIL")
            return json.dumps({"artifacts_dir": artifacts_dir,
                               "verdict": verdict,
                               "audit_output": text,
                               "report": os.path.join(artifacts_dir, "audit_report.md")},
                              ensure_ascii=False, indent=2)
        return text
    except asyncio.TimeoutError:
        return "Error: audit timed out after 120s. Check the artifacts directory size."
    except Exception as e:
        return _handle_api_error(e)


# ================= SymMap / 疾病-通路工作流工具（2026-10-11 新增） =================
# 实测通道（2026-10-10）：SymMap v2.0 POST /search/ /search_th/ /related_components/
# /related_enrichment/ 全通；KEGG list/disease + link/pathway/disease + get 全通。
# 已知 502 陷阱：related_components 大结果集（hub 基因 Gene->Mol、多靶成分 Mol->Herb）
# 后端超时；客户端重试 3 次后返回明确错误。

import math as _math
import sys
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from tcm_core.stats import log_hypergeom_sf as _log_hypergeom_sf  # noqa: E402

SYMMAP_BASE = "http://symmap.org"
SYMMAP_TABLES = ("Herb", "Mol", "TCM_symptom", "MM_symptom",
                 "Gene", "Disease", "Syndrome")
KEGG_REST = "https://rest.kegg.jp"
_GENE_POPULATION_DEFAULT = 20000  # human protein-coding gene space


async def _symmap_post(path: str, form: Dict[str, str], timeout: float = 90.0):
    '''POST SymMap form endpoint, return parsed JSON (3 retries on 502).'''
    last = None
    for attempt in range(3):
        try:
            r = await _make_api_request(SYMMAP_BASE + path, method="POST",
                                        data=form, timeout=timeout,
                                        max_retries=0)
            r.raise_for_status()
            return r.json()
        except Exception as e:                     # noqa: BLE001
            last = e
            await asyncio.sleep(6.0 * (attempt + 1))
    raise RuntimeError(f"SymMap request failed after retries: {path} {form} ({last})")


class SymMapLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(..., description="search | typeahead | related | enrichment")
    table: Optional[str] = Field(
        None, description="SymMap table: Herb/Mol/TCM_symptom/MM_symptom/Gene/"
        "Disease/Syndrome (required for search/typeahead/related)")
    key: Optional[str] = Field(None, description="search keyword (entity name)")
    rrid: Optional[str] = Field(None, description="SymMap entity id (e.g. SMDE14075)")
    genes: Optional[List[str]] = Field(
        None, description="gene symbols for enrichment action (KEGG pathways)")
    limit: int = Field(10, ge=1, le=100)
    response_format: ResponseFormat = ResponseFormat.MARKDOWN


@mcp.tool(
    name="tcm_symmap_lookup",
    annotations={"title": "SymMap v2.0 Multi-dimension TCM Lookup",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_symmap_lookup(params: SymMapLookupInput) -> str:
    '''Query SymMap v2.0 (703 herbs / 27k ingredients / symptoms / genes / diseases)
    across its multi-dimension network.

    Actions:
      - search     : entities by name (table+key) -> records with SymMap ids
      - typeahead  : name suggestions (table+key)
      - related    : related entities of a TARGET table for an entity id
                     (rrid+table). Directions: Herb<->Mol; Mol<->Gene;
                     Gene<->Disease; Disease<->MM_symptom; TCM_symptom<->Herb ...
      - enrichment : KEGG pathway enrichment for a gene-symbol list
                     (server-side OR / P / FDR(BH))

    Args:
        params (SymMapLookupInput): action + parameters (see fields).

    Returns:
        str: records with source='SymMap v2.0' and queried_at stamp.

    Examples:
        - "Find UC in SymMap" -> action=search, table=Disease, key="ulcerative colitis"
        - "Genes related to UC SMDE14075" -> action=related, rrid=SMDE14075, table=Gene
        - "Which herbs contain berberine?" -> action=search table=Mol key=berberine,
          then action=related rrid=<Mol_id> table=Herb (may 502 for poly-target mols)
        - "Enrich NFKB1, RELA, JUN to KEGG pathways" -> action=enrichment
    '''
    try:
        if params.action == "search":
            if not (params.table and params.key):
                return "Error: search requires table and key."
            if params.table not in SYMMAP_TABLES:
                return f"Error: table must be one of {SYMMAP_TABLES}."
            res = await _symmap_post("/search/",
                                     {"table_name": params.table, "key": params.key})
            data = (res.get("data") or [])[: params.limit]
            return _fmt({"action": "search", "table": params.table,
                         "query": params.key, "hits": len(res.get("data") or []),
                         "records": data,
                         "source": "SymMap v2.0 /search/",
                         "queried_at": _now_iso()},
                        params.response_format, f"SymMap search: {params.key}")
        if params.action == "typeahead":
            if not (params.table and params.key):
                return "Error: typeahead requires table and key."
            res = await _symmap_post("/search_th/",
                                     {"name": params.key, "table_name": params.table})
            return _fmt({"action": "typeahead", "table": params.table,
                         "suggestions": (res or [])[: params.limit],
                         "source": "SymMap v2.0 /search_th/",
                         "queried_at": _now_iso()},
                        params.response_format, f"SymMap typeahead: {params.key}")
        if params.action == "related":
            if not (params.rrid and params.table):
                return "Error: related requires rrid and table (target table)."
            if params.table not in SYMMAP_TABLES:
                return f"Error: table must be one of {SYMMAP_TABLES}."
            res = await _symmap_post("/related_components/",
                                     {"rrid": params.rrid, "table_name": params.table,
                                      "filter": "0"})
            data = (res.get("data") or [])
            return _fmt({"action": "related", "rrid": params.rrid,
                         "target_table": params.table, "total": len(data),
                         "records": data[: params.limit],
                         "note": "large result sets (hub gene -> Mol, poly-target "
                                 "Mol -> Herb) may time out server-side (502)",
                         "source": "SymMap v2.0 /related_components/",
                         "queried_at": _now_iso()},
                        params.response_format, f"SymMap related: {params.rrid}")
        if params.action == "enrichment":
            if not params.genes:
                return "Error: enrichment requires genes (symbols)."
            genes = [g.rstrip(";").strip() for g in params.genes if g.strip()][:300]
            res = await _symmap_post("/related_enrichment/",
                                     {"genes": json.dumps({"gene": genes})},
                                     timeout=120.0)
            rows = (res.get("enrichment") or {}).get("data") or []
            rows = [r for r in rows if r][: params.limit]
            return _fmt({"action": "enrichment", "input_genes": len(genes),
                         "pathways": [{"pathway_id": r[0], "name": r[1],
                                       "odd_ratio": r[2], "p": r[3], "fdr_bh": r[4],
                                       "n_genes": r[5], "genes": r[6]}
                                      for r in rows],
                         "source": "SymMap v2.0 /related_enrichment/",
                         "queried_at": _now_iso()},
                        params.response_format,
                        f"SymMap KEGG enrichment ({len(genes)} genes)")
        return "Error: action must be search|typeahead|related|enrichment."
    except Exception as e:
        return _handle_api_error(e)


class KeggDiseasePathwaysInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disease_name: str = Field(..., description='e.g. "ulcerative colitis"')
    use_enrichment: bool = Field(
        True, description="also enrich SymMap disease-related genes to KEGG pathways")
    limit: int = Field(15, ge=1, le=100)
    response_format: ResponseFormat = ResponseFormat.MARKDOWN


async def _kegg_get(path: str, timeout: float = 60.0) -> str:
    r = await _make_api_request(KEGG_REST + path, method="GET",
                                timeout=timeout, max_retries=2)
    r.raise_for_status()
    return r.text


@mcp.tool(
    name="tcm_kegg_disease_pathways",
    annotations={"title": "Disease -> KEGG Pathways (direct + enrichment)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_kegg_disease_pathways(params: KeggDiseasePathwaysInput) -> str:
    '''Step 1 of the disease-first workflow: which KEGG pathways relate to a disease?

    Two channels, merged and ranked:
      (a) KEGG DISEASE (H#####) matched by name -> direct disease-pathway links
          (KEGG link/pathway/disease; coverage is mostly cancer/infection)
      (b) SymMap disease-related genes -> server-side KEGG enrichment
          (odd ratio / P / FDR(BH)); catches multifactorial diseases

    Args:
        params (KeggDiseasePathwaysInput): disease_name etc.

    Returns:
        str: ranked pathway list with source tags (KEGG-direct / SymMap-enrichment).

    Examples:
        - "Which pathways relate to ulcerative colitis?" ->
          disease_name="ulcerative colitis"
    '''
    try:
        text = await _kegg_get("/list/disease")
        low = params.disease_name.lower()
        hits = []
        for line in text.splitlines():
            if "\t" not in line:
                continue
            hid, names = line.split("\t", 1)
            for nm in names.split(";"):
                nm = nm.strip().lower()
                if nm == low or (len(low) > 5 and low in nm) or \
                        (len(nm) > 5 and nm in low):
                    hits.append({"kegg_disease_id": hid, "name": names.strip()})
                    break

        pathways: Dict[str, Dict] = {}
        # (a) direct links
        for d in hits[:3]:
            try:
                lt = await _kegg_get(f"/link/pathway/{d['kegg_disease_id']}")
                for line in lt.splitlines():
                    if "\t" in line:
                        pid = line.split("\t")[1].replace("path:", "")
                        e = pathways.setdefault(pid, {"sources": set()})
                        e["sources"].add("KEGG-direct")
            except Exception:
                continue

        # pathway names (single request)
        names_map: Dict[str, str] = {}
        try:
            pt = await _kegg_get("/list/pathway/hsa")
            for line in pt.splitlines():
                if "\t" in line:
                    pid, nm = line.split("\t", 1)
                    names_map[pid.replace("path:", "")] = nm.strip()
        except Exception:
            pass

        # (b) SymMap enrichment channel
        symmap_note = None
        if params.use_enrichment:
            try:
                res = await _symmap_post(
                    "/search/", {"table_name": "Disease",
                                 "key": params.disease_name})
                drows = res.get("data") or []
                if drows:
                    did = drows[0].get("Disease_id")
                    res2 = await _symmap_post(
                        "/related_components/",
                        {"rrid": did, "table_name": "Gene", "filter": "0"},
                        timeout=120.0)
                    genes = [g.get("Gene_symbol") for g in (res2.get("data") or [])
                             if g.get("Gene_symbol")][:300]
                    if genes:
                        res3 = await _symmap_post(
                            "/related_enrichment/",
                            {"genes": json.dumps({"gene": genes})},
                            timeout=150.0)
                        rows = (res3.get("enrichment") or {}).get("data") or []
                        for r in [x for x in rows if x]:
                            pid = str(r[0])
                            e = pathways.setdefault(pid, {"sources": set()})
                            e["sources"].add("SymMap-enrichment")
                            e["enrich_p"] = r[3]
                            e["enrich_fdr"] = r[4]
                            e["odd_ratio"] = r[2]
                            e["related_genes"] = r[6]
                    symmap_note = {"disease_id": did, "n_genes": len(genes)}
            except Exception as e:
                symmap_note = {"error": str(e)}

        out_rows = []
        for pid, e in pathways.items():
            out_rows.append({
                "pathway_id": pid,
                "pathway_name": names_map.get(pid, pid),
                "source": "+".join(sorted(e["sources"])),
                "enrich_p": e.get("enrich_p"),
                "enrich_fdr": e.get("enrich_fdr"),
                "odd_ratio": e.get("odd_ratio"),
                "related_genes": e.get("related_genes")})
        out_rows.sort(key=lambda r: (0 if "KEGG-direct" in r["source"] else 1,
                                     r.get("enrich_p") or 1))
        return _fmt({
            "disease": params.disease_name, "kegg_disease": hits,
            "symmap": symmap_note, "n_pathways": len(out_rows),
            "pathways": out_rows[: params.limit],
            "source": "KEGG REST + SymMap v2.0 enrichment",
            "queried_at": _now_iso()},
            params.response_format,
            f"Disease pathways: {params.disease_name} ({len(out_rows)})")
    except Exception as e:
        return _handle_api_error(e)


class HypergeometricInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hit_genes: List[str] = Field(
        ..., min_length=1, description="query genes overlapping the term")
    term_genes: List[str] = Field(
        ..., min_length=1, description="all genes of the term/pathway")
    query_genes: List[str] = Field(
        ..., min_length=1, description="all genes of the query set")
    population_size: int = Field(
        _GENE_POPULATION_DEFAULT, ge=1000, le=40000,
        description="background gene space (default 20000 human protein-coding)")
    response_format: ResponseFormat = ResponseFormat.MARKDOWN


def _log_p_hypergeom(k: int, K: int, n: int, N: int) -> float:
    '''ln P(X >= k). Impossible k returns -inf, so the probability is 0.'''
    return _log_hypergeom_sf(k, K, n, N)


@mcp.tool(
    name="tcm_hypergeometric_test",
    annotations={"title": "Hypergeometric Over-representation Test",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_hypergeometric_test(params: HypergeometricInput) -> str:
    '''One-tailed hypergeometric over-representation test (cross-validation step).

    Tests whether a query gene set (e.g. ingredient targets) is enriched for a
    term/pathway gene set (e.g. disease pathway genes). Computes:
      k = |query ∩ term|, K = |term|, n = |query|, N = population
      P(X >= k), enrichment ratio = (k/n)/(K/N).

    Validation: poker check N=52,K=13,n=5,k>=3 -> 0.0928 (textbook 9.28%).

    Args:
        params (HypergeometricInput): three gene lists + population size.

    Returns:
        str: k/K/n/N, p_value, enrichment_ratio with interpretation.

    Examples:
        - "Are berberine targets enriched for the IBD pathway?" ->
          hit_genes=<overlap>, term_genes=<66 IBD genes>,
          query_genes=<berberine targets>
    '''
    try:
        q = set(params.query_genes)
        t = set(params.term_genes)
        h = set(params.hit_genes)
        inter = q & t
        if set(params.hit_genes) != inter:
            hit = sorted(inter)
        else:
            hit = sorted(h)
        k = len(hit)
        K = len(t)
        n = len(q)
        N = params.population_size
        if n > N or K > N:
            return "Error: term/query sizes must not exceed population_size."
        lp = _log_p_hypergeom(k, K, n, N) if k else 0.0
        p = _math.exp(lp) if lp > float("-inf") else 0.0
        er = (k / n) / (K / N) if (k and n) else 0.0
        verdict = ("significant (p<0.05)" if p < 0.05 else "not significant")
        return _fmt({
            "k_hits": k, "term_size": K, "query_size": n, "population": N,
            "hit_genes": hit, "p_value": p,
            "enrichment_ratio": round(er, 2),
            "interpretation": verdict,
            "method": "one-tailed hypergeometric (over-representation)",
            "note": "enrichment_ratio > 1 means term genes are over-represented "
                    "in the query set relative to the background"},
            params.response_format, f"Hypergeometric test: {verdict}")
    except Exception as e:
        return _handle_api_error(e)

# ================= 交叉分析与数据循环工具（2026-10-12 新增） =================
# 通道实测（2026-10-12）：UniProt ID mapping（run+poll，P04637→CHEMBL4096）、
# Monarch v3（search/entity）、Pharos GraphQL（target by sym → TDL/UniProt/疾病）、
# PubChem assay API、g:Profiler gost、STRING interaction_partners 全通。
# Wikidata SPARQL 当晚连接重置（历史可靠，内置 3 次重试）；OT Genetics 域名失效、
# Reactome AnalysisService 迁移 404、COCONUT/SuperNatural/LOTUS API 不可达——均已记录。

PHAROS_GQL = "https://pharos-api.ncats.io/graphql"
OT_GQL = "https://api.platform.opentargets.org/api/v4/graphql"
GPROFILER_URL = "https://biit.cs.ut.ee/gprofiler/api/gost/profile/"
CHEMBL_SEARCH = "https://www.ebi.ac.uk/chembl/api/data/molecule/search.json"
WD_SPARQL = "https://query.wikidata.org/sparql"
UNIPROT_IDMAP = "https://rest.uniprot.org/idmapping"

# Wikidata 常用标识符属性（交叉库 ID 矩阵）
WD_PROPS = {
    "P231": "CAS Registry Number", "P235": "InChIKey", "P234": "InChI",
    "P662": "PubChem CID", "P233": "canonical SMILES", "P592": "ChEMBL ID",
    "P665": "KEGG ID", "P715": "DrugBank ID", "P352": "UniProt AC",
    "P351": "NCBI Gene ID", "P638": "PDB ID",
}


async def _pharos_post(query: str, timeout: float = 30.0) -> Dict:
    '''POST a GraphQL query to Pharos (introspection-validated schema).'''
    r = await _make_api_request(PHAROS_GQL,
                                method="POST",
                                json_body={"query": query},
                                headers={"Content-Type": "application/json"},
                                timeout=timeout, max_retries=1)
    r.raise_for_status()
    return r.json()


class CrossDBScreenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(..., description="compound | gene")
    entities: List[str] = Field(
        ..., min_length=1, max_length=20,
        description="compound names (e.g. berberine) or gene symbols (e.g. TNF)")
    limit: int = Field(5, ge=1, le=20)
    concurrency: int = Field(3, ge=1, le=6,
                            description="parallel entity probes (efficiency; "
                                        "PubChem stays rate-limited internally)")
    response_format: ResponseFormat = ResponseFormat.MARKDOWN


@mcp.tool(
    name="tcm_cross_database_screen",
    annotations={"title": "Cross-database Evidence Screen (compound/gene)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_cross_database_screen(params: CrossDBScreenInput) -> str:
    '''Screen entities across multiple databases and merge evidence.

    The core cross-analysis tool: each entity is probed in parallel channels,
    IDs/evidence are merged per entity, and a consensus score counts how many
    databases independently confirm it.

    mode=compound: PubChem (CID/SMILES/InChIKey) + ChEMBL (chembl_id) +
    SymMap (Mol_id) -> consensus 0-3.
    mode=gene: STRING (interactors) + Open Targets (target search) +
    SymMap (Gene_id) + Pharos (TDL development level/UniProt) -> consensus 0-4.

    Args:
        params (CrossDBScreenInput): mode + entities + limit.

    Returns:
        str: per-entity merged evidence record with consensus score,
        every field carrying its source DB and queried_at.

    Examples:
        - "Cross-screen berberine and quercetin" ->
          mode=compound, entities=["berberine","quercetin"]
        - "Cross-screen TNF and IL10 as gene targets" ->
          mode=gene, entities=["TNF","IL10"]
    '''
    if params.mode not in ("compound", "gene"):
        return "Error: mode must be compound|gene."
    entities = [e.strip() for e in params.entities if e.strip()]
    sem = asyncio.Semaphore(params.concurrency)

    async def _screen_one(ent: str) -> Dict[str, Any]:
        rec: Dict[str, Any] = {"entity": ent}
        hits = 0
        summary_bits = []
        ch: Dict[str, Any] = {}
        try:
            if params.mode == "compound":
                # PubChem
                try:
                    r = await _make_api_request(
                        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                        f"{_urlencode(ent)}/property/MolecularFormula,MolecularWeight,"
                        f"CanonicalSMILES,InChIKey/JSON",
                        respect_pubchem_limit=True, timeout=20.0)
                    props = (r.json().get("PropertyTable") or {}).get("Properties") or []
                    if props:
                        p0 = props[0]
                        ch["pubchem"] = {
                            "cid": p0.get("CID"),
                            "molecular_formula": p0.get("MolecularFormula"),
                            "molecular_weight": p0.get("MolecularWeight"),
                            "canonical_smiles": p0.get("CanonicalSMILES"),
                            "inchikey": p0.get("InChIKey")}
                        summary_bits.append(
                            f"PubChem(CID={p0.get('CID')},"
                            f"InChIKey={p0.get('InChIKey')})")
                        hits += 1
                except Exception:
                    ch["pubchem"] = {"error": "not found / lookup failed"}
                # ChEMBL (molecule/search.json, validated endpoint)
                try:
                    r = await _make_api_request(
                        CHEMBL_SEARCH, params={"q": ent}, timeout=20.0)
                    mol_hits = r.json().get("molecules") or []
                    if mol_hits:
                        m0 = mol_hits[0]
                        ch["chembl"] = {
                            "chembl_id": m0.get("molecule_chembl_id"),
                            "pref_name": m0.get("pref_name")}
                        summary_bits.append(
                            f"ChEMBL({m0.get('molecule_chembl_id')})")
                        hits += 1
                    else:
                        ch["chembl"] = {"error": "no compound hit"}
                except Exception:
                    ch["chembl"] = {"error": "lookup failed"}
                # SymMap
                try:
                    res = await _symmap_post("/search/",
                                             {"table_name": "Mol", "key": ent})
                    rows = res.get("data") or []
                    if rows:
                        m0 = rows[0]
                        mid = m0.get("Mol_id") or m0.get("MOL_id")
                        ch["symmap"] = {"mol_id": mid,
                                        "name": m0.get("Mol_name") or m0.get("name")}
                        summary_bits.append(f"SymMap({mid})")
                        hits += 1
                    else:
                        ch["symmap"] = {"error": "no hit"}
                except Exception:
                    ch["symmap"] = {"error": "timeout/failed"}
            else:
                # STRING interaction partners
                try:
                    r = await _make_api_request(
                        "https://string-db.org/api/tsv/interaction_partners",
                        params={"identifiers": ent, "species": "9606",
                                "limit": params.limit}, timeout=25.0)
                    lines = [ln for ln in r.text.splitlines() if ln.strip()]
                    if len(lines) > 1:
                        hdr = lines[0].split("\t")
                        partners = []
                        for ln in lines[1:params.limit + 1]:
                            f = ln.split("\t")
                            row = dict(zip(hdr, f))
                            partners.append({
                                "partner": row.get("preferredName_B"),
                                "score": row.get("score")})
                        ch["string"] = {
                            "n_partners": len(partners), "top": partners[:5]}
                        summary_bits.append(
                            f"STRING({len(partners)} partners)")
                        hits += 1
                    else:
                        ch["string"] = {"error": "no interactions"}
                except Exception:
                    ch["string"] = {"error": "lookup failed"}
                # Open Targets target search
                try:
                    gql = ('{ search(queryString: "' + ent + '", '
                           'entityNames: ["target"]) { hits { id name entity } } }')
                    r = await _make_api_request(
                        OT_GQL, method="POST", json_body={"query": gql},
                        headers={"Content-Type": "application/json"},
                        timeout=25.0, max_retries=1)
                    h = ((r.json().get("data") or {}).get("search") or {}).get("hits") or []
                    if h:
                        ch["opentargets"] = {
                            "target_id": h[0].get("id"),
                            "name": h[0].get("name"),
                            "n_hits": len(h)}
                        summary_bits.append(
                            f"OpenTargets({h[0].get('id')})")
                        hits += 1
                    else:
                        ch["opentargets"] = {"error": "no target hit"}
                except Exception:
                    ch["opentargets"] = {"error": "lookup failed"}
                # SymMap Gene
                try:
                    res = await _symmap_post("/search/",
                                             {"table_name": "Gene", "key": ent})
                    rows = res.get("data") or []
                    if rows:
                        gid = rows[0].get("Gene_id")
                        ch["symmap"] = {
                            "gene_id": gid,
                            "n_records": len(rows)}
                        summary_bits.append(f"SymMap({gid})")
                        hits += 1
                    else:
                        ch["symmap"] = {"error": "no hit"}
                except Exception:
                    ch["symmap"] = {"error": "timeout/failed"}
                # Pharos development level
                try:
                    gql = ('{ target(q: { sym: "' + ent + '" }) { tcrdid uniprot '
                           'name sym tdl fam } }')
                    d = await _pharos_post(gql)
                    t = (d.get("data") or {}).get("target")
                    if t:
                        ch["pharos"] = {
                            "uniprot": t.get("uniprot"),
                            "target_name": t.get("name"),
                            "tdl": t.get("tdl"),
                            "family": t.get("fam")}
                        summary_bits.append(
                            f"Pharos(TDL={t.get('tdl')},UniProt={t.get('uniprot')})")
                        hits += 1
                    else:
                        ch["pharos"] = {"error": "no target"}
                except Exception:
                    ch["pharos"] = {"error": "lookup failed"}
        except Exception as e:
            ch["_error"] = str(e)[:120]
        max_hits = 3 if params.mode == "compound" else 4
        rec["summary"] = f"{ent}: " + (", ".join(summary_bits) if summary_bits
                           else "no channel hit") + f" -> {hits}/{max_hits}"
        rec["consensus_score"] = f"{hits}/{max_hits}"
        rec["consensus_hits"] = hits
        rec["channels"] = ch
        return rec

    async def _bounded(ent: str) -> Dict[str, Any]:
        async with sem:
            return await _screen_one(ent)

    records = list(await asyncio.gather(*[_bounded(e) for e in entities]))
    ordered = sorted(records, key=lambda x: -x["consensus_hits"])
    return _fmt({"mode": params.mode, "n_entities": len(records),
                 "consensus_ranking": ordered,
                 "note": "consensus_hits counts databases independently confirming "
                         "the entity; IDs from different channels can be chained "
                         "(e.g. PubChem CID -> tcm_pubchem_lookup, UniProt -> "
                         "tcm_uniprot_idmap, Mol_id -> tcm_symmap_lookup related)",
                 "source": "PubChem+ChEMBL+SymMap+STRING+OpenTargets+Pharos (live)",
                 "queried_at": _now_iso()},
                params.response_format,
                f"Cross-database screen ({params.mode}, {len(records)} entities)")


def _brandes_betweenness(adj: Dict[str, set]) -> Dict[str, float]:
    '''Brandes algorithm, unweighted undirected; O(V*E).'''
    from collections import deque
    nodes = list(adj.keys())
    bet = {v: 0.0 for v in nodes}
    for s in nodes:
        stack, parents = [], {w: [] for w in nodes}
        sigma = {v: 0 for v in nodes}
        sigma[s] = 1
        dist = {v: -1 for v in nodes}
        dist[s] = 0
        q = deque([s])
        while q:
            v = q.popleft()
            stack.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    q.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    parents[w].append(v)
        delta = {v: 0.0 for v in nodes}
        while stack:
            w = stack.pop()
            for v in parents[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                bet[w] += delta[w]
    return {v: b / 2.0 for v, b in bet.items()}


def _graph_metrics(adj: Dict[str, set]) -> Dict[str, Any]:
    '''Degree, betweenness, closeness, k-core levels, components.'''
    from collections import deque
    nodes = list(adj.keys())
    n = len(nodes)
    deg = {v: len(adj[v]) for v in nodes}
    bet = _brandes_betweenness(adj) if n <= 300 else {v: 0.0 for v in nodes}
    close = {}
    for s in nodes:
        seen = {s: 0}
        q = deque([s])
        while q:
            v = q.popleft()
            for w in adj[v]:
                if w not in seen:
                    seen[w] = seen[v] + 1
                    q.append(w)
        reach = len(seen) - 1
        total = sum(seen.values())
        close[s] = (reach / total) if total else 0.0
    # k-core peeling
    core = {v: 0 for v in nodes}
    work = {v: set(adj[v]) for v in nodes}
    k = 0
    while work:
        k += 1
        while True:
            drop = [v for v in work if len(work[v]) < k]
            if not drop:
                break
            for v in drop:
                for w in work[v]:
                    work[w].discard(v)
                core[v] = k - 1
                del work[v]
    # connected components
    comps, seen = [], set()
    for s in nodes:
        if s in seen:
            continue
        comp, q = [], deque([s])
        seen.add(s)
        while q:
            v = q.popleft()
            comp.append(v)
            for w in adj[v]:
                if w not in seen:
                    seen.add(w)
                    q.append(w)
        comps.append(sorted(comp))
    return {"degree": deg, "betweenness": bet, "closeness": close,
            "core": core, "components": comps}


class NetworkAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genes: List[str] = Field(..., min_length=2, max_length=200,
                             description="gene symbols, e.g. target list from "
                                         "tcm_search_disease_targets")
    species: int = Field(9606, description="NCBI taxon id (9606 human)")
    required_score: float = Field(0.4, ge=0.15, le=1.0,
                                  description="STRING combined score threshold "
                                              "(0.4 = medium confidence)")
    hub_top: int = Field(10, ge=1, le=50)
    response_format: ResponseFormat = ResponseFormat.MARKDOWN


@mcp.tool(
    name="tcm_network_analysis",
    annotations={"title": "STRING PPI Network + Hub Centrality Analysis",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_network_analysis(params: NetworkAnalysisInput) -> str:
    '''Build a STRING PPI network and compute centrality-ranked hubs.

    Data-circulation step: feed disease targets (tcm_search_disease_targets) or
    ingredient targets into this tool to find network hubs. Computes degree,
    betweenness (Brandes), closeness centrality, k-core levels and connected
    components; ranks hubs by a composite rank-sum of the three centralities.

    Args:
        params (NetworkAnalysisInput): genes + STRING params + hub_top.

    Returns:
        str: network stats, components, and top hub table with centralities.

    Examples:
        - "Find hub genes among UC targets TNF, IL10, IL12B, JAK2, STAT3" ->
          genes=["TNF","IL10","IL12B","JAK2","STAT3"]
    '''
    try:
        genes = [g.strip() for g in params.genes if g.strip()]
        r = await _make_api_request(
            "https://string-db.org/api/tsv/network",
            params={"identifiers": "\r".join(genes),
                    "species": str(params.species),
                    "required_score": params.required_score},
            timeout=60.0)
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        adj: Dict[str, set] = {g: set() for g in genes}
        edges = []
        if len(lines) > 1:
            hdr = lines[0].split("\t")
            for ln in lines[1:]:
                f = ln.split("\t")
                row = dict(zip(hdr, f))
                a = row.get("preferredName_A") or row.get("stringId_A")
                b = row.get("preferredName_B") or row.get("stringId_B")
                if a and b:
                    adj.setdefault(a, set()).add(b)
                    adj.setdefault(b, set()).add(a)
                    edges.append({"a": a, "b": b, "score": row.get("score")})
        m = _graph_metrics(adj)
        n = len(adj)
        e = len(edges)
        density = (2 * e) / (n * (n - 1)) if n > 1 else 0.0
        # composite hub ranking: rank-sum of degree/betweenness/closeness
        by_deg = sorted(adj, key=lambda v: -m["degree"][v])
        by_bet = sorted(adj, key=lambda v: -m["betweenness"][v])
        by_clo = sorted(adj, key=lambda v: -m["closeness"][v])
        rank_sum = {v: by_deg.index(v) + by_bet.index(v) + by_clo.index(v)
                    for v in adj}
        hubs = sorted(adj, key=lambda v: rank_sum[v])[:params.hub_top]
        hub_rows = [{
            "gene": v,
            "degree": m["degree"][v],
            "betweenness": round(m["betweenness"][v], 2),
            "closeness": round(m["closeness"][v], 3),
            "k_core": m["core"][v],
            "composite_rank": rank_sum[v]} for v in hubs]
        comp_sizes = sorted((len(c) for c in m["components"]), reverse=True)
        return _fmt({
            "input_genes": len(genes), "nodes": n, "edges": e,
            "density": round(density, 4),
            "required_score": params.required_score,
            "components": len(m["components"]),
            "component_sizes": comp_sizes[:10],
            "top_hubs": hub_rows,
            "edge_sample": edges[:10],
            "note": "hubs ranked by degree+betweenness+closeness rank-sum; "
                    "feed hubs into tcm_multi_enrichment or "
                    "tcm_cross_database_screen(mode=gene)",
            "source": "STRING v12 network API",
            "queried_at": _now_iso()},
            params.response_format,
            f"PPI network analysis ({n} nodes, {e} edges)")
    except Exception as e:
        return _handle_api_error(e)


class MultiEnrichmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genes: List[str] = Field(..., min_length=3, max_length=300,
                             description="gene symbols (e.g. network hubs)")
    sources: List[str] = Field(
        ["gprofiler", "symmap"],
        description="enrichment engines to compare: gprofiler, symmap")
    limit: int = Field(15, ge=1, le=50)
    response_format: ResponseFormat = ResponseFormat.MARKDOWN


@mcp.tool(
    name="tcm_multi_enrichment",
    annotations={"title": "Cross-engine Enrichment (g:Profiler + SymMap)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_multi_enrichment(params: MultiEnrichmentInput) -> str:
    '''Run KEGG enrichment on two independent engines and compare.

    Data-circulation step: hub genes -> enrichment consensus. g:Profiler gost
    (client-side BH-FDR) vs SymMap server-side enrichment (OR/P/FDR); terms are
    matched by name similarity and ranked by best (minimum) FDR across engines.
    Terms significant in BOTH engines are flagged as consensus hits - the
    strongest enrichment evidence.

    Args:
        params (MultiEnrichmentInput): genes + sources + limit.

    Returns:
        str: consensus terms + per-engine term lists with FDR.

    Examples:
        - "Enrich UC hub genes with both engines" ->
          genes=["TNF","IL10","IL12B","JAK2","STAT3","IFNG","IL1B"]
    '''
    try:
        genes = [g.rstrip(";").strip() for g in params.genes if g.strip()]
        gp_terms, sm_terms = [], []
        if "gprofiler" in params.sources:
            try:
                r = await _make_api_request(
                    GPROFILER_URL, method="POST",
                    json_body={"organism": "hsapiens", "query": genes,
                               "sources": ["KEGG"], "user_threshold": 0.05,
                               "significance_threshold_method": "g_SCS"},
                    headers={"Content-Type": "application/json"},
                    timeout=60.0, max_retries=1)
                r.raise_for_status()
                rows = (r.json() or {}).get("result") or []
                for row in rows:
                    # gost (g_SCS) returns its own corrected significance in
                    # p_value; use it directly (no double correction)
                    gp_terms.append({
                        "term": row.get("name"),
                        "term_id": row.get("native"),
                        "p_value": row.get("p_value"),
                        "fdr_gscs": row.get("p_value"),
                        "term_size": row.get("term_size"),
                        "intersection_size": row.get("intersection_size")})
                gp_terms.sort(key=lambda t: (t.get("fdr_gscs") is None,
                                             t.get("fdr_gscs") or 1.0))
            except Exception as e:
                gp_terms = [{"error": f"g:Profiler failed: {str(e)[:120]}"}]
        if "symmap" in params.sources:
            try:
                res = await _symmap_post("/related_enrichment/",
                                         {"genes": json.dumps({"gene": genes})},
                                         timeout=120.0)
                rows = (res.get("enrichment") or {}).get("data") or []
                for r in rows:
                    if not r:
                        continue
                    sm_terms.append({
                        "term": r[1], "term_id": r[0],
                        "p_value": float(r[3]) if len(r) > 3 else None,
                        "fdr_bh": float(r[4]) if len(r) > 4 else None,
                        "term_size": r[5] if len(r) > 5 else None,
                        "intersection_size": r[5] if len(r) > 5 else None})
                sm_terms.sort(key=lambda t: (t["fdr_bh"] is None,
                                             t["fdr_bh"] or 1.0))
            except Exception as e:
                sm_terms = [{"error": f"SymMap failed: {str(e)[:120]}"}]
        # name-similarity consensus matching
        def _norm(s):
            return set(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())
        consensus = []
        used = set()
        for t in sm_terms[:40]:
            nm = _norm(t.get("term"))
            if not nm:
                continue
            best, best_ov = None, 0.0
            for g in gp_terms[:40]:
                if id(g) in used or not g.get("term"):
                    continue
                ov = len(nm & _norm(g["term"])) / max(len(nm | _norm(g["term"])), 1)
                if ov > best_ov:
                    best, best_ov = g, ov
            if best is not None and best_ov >= 0.34:
                used.add(id(best))
                consensus.append({
                    "term": t.get("term"),
                    "symmap_fdr": t.get("fdr_bh"),
                    "gprofiler_fdr": round(best.get("fdr_gscs", 1.0) or 1.0, 6),
                    "best_fdr": min(t.get("fdr_bh") or 1.0,
                                    best.get("fdr_gscs") or 1.0),
                    "name_overlap": round(best_ov, 2)})
        consensus.sort(key=lambda c: c["best_fdr"])
        both_sig = [c for c in consensus
                    if (c["symmap_fdr"] or 1) < 0.05 and
                    (c["gprofiler_fdr"] or 1) < 0.05]
        return _fmt({
            "input_genes": len(genes), "engines": params.sources,
            "consensus_terms": consensus[:params.limit],
            "consensus_significant_both": len(both_sig),
            "gprofiler_terms": gp_terms[:params.limit],
            "symmap_terms": sm_terms[:params.limit],
            "note": "consensus = same term found by both engines "
                    "(Jaccard name overlap >= 0.34); best_fdr = min across "
                    "engines (g:Profiler value is its own g_SCS corrected p)",
            "source": "g:Profiler gost + SymMap v2.0 related_enrichment (live)",
            "queried_at": _now_iso()},
            params.response_format,
            f"Cross-engine enrichment ({len(genes)} genes, {len(consensus)} consensus)")
    except Exception as e:
        return _handle_api_error(e)


async def _wd_sparql(query: str, timeout: float = 60.0) -> Dict:
    '''POST a SPARQL query to Wikidata with retries (channel can reset).'''
    last = None
    for attempt in range(3):
        try:
            r = await _make_api_request(
                WD_SPARQL, method="POST",
                params={"query": query, "format": "json"},
                headers={"Accept": "application/sparql-results+json",
                         "Content-Type": "application/x-www-form-urlencoded"},
                timeout=timeout, max_retries=0)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            await asyncio.sleep(4.0 * (attempt + 1))
    raise RuntimeError(f"Wikidata SPARQL failed after 3 retries ({last}); "
                       f"the endpoint may be temporarily unreachable - retry later.")


class WikidataLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(..., description="search | resolve")
    query: str = Field(..., description="entity name (search) or QID (resolve), "
                                        "e.g. 'berberine' or 'Q409668'")
    limit: int = Field(5, ge=1, le=20)
    response_format: ResponseFormat = ResponseFormat.MARKDOWN


@mcp.tool(
    name="tcm_wikidata_lookup",
    annotations={"title": "Wikidata Cross-Database Identifier Resolution",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_wikidata_lookup(params: WikidataLookupInput) -> str:
    '''Resolve an entity into identifiers across databases via Wikidata.

    action=search: entity name -> Wikidata QIDs (with English description).
    action=resolve: QID -> external identifier matrix (CAS P231, InChIKey P235,
    PubChem CID P662, ChEMBL P592, KEGG P665, DrugBank P715, UniProt P352,
    NCBI Gene P351, PDB P638...). The bridge that links one database's entity
    to all others - key for cross-database data circulation.

    Channel note: Wikidata SPARQL occasionally resets connections; the tool
    retries 3x with backoff and reports a clear error if unreachable.

    Args:
        params (WikidataLookupInput): action + query + limit.

    Returns:
        str: QID hits or identifier matrix with property names.

    Examples:
        - "Find Wikidata QID for berberine" -> action=search, query=berberine
        - "Get all database IDs for Q409668" -> action=resolve, query=Q409668
    '''
    try:
        if params.action == "search":
            safe = params.query.replace('"', "'").strip()
            q = ('SELECT ?item ?itemLabel ?desc WHERE { '
                 f'?item rdfs:label "{safe}"@en . '
                 'OPTIONAL { ?item schema:description ?desc . '
                 'FILTER(LANG(?desc)="en") } '
                 'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } '
                 f'}} LIMIT {params.limit}')
            d = await _wd_sparql(q)
            rows = d.get("results", {}).get("bindings", [])
            hits = [{"qid": b["item"]["value"].rsplit("/", 1)[-1],
                     "label": b.get("itemLabel", {}).get("value", ""),
                     "description": b.get("desc", {}).get("value", "")}
                    for b in rows]
            return _fmt({"action": "search", "query": params.query,
                         "hits": hits,
                         "note": "use action=resolve with a QID to get the "
                                 "full identifier matrix",
                         "source": "Wikidata SPARQL", "queried_at": _now_iso()},
                        params.response_format,
                        f"Wikidata search: {params.query}")
        if params.action == "resolve":
            qid = params.query.strip()
            if not re.match(r"^Q\d+$", qid):
                return "Error: resolve requires a QID like Q409668 (use action=search first)."
            q = ('SELECT ?prop ?propLabel ?value WHERE { '
                 f'VALUES ?item {{ wd:{qid} }} '
                 '?item ?p ?value . ?prop wikibase:directClaim ?p . '
                 'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } '
                 'LIMIT 500')
            d = await _wd_sparql(q)
            rows = d.get("results", {}).get("bindings", [])
            ids = {}
            other = {}
            for b in rows:
                pnum = (b.get("prop", {}).get("value", "")).rsplit("/", 1)[-1]
                val = b.get("value", {}).get("value", "")
                plabel = b.get("propLabel", {}).get("value", pnum)
                if pnum in WD_PROPS:
                    ids.setdefault(WD_PROPS[pnum], []).append(val)
                elif pnum not in ("P31", "P279"):     # skip instance-of noise
                    other.setdefault(plabel, []).append(val)
            if not ids and not other:
                return _fmt({"action": "resolve", "qid": qid,
                             "identifiers": {}, "other_properties": {},
                             "note": "no statements found (or channel flaky)",
                             "source": "Wikidata SPARQL",
                             "queried_at": _now_iso()},
                            params.response_format, f"Wikidata resolve: {qid}")
            return _fmt({"action": "resolve", "qid": qid,
                         "identifiers": ids,
                         "other_properties": {k: v[:3] for k, v in
                                              list(other.items())[:10]},
                         "note": "identifier properties cover CAS/InChIKey/"
                                 "PubChem/ChEMBL/KEGG/DrugBank/UniProt/NCBI/PDB",
                         "source": "Wikidata SPARQL", "queried_at": _now_iso()},
                        params.response_format, f"Wikidata identifiers: {qid}")
        return "Error: action must be search|resolve."
    except Exception as e:
        return _handle_api_error(e)


class UniprotIdmapInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: List[str] = Field(..., min_length=1, max_length=50,
                           description='identifiers, e.g. ["P04637","P24941"]')
    from_db: str = Field("UniProtKB_AC-ID",
                         description="source: UniProtKB_AC-ID | ChEMBL | HGNC | "
                                     "Ensembl | RefSeq_Prot | PDB | KEGG ...")
    to_db: str = Field("ChEMBL",
                       description="target: ChEMBL | UniProtKB_AC-ID | Ensembl | "
                                   "PDB | RefSeq_Prot | HGNC ...")
    wait_seconds: int = Field(30, ge=5, le=90,
                              description="max polling time for the async job")
    response_format: ResponseFormat = ResponseFormat.MARKDOWN


@mcp.tool(
    name="tcm_uniprot_idmap",
    annotations={"title": "UniProt Cross-Database ID Mapping",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_uniprot_idmap(params: UniprotIdmapInput) -> str:
    '''Map identifiers between databases via the UniProt ID mapping service.

    The glue tool for data circulation: converts IDs between UniProt, ChEMBL,
    Ensembl, HGNC, PDB, RefSeq, KEGG and more (verified live: P04637->
    CHEMBL4096, P24941->CHEMBL301). Submit-and-poll async workflow with
    graceful timeout.

    Args:
        params (UniprotIdmapInput): ids + from_db + to_db + wait_seconds.

    Returns:
        str: mapping table (from -> to pairs) with failed IDs if any.

    Examples:
        - "Map UniProt P04637 to ChEMBL" ->
          ids=["P04637"], from_db="UniProtKB_AC-ID", to_db="ChEMBL"
    '''
    try:
        id_str = ",".join(i.strip() for i in params.ids if i.strip())
        if not id_str:
            return "Error: ids empty."
        r = await _make_api_request(
            f"{UNIPROT_IDMAP}/run", method="POST",
            data={"from": params.from_db, "to": params.to_db, "ids": id_str},
            timeout=20.0)
        r.raise_for_status()
        job = (r.json() or {}).get("jobId")
        if not job:
            return "Error: UniProt idmapping did not return a jobId."
        deadline = time.monotonic() + params.wait_seconds
        results, failed = None, []
        while time.monotonic() < deadline:
            await asyncio.sleep(3.0)
            s = await _make_api_request(f"{UNIPROT_IDMAP}/status/{job}",
                                        timeout=20.0)
            s.raise_for_status()
            d = s.json()
            if "results" in d:
                results = d["results"]
                failed = d.get("failedIds") or []
                break
            if (d.get("jobStatus") or "").startswith("ERROR"):
                return f"Error: idmapping job failed: {d.get('jobStatus')}."
        if results is None:
            return (f"Error: idmapping job still RUNNING after "
                    f"{params.wait_seconds}s (jobId={job}); retry with larger "
                    f"wait_seconds or poll https://rest.uniprot.org/idmapping/"
                    f"status/{job}")
        return _fmt({"from_db": params.from_db, "to_db": params.to_db,
                     "n_input": len(id_str.split(",")),
                     "n_mapped": len(results),
                     "mappings": results,
                     "failed_ids": failed,
                     "source": "UniProt ID mapping API", "queried_at": _now_iso()},
                    params.response_format,
                    f"ID mapping: {params.from_db} -> {params.to_db} "
                    f"({len(results)} mapped)")
    except Exception as e:
        return _handle_api_error(e)


# ================= 自迭代工具（2026-10-12 新增） =================

class ReportInsightInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(...,
                         description="dead_end | endpoint_change | quirk | "
                                     "performance | new_channel | methodology")
    title: str = Field(..., min_length=3, max_length=120,
                       description="one-line summary, e.g. "
                                   "'PubChem name lookup fails for CAS numbers'")
    detail: str = Field(..., min_length=3, max_length=2000,
                        description="what happens, when, under which conditions")
    evidence: str = Field("", max_length=2000,
                          description="concrete observation backing the insight "
                                      "(error message, latency numbers, URLs)")
    source_ref: str = Field("", max_length=300,
                            description="where you found it (doc section, paper, "
                                        "API response field)")


@mcp.tool(
    name="tcm_report_insight",
    annotations={"title": "Record a Runtime Insight (self-iteration memory)",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": False})
async def tcm_report_insight(params: ReportInsightInput) -> str:
    '''Persist a lesson learned at runtime into the skill's insight store.

    The self-iteration write path: whenever analysis of run results reveals a
    NEW fact (a dead endpoint, an API change, a parameter quirk, a performance
    pattern, a better channel), record it here. Insights are consulted by
    future runs (error hints) and by tcm_analyze_runtime (proposed updates to
    the skill's references). Recording the same title again increments its
    hit counter instead of duplicating.

    This is how the skill gets smarter on ANY agent host: local file store
    (~/.tcm_mcp_state/insights.json), no upload, survives restarts, and feeds
    the platform's Skill-evolution flow via tcm_analyze_runtime's
    proposed_updates.

    Args:
        params (ReportInsightInput): category + title + detail (+evidence,
        +source_ref).

    Returns:
        str: confirmation with insight id; "hit_count" when merged into an
        existing entry.

    Examples:
        - "SymMap related Gene->Mol timed out for hub gene NFKB1 twice" ->
          category="quirk", title="SymMap Gene->Mol 502 for hub genes",
          detail="...", evidence="HTTP 502 after 3 retries, 2026-10-12"
    '''
    try:
        cat = params.category.strip().lower()
        if cat not in _INSIGHT_CATS:
            return (f"Error: category must be one of {_INSIGHT_CATS} "
                    f"(got {params.category}).")
        async with _insights_lock:
            data = _load_insights()
            ins = data.setdefault("insights", [])
            for e in ins:
                if e.get("category") == cat and e.get("title") == params.title.strip():
                    e["hits"] = int(e.get("hits", 0)) + 1
                    e["last_hit"] = _now_iso()
                    if params.evidence:
                        e["evidence"] = params.evidence
                    _save_insights(data)
                    return _fmt({"action": "insight_merged",
                                 "id": e["id"], "category": cat,
                                 "title": e["title"],
                                 "hit_count": e["hits"],
                                 "note": "existing insight reinforced; its "
                                         "weight rises in analyze_runtime",
                                 "store": str(INSIGHTS_PATH),
                                 "queried_at": _now_iso()},
                                "markdown", f"Insight reinforced: {e['title'][:60]}")
            iid = "INS-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + \
                "-" + os.urandom(3).hex()
            entry = {"id": iid, "category": cat, "title": params.title.strip(),
                     "detail": params.detail.strip(),
                     "evidence": params.evidence.strip(),
                     "source_ref": params.source_ref.strip(),
                     "created_at": _now_iso(), "hits": 1, "last_hit": _now_iso()}
            ins.insert(0, entry)
            _save_insights(data)
        return _fmt({"action": "insight_recorded", "id": iid, "category": cat,
                     "title": entry["title"],
                     "store": str(INSIGHTS_PATH),
                     "note": "will be surfaced by tcm_analyze_runtime and "
                             "consulted as error hints for related hosts",
                     "queried_at": _now_iso()},
                    "markdown", f"Insight recorded: {entry['title'][:60]}")
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:200]}"


class AnalyzeRuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_events: int = Field(5000, ge=100, le=50000,
                            description="recent telemetry events to analyze")
    response_format: ResponseFormat = ResponseFormat.MARKDOWN


@mcp.tool(
    name="tcm_analyze_runtime",
    annotations={"title": "Analyze Runtime Telemetry + Insights (self-iteration)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_analyze_runtime(params: AnalyzeRuntimeInput) -> str:
    '''Analyze accumulated run results: channel health, dead-end candidates,
    latency regressions, recorded insights - and propose skill updates.

    The self-iteration read path. Aggregates the local flight recorder
    (~/.tcm_mcp_state/call_log.jsonl) per database host: call volume, success
    rate, average latency; flags dead-end candidates (>=5 calls, >=50% recent
    failures) and slow channels; merges recorded insights
    (tcm_report_insight), weighting by hit count. Output includes
    proposed_updates - concrete suggestions for SKILL.md / references changes
    that can be relayed into the platform's Skill-evolution flow (which needs
    user confirmation before applying).

    Run this periodically (or after long sessions) to let the skill learn from
    every agent's runs.

    Args:
        params (AnalyzeRuntimeInput): max_events window.

    Returns:
        str: per-host health table, dead-end candidates, top errors, insights,
        proposed_updates, totals.

    Examples:
        - "How healthy are our database channels? Any dead ends?" -> defaults
    '''
    try:
        events = []
        if CALL_LOG_PATH.exists():
            with open(CALL_LOG_PATH, encoding="utf-8") as f:
                lines = f.readlines()
            for ln in lines[-params.max_events:]:
                try:
                    events.append(json.loads(ln))
                except Exception:
                    continue
        per_host: Dict[str, Dict] = {}
        err_types: Dict[str, int] = {}
        for ev in events:
            if ev.get("type") != "db_call":
                continue
            h = ev.get("host") or "?"
            st = per_host.setdefault(h, {"calls": 0, "ok": 0, "fail": 0,
                                         "ms_total": 0.0, "ms_max": 0.0,
                                         "last": None})
            st["calls"] += 1
            ms = float(ev.get("ms") or 0)
            st["ms_total"] += ms
            st["ms_max"] = max(st["ms_max"], ms)
            st["last"] = ev.get("ts")
            if ev.get("ok"):
                st["ok"] += 1
            else:
                st["fail"] += 1
                err = (ev.get("error") or f"status {ev.get('status')}")
                key = err.split(" | ")[0][:60]
                err_types[key] = err_types.get(key, 0) + 1
        host_rows = []
        for h, st in sorted(per_host.items(), key=lambda kv: -kv[1]["calls"]):
            ok_rate = st["ok"] / st["calls"] if st["calls"] else 0
            host_rows.append({
                "host": h, "calls": st["calls"],
                "ok_rate": round(ok_rate, 3),
                "avg_ms": round(st["ms_total"] / st["calls"]) if st["calls"] else 0,
                "max_ms": round(st["ms_max"]),
                "last_seen": st["last"]})
        dead_ends = [r for r in host_rows
                     if r["calls"] >= 5 and r["ok_rate"] <= 0.5]
        slow = [r for r in host_rows if r["calls"] >= 3 and r["avg_ms"] > 15000]
        insights = _load_insights().get("insights", [])[:20]
        # proposed updates (for the platform Skill-evolution flow)
        proposals = []
        for r in dead_ends:
            proposals.append(
                f"[dead_end candidate] {r['host']}: {r['calls']} calls, "
                f"ok_rate {r['ok_rate']} -> verify manually; if confirmed, mark "
                f"as down in references/database_availability doc and suggest "
                f"alternatives (tcm_check_database_status lists substitutes)")
        for r in slow:
            proposals.append(
                f"[latency] {r['host']} avg {r['avg_ms']}ms -> consider raising "
                f"tool timeouts or documenting slow-channel expectations")
        for ins in insights[:10]:
            if ins["category"] in ("dead_end", "endpoint_change") and ins.get("hits", 1) >= 2:
                proposals.append(
                    f"[{ins['category']} x{ins['hits']}] {ins['title']} -> "
                    f"fold into references docs (see tcm_report_insight store)")
        if not proposals:
            proposals.append("no update proposals - all channels healthy")
        return _fmt({
            "state_dir": str(STATE_DIR),
            "events_analyzed": len(events),
            "channel_health": host_rows[:20],
            "dead_end_candidates": dead_ends,
            "slow_channels": slow,
            "top_errors": sorted(err_types.items(), key=lambda kv: -kv[1])[:10],
            "recorded_insights": insights,
            "proposed_updates": proposals,
            "iteration_note": "proposed_updates are suggestions - apply via the "
                              "platform's Skill-evolution flow (user-confirmed "
                              "staged update), or manually edit references; "
                              "insights with hits>=2 are strong candidates",
            "queried_at": _now_iso()},
            params.response_format,
            f"Runtime self-analysis ({len(events)} events, "
            f"{len(host_rows)} channels, {len(insights)} insights)")
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:200]}"


# ================= 入口 =================

def main():
    ap = argparse.ArgumentParser(description="open_tcm_discovery_mcp server")
    ap.add_argument("--port", type=int, default=8000, help="StreamableHTTP port (default 8000)")
    ap.add_argument("--stdio", action="store_true", help="Run with stdio transport instead of HTTP")
    args = ap.parse_args()

    if args.stdio:
        mcp.run()                       # stdio（本地子进程模式）
    else:
        # mcp 1.x 用 "streamable_http"，mcp 2.x 用 "streamable-http"；两者 kwargs 均转发 host/port
        try:
            mcp.run(transport="streamable-http", port=args.port, host="127.0.0.1")
        except ValueError:
            mcp.run(transport="streamable_http", port=args.port, host="127.0.0.1")


if __name__ == "__main__":
    main()
