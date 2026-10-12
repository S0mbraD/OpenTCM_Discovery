#!/usr/bin/env python3
"""smart_layer: intelligence-layer extension for the unified TCM skill.

Adds four capabilities on top of the transferred tool families:

  tcm_cache_status     - transparent SQLite response cache for read-only
                         retrieval tools (TTL-bounded, source-tagged)
  tcm_smart_run        - EXECUTION ENGINE for the dynamic analysis network:
                         turns a goal into a plan, then auto-runs the
                         automatable steps (data flows between steps), writes
                         checkpoints, auto-branches at decision points, and
                         pauses with explicit resume instructions whenever a
                         step needs human input (e.g. literature pocket
                         residues for docking)
  tcm_report_generate  - turns a session checkpoint into a review-ready
                         Markdown research brief (timeline, results, decision
                         log, evidence grading, full provenance)
  tcm_classify_structure - rule-based TCM chemical-class classifier for
                         SMILES (flavonoids / alkaloids / anthraquinones /
                         coumarins / lignans / terpenoids / steroids /
                         phenylpropanoids / organic acids / glycosides),
                         SMARTS via RDKit when available

The layer never fabricates data: every cached entry keeps the ORIGINAL
queried_at of the live retrieval, and every classification is labelled as a
rule-based prediction (evidence tier: computational prediction).
"""
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

from pydantic import BaseModel, Field, ConfigDict

mcp = _MCPBase("tcm_smart_layer")

STATE_ROOT = Path(os.environ.get(
    "TCM_MCP_STATE_DIR", str(Path.home() / ".tcm_mcp_state")))
CACHE_DB = STATE_ROOT / "cache.db"
SESSIONS_DIR = STATE_ROOT / "sessions"
CACHE_TTL = int(os.environ.get("TCM_CACHE_TTL", "3600"))
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# read-only retrieval tools eligible for transparent caching (no side
# effects, no telemetry-logging inside the wrapper)
CACHEABLE_TOOLS = {
    "tcm_pubchem_lookup", "tcm_kegg_compound", "tcm_chembl_lookup",
    "tcm_ensembl_lookup", "tcm_search_disease_targets", "tcm_string_ppi",
    "tcm_tcmbank_search", "tcm_ctd_gene_interactions", "tcm_get_gene_info",
    "tcm_ncbi_gene_search", "tcm_enrichment_analysis",
    "tcm_npass_search_compound", "tcm_get_structure",
    "tcm_dgidb_interactions", "tcm_gwas_associations",
    "tcm_symmap_lookup", "tcm_kegg_disease_pathways",
    "tcm_wikidata_lookup", "tcm_uniprot_idmap",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ================= transparent response cache =================

def _cache_connect() -> sqlite3.Connection:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CACHE_DB), timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS responses ("
        "tool TEXT NOT NULL, key TEXT NOT NULL, response TEXT NOT NULL, "
        "created_at REAL NOT NULL, PRIMARY KEY (tool, key))")
    conn.commit()
    return conn


def _cache_key(params: Dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, ensure_ascii=False,
                         default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_get(tool: str, params: Dict[str, Any]) -> Optional[str]:
    try:
        with _cache_connect() as conn:
            row = conn.execute(
                "SELECT response, created_at FROM responses "
                "WHERE tool = ? AND key = ?", (tool, _cache_key(params))
            ).fetchone()
        if not row:
            return None
        response, created = row
        age = time.time() - created
        if age > CACHE_TTL:
            return None
        note = (f"\n[cache hit: age {int(age // 60)} min, ttl "
                f"{CACHE_TTL // 60} min; original retrieval time preserved "
                f"inside the response]")
        return response + note
    except sqlite3.Error:
        return None


def cache_put(tool: str, params: Dict[str, Any], response: str) -> None:
    try:
        with _cache_connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO responses "
                "(tool, key, response, created_at) VALUES (?, ?, ?, ?)",
                (tool, _cache_key(params), response, time.time()))
    except sqlite3.Error:
        pass  # cache is best-effort; retrieval must never fail on it


def cache_stats() -> Dict[str, Any]:
    try:
        with _cache_connect() as conn:
            rows = conn.execute(
                "SELECT tool, COUNT(*), MIN(created_at), MAX(created_at) "
                "FROM responses GROUP BY tool ORDER BY COUNT(*) DESC"
            ).fetchall()
        return {
            "db": str(CACHE_DB),
            "ttl_seconds": CACHE_TTL,
            "tools": [
                {"tool": r[0], "entries": r[1],
                 "oldest": datetime.fromtimestamp(r[2], timezone.utc)
                 .strftime("%Y-%m-%d %H:%M UTC") if r[2] else None,
                 "newest": datetime.fromtimestamp(r[3], timezone.utc)
                 .strftime("%Y-%m-%d %H:%M UTC") if r[3] else None}
                for r in rows],
            "total_entries": sum(r[1] for r in rows)}
    except sqlite3.Error as e:
        return {"error": str(e)}


def cache_clear() -> str:
    try:
        with _cache_connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
            conn.execute("DELETE FROM responses")
        return f"cleared {n} cache entries"
    except sqlite3.Error as e:
        return f"Error: {e}"


def make_cached_wrapper(name: str, fn: Callable) -> Callable:
    """Wrap an async tool fn with a transparent TTL cache.

    The wrapper keeps the ORIGINAL function signature (functools.wraps +
    __wrapped__), so the transferred tool schema is unchanged; only the
    runtime behavior gains a cache short-circuit.
    """
    import functools

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        # normalise params: tools take a single pydantic 'params' argument
        if args and args[0].__class__.__name__.endswith("Input"):
            key_dict = {"params": args[0].model_dump()}
        else:
            key_dict = dict(kwargs)
            if "params" in key_dict and hasattr(key_dict["params"],
                                                "model_dump"):
                key_dict["params"] = key_dict["params"].model_dump()
        hit = cache_get(name, key_dict)
        if hit is not None:
            return hit
        result = await fn(*args, **kwargs)
        if isinstance(result, str) and not result.startswith("Error"):
            cache_put(name, key_dict, result)
        return result

    return wrapper


# ================= tool 3: tcm_cache_status =================

class CacheStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field("status", description="status | clear")
    response_format: str = Field("markdown",
                                 description="markdown | json")


@mcp.tool(
    name="tcm_cache_status",
    annotations={"title": "Retrieval Response Cache: status / clear",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_cache_status(params: CacheStatusInput) -> str:
    '''Inspect or clear the transparent retrieval-response cache.

    Read-only retrieval tools are cached with a TTL (default 3600 s,
    override with env TCM_CACHE_TTL). Cache hits preserve the ORIGINAL
    retrieval timestamp inside the response (real-time-retrieval discipline:
    cached entries are past live retrievals, never pre-seeded answers).

    Args:
        params (CacheStatusInput): action="status" (default) or "clear".

    Returns:
        str: per-tool entry counts and TTL, or the clear result.
    '''
    if params.action == "clear":
        return cache_clear()
    data = cache_stats()
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = ["# Retrieval response cache", ""]
    lines.append(f"- **db**: {data.get('db')}")
    lines.append(f"- **ttl**: {CACHE_TTL} s ({CACHE_TTL // 60} min)")
    lines.append(f"- **total_entries**: {data.get('total_entries')}")
    lines.append("")
    lines.append("## cached tools")
    for t in data.get("tools", []):
        lines.append(f"- {t['tool']}: {t['entries']} entries "
                     f"(newest {t['newest']})")
    lines.append("")
    lines.append("## discipline")
    lines.append("- cache = past LIVE retrievals only, TTL-bounded, "
                 "original queried_at preserved in-body")
    lines.append("- non-2xx / Error responses are never cached")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)


# ================= tool 4: tcm_classify_structure =================
# Rule-based TCM chemical-class classifier (SMARTS via RDKit if available).
# Every output is labelled a rule-based PREDICTION (computational tier),
# never experimental evidence.

try:
    from rdkit import Chem
    from rdkit.Chem import rdMolDescriptors
    _HAS_RDKIT = True
except ImportError:
    _HAS_RDKIT = False

# (class, subclass, SMARTS, basis note) - evaluated in order, first match wins
# NOTE: RDKit aromatizes conjugated heterocycles (flavonoid chromone ring,
# coumarin pyrone ring), so each core needs an aromatic AND a kekule/saturated
# pattern - all patterns below validated against real PubChem structures.
_SMARTS_RULES: List[tuple] = [
    ("anthraquinone", "蒽醌类",
     "O=C1c2ccccc2C(=O)c3ccccc13",
     "9,10-anthraquinone core (two carbonyls fused to anthracene)"),
    ("flavonoid", "黄酮类",
     "O=c1ccoc2ccccc12",
     "aromatized chromen-4-one core; C6-C3-C6 skeleton"),
    ("flavonoid", "二氢黄酮类",
     "O=C1CCc2ccccc2O1",
     "saturated chroman-4-one core (dihydroflavonoid)"),
    ("flavonoid", "黄酮类(kekule)",
     "O=C1C=Cc2ccccc2O1",
     "kekule chromen-4-one core"),
    ("coumarin", "香豆素类",
     "O=c1ccc2ccccc2o1",
     "aromatized benzo-alpha-pyrone (2H-chromen-2-one) core"),
    ("coumarin", "二氢香豆素类",
     "O=C1Oc2ccccc2CC1",
     "saturated 3,4-dihydrocoumarin core"),
    ("alkaloid_purine", "生物碱-嘌呤类",
     "c1ncnc2ncnc12",
     "purine fused ring with ring nitrogens"),
    ("terpene_lactone", "萜类内酯",
     "O=C1OC(C)C2CCC(C)C1C2",
     "sesquiterpene lactone gamma-lactone pattern"),
]

# ring-nitrogen alkaloid SMARTS (excludes amides in open chains)
_ALKALOID_SMARTS = [
    "[nR]1[nR][nR][nR][nR]1",   # imidazole/triazole etc fused
    "[nR]1ccccc1",              # pyridine-type aromatic N
    "[NR]1CCNCC1",              # saturated cyclic amine
    "[nR]1cccc1",               # pyrrole-type
    "[NR]3[R][R][R][R]3",       # generic ring N
]

_GLYCOSIDE_SMARTS = [
    "OC1COC(O)C(O)C1O",         # pyranose with OH pattern
    "OC1COC(O)C(O)CO1",         # furanose
]

_PHENYLPROPANE_SMARTS = "c1ccc(C=C)cc1"          # C6-C3 skeleton
_CARBOXYLIC_SMARTS = "C(=O)O"                    # organic acid
_TERPENE_C_COUNTS = {10: "monoterpene 单萜", 15: "sesquiterpene 倍半萜",
                     20: "diterpene 二萜", 25: "sesterterpene 二倍半萜",
                     30: "triterpene 三萜", 40: "tetraterpene 四萜"}


def _classify_with_rdkit(smiles: str) -> Optional[Dict[str, Any]]:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    n_carbon = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 6)
    ring_info = mol.GetRingInfo()
    n_rings = ring_info.NumRings()
    formula = rdMolDescriptors.CalcMolFormula(mol)
    mw = rdMolDescriptors.CalcExactMolWt(mol)

    matched = []
    for cls, cls_cn, smarts, basis in _SMARTS_RULES:
        patt = Chem.MolFromSmarts(smarts)
        if patt and mol.HasSubstructMatch(patt):
            matched.append((cls, cls_cn, basis))
    # flavonoid refinement: chromone + >=2 benzene rings
    benz = Chem.MolFromSmarts("c1ccccc1")
    n_benz = len(mol.GetSubstructMatches(benz)) if benz else 0
    if matched:
        cls, cls_cn, basis = matched[0]
        if cls == "flavonoid" and n_benz >= 2:
            cls_cn = "黄酮类（含2-苯基色原酮骨架）"
            basis += f"; {n_benz} aromatic rings detected"
        return {"class": cls, "class_cn": cls_cn, "basis": basis,
                "formula": formula, "mw": round(mw, 2),
                "n_carbons": n_carbon, "n_rings": n_rings}
    # alkaloid: ring nitrogen, not a plain amide
    for sma in _ALKALOID_SMARTS:
        patt = Chem.MolFromSmarts(sma)
        if patt and mol.HasSubstructMatch(patt):
            return {"class": "alkaloid", "class_cn": "生物碱类",
                    "basis": f"cyclic nitrogen pattern {sma}",
                    "formula": formula, "mw": round(mw, 2),
                    "n_carbons": n_carbon, "n_rings": n_rings}
    # glycoside / sugar
    for sma in _GLYCOSIDE_SMARTS:
        patt = Chem.MolFromSmarts(sma)
        if patt and mol.HasSubstructMatch(patt):
            return {"class": "glycoside", "class_cn": "糖苷/糖类",
                    "basis": "pyranose/furanose ring with poly-OH pattern",
                    "formula": formula, "mw": round(mw, 2),
                    "n_carbons": n_carbon, "n_rings": n_rings}
    # steroid before terpene-carbon-count: C26-C30 with >=4 fused rings and
    # no ring heteroatoms (validated on cholesterol C27/4 rings)
    ring_hetero = any(a.GetIsAromatic() and a.GetSymbol() in "NO"
                      and a.IsInRing() for a in mol.GetAtoms())
    if 26 <= n_carbon <= 30 and n_rings >= 4 and not ring_hetero:
        return {"class": "steroid",
                "class_cn": "甾体类（骨架近似）",
                "basis": f"C{n_carbon} with {n_rings} fused rings, no ring "
                         "heteroatoms (count-based heuristic)",
                "formula": formula, "mw": round(mw, 2),
                "n_carbons": n_carbon, "n_rings": n_rings,
                "confidence": "low"}
    # terpene by carbon count + ring count
    if n_carbon in _TERPENE_C_COUNTS and n_rings >= 1:
        return {"class": "terpene",
                "class_cn": f"萜类（{_TERPENE_C_COUNTS[n_carbon]}）",
                "basis": f"C{n_carbon} isoprene-count skeleton, "
                         f"{n_rings} rings (count-based heuristic)",
                "formula": formula, "mw": round(mw, 2),
                "n_carbons": n_carbon, "n_rings": n_rings,
                "confidence": "medium"}
    # phenylpropanoid
    patt = Chem.MolFromSmarts(_PHENYLPROPANE_SMARTS)
    if patt and mol.HasSubstructMatch(patt):
        return {"class": "phenylpropanoid", "class_cn": "苯丙素类",
                "basis": "C6-C3 skeleton (benzene + propenyl)",
                "formula": formula, "mw": round(mw, 2),
                "n_carbons": n_carbon, "n_rings": n_rings,
                "confidence": "medium"}
    # organic acid
    patt = Chem.MolFromSmarts(_CARBOXYLIC_SMARTS)
    if patt and mol.HasSubstructMatch(patt):
        return {"class": "organic_acid", "class_cn": "有机酸类",
                "basis": "carboxylic acid group",
                "formula": formula, "mw": round(mw, 2),
                "n_carbons": n_carbon, "n_rings": n_rings}
    return {"class": "unclassified", "class_cn": "未归类",
            "basis": "no rule matched; manual classification needed",
            "formula": formula, "mw": round(mw, 2),
            "n_carbons": n_carbon, "n_rings": n_rings,
            "confidence": "none"}


def _classify_fallback(smiles: str) -> Dict[str, Any]:
    """String-heuristic fallback when RDKit is unavailable (low confidence)."""
    s = smiles.lower()
    rules = [
        ("anthraquinone", "蒽醌类", "c(=o)c" in s and s.count("c(=o)c") >= 2
         and s.count("c1ccc") >= 2),
        ("flavonoid", "黄酮类", "c1ccc(" in s and "o1" in s
         and "=o" in s and s.count("c1ccc") >= 2),
        ("coumarin", "香豆素类", "o=c1oc" in s.replace(" ", "")),
        ("alkaloid", "生物碱类", ("n1" in s or "n2" in s or "n3" in s)
         and "c(=o)n" not in s),
        ("organic_acid", "有机酸类", "c(=o)o" in s),
    ]
    for cls, cls_cn, ok in rules:
        if ok:
            return {"class": cls, "class_cn": cls_cn,
                    "basis": "string heuristic (RDKit unavailable)",
                    "confidence": "low"}
    return {"class": "unclassified", "class_cn": "未归类",
            "basis": "string heuristic found no match",
            "confidence": "none"}


class ClassifyStructureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smiles: Optional[str] = Field(
        None, description="SMILES to classify (mutually exclusive with name)")
    name: Optional[str] = Field(
        None, description="PubChem compound name: SMILES is looked up live "
                          "first, then classified")
    label: Optional[str] = Field(None, description="optional display label "
        "(e.g. compound name for the report)")
    response_format: str = Field("markdown", description="markdown | json")


@mcp.tool(
    name="tcm_classify_structure",
    annotations={"title": "Rule-based TCM chemical-class classifier",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_classify_structure(params: ClassifyStructureInput) -> str:
    '''Classify a compound into TCM chemical classes (rule-based).

    Covers the classic TCM chemistry taxonomy: anthraquinones, flavonoids,
    coumarins, alkaloids, terpenoids (C10/C15/C20/C30 skeletons), steroids,
    lignans/phenylpropanoids, organic acids, glycosides. Uses SMARTS
    substructure matching (RDKit) with count-based heuristics as fallback.

    IMPORTANT: this is a RULE-BASED PREDICTION (computational evidence
    tier). It supports dedup/classification bookkeeping; it does not replace
    spectroscopy or literature classification. Always cross-check against
    NPASS/TCMSP class fields when available.

    Args:
        params (ClassifyStructureInput): smiles OR name (name triggers a
            live PubChem SMILES lookup first).

    Returns:
        str: class (EN + CN), basis for the match, formula/MW when RDKit
        is available, confidence, and the prediction disclaimer.

    Examples:
        - "Classify quercetin" -> name="quercetin"
        - "What class is O=C1C=Cc2ccccc2O1c3ccccc3?" -> smiles="..."
    '''
    if not params.smiles and not params.name:
        return "Error: provide either smiles or name."
    if params.smiles and params.name:
        return "Error: provide only ONE of smiles or name."
    label = params.label or params.name or "input structure"
    provenance = []
    smiles = params.smiles
    if params.name:
        # live PubChem lookup for canonical SMILES (real-time discipline)
        import httpx
        url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
               f"{params.name}/property/"
               "CanonicalSMILES,ConnectivitySMILES,SMILES/JSON")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(url)
            if r.status_code == 404:
                return (f"No compound found for name '{params.name}' "
                        "in PubChem.")
            r.raise_for_status()
            props = (r.json().get("PropertyTable", {})
                     .get("Properties", [{}])[0])
            smiles = None
            smi_field = None
            for field in ("CanonicalSMILES", "ConnectivitySMILES",
                          "SMILES"):
                if props.get(field):
                    smiles = props[field]
                    smi_field = field
                    break
            provenance.append(
                {"field": smi_field or "SMILES",
                 "source": "PubChem PUG-REST",
                 "queried_at": _now_iso()})
        except Exception as e:
            return f"Error: PubChem lookup failed: {e}"
    if not smiles:
        return "Error: no SMILES available."

    if _HAS_RDKIT:
        result = _classify_with_rdkit(smiles)
        if result is None:
            return (f"Error: RDKit could not parse the SMILES: {smiles}")
        result["engine"] = "rdkit SMARTS"
    else:
        result = _classify_fallback(smiles)
        result["engine"] = "string heuristics"
    result.update({
        "label": label, "smiles": smiles,
        "evidence_tier": "computational prediction (rule-based)",
        "disclaimer": "rule-based prediction; cross-check NPASS/TCMSP "
                      "class fields or literature before publication",
        "provenance": provenance,
        "queried_at": _now_iso()})
    if params.response_format == "json":
        return json.dumps(result, ensure_ascii=False, indent=2,
                          default=str)
    lines = [f"# Structure classification: {label}", ""]
    lines.append(f"- **class**: {result['class']} / {result.get('class_cn')}")
    if result.get("formula"):
        lines.append(f"- **formula**: {result['formula']}")
    if result.get("mw"):
        lines.append(f"- **MW**: {result.get('mw')}")
    lines.append(f"- **basis**: {result['basis']}")
    lines.append(f"- **engine**: {result['engine']}")
    lines.append(f"- **confidence**: {result.get('confidence', 'high')}")
    lines.append(f"- **smiles**: {smiles}")
    lines.append("")
    lines.append("**DISCLAIMER**: rule-based prediction (computational "
                 "evidence tier); cross-check NPASS/TCMSP or literature "
                 "before publication.")
    for p in provenance:
        lines.append(f"- provenance: {p['field']} from {p['source']} "
                     f"at {p['queried_at']}")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)


# ================= tool 5: tcm_smart_run (execution engine) =================

# attached by unified_mcp after tool transfer: {tool_name: fn}
_FNS: Dict[str, Callable] = {}


def attach_tools(fn_map: Dict[str, Callable]) -> None:
    _FNS.update(fn_map)


def _parse_json_output(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort parse of a tool's JSON-mode output."""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        # markdown fallback: pull the first top-level '# ' section headers
        return None


async def _invoke(tool: str, timeout: float, **kwargs) -> Dict[str, Any]:
    """Call an attached tool fn with a params wrapper, JSON mode preferred."""
    fn = _FNS.get(tool)
    if fn is None:
        return {"status": "error", "error": f"tool {tool} not available"}
    import inspect
    try:
        ann = inspect.signature(fn).parameters["params"].annotation
    except (KeyError, TypeError):
        ann = None
    call_kwargs = dict(kwargs)
    # prefer JSON responses for machine parsing
    try:
        if ann is not None and "response_format" in ann.model_fields:
            call_kwargs.setdefault("response_format", "json")
    except AttributeError:
        pass
    try:
        obj = ann(**call_kwargs) if ann is not None else None
        if obj is not None:
            raw = await asyncio.wait_for(fn(params=obj), timeout=timeout)
        else:
            raw = await asyncio.wait_for(fn(**call_kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "timeout",
                "error": f"{tool} exceeded {timeout:.0f}s"}
    except Exception as e:
        return {"status": "error", "error": f"{tool}: {type(e).__name__}: {e}"}
    parsed = _parse_json_output(raw) if isinstance(raw, str) else None
    if parsed is not None:
        parsed.setdefault("status", "ok")
        return parsed
    if isinstance(raw, str) and raw.startswith("Error"):
        return {"status": "error", "error": raw[:500]}
    return {"status": "ok", "markdown": str(raw)[:4000]}


def _builtin_vina() -> Optional[str]:
    """Skill-bundled vina: <skill>/bin/vina.exe (self-contained)."""
    p = Path(__file__).resolve().parent.parent / "bin" / "vina.exe"
    return str(p) if p.exists() else None


def _resolve_vina(explicit: Optional[str]) -> Optional[str]:
    """explicit > VINA_EXE env > skill-bundled bin/vina.exe."""
    if explicit and Path(explicit).exists():
        return explicit
    env = os.environ.get("VINA_EXE")
    if env and Path(env).exists():
        return env
    return _builtin_vina()


class SmartRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: Optional[str] = Field(None, max_length=1000,
                                description="natural language research goal")
    disease: Optional[str] = Field(None, max_length=200)
    compounds: Optional[List[str]] = Field(None, max_length=20)
    genes: Optional[List[str]] = Field(None, max_length=50)
    session_id: Optional[str] = Field(
        None, description="RESUME an existing session (checkpointed)")
    docking_inputs: Optional[Dict[str, Any]] = Field(
        None, description="docking prerequisites for autonomous execution: "
                          "receptor_pdb, ligand (name/CID), pocket_chain, "
                          "pocket_residues (MUST be from literature), "
                          "exhaustiveness, seed, vina_path")
    depth: str = Field(
        "standard",
        description="fast | standard | full. full does not run the 30-60 min "
                    "herb workflow inside this tool; it records deferred "
                    "step D3 and the exact script command")
    max_runtime: int = Field(1800, ge=60, le=7200,
                             description="overall wall-clock budget (s)")


def _session_path(sid: str) -> Path:
    return SESSIONS_DIR / f"{sid}.json"


def _atomic_write_json(path: Path, data: Dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                              default=str), encoding="utf-8")
    os.replace(tmp, path)


class _Engine:
    """Drives the dynamic analysis network for one session."""

    def __init__(self, state: Dict):
        self.s = state

    # -- step bookkeeping -------------------------------------------------
    def done(self, step: int, tool: str, module: str, result: Dict,
             summary: str, decision: str = "") -> None:
        self.s["executed"].append({
            "step": step, "module": module, "tool": tool,
            "status": result.get("status", "ok"),
            "summary": summary, "decision": decision,
            "result": {k: v for k, v in result.items()
                       if k not in ("markdown",)},
            "raw_excerpt": (result.get("markdown") or "")[:600],
            "source": result.get("source"),
            "queried_at": result.get("queried_at", _now_iso()),
            "finished_at": _now_iso()})

    def save(self) -> None:
        self.s["updated_at"] = _now_iso()
        _atomic_write_json(_session_path(self.s["session_id"]), self.s)

    # -- extraction helpers -----------------------------------------------
    @staticmethod
    def _genes_from_pathways(res: Dict, limit: int = 40) -> List[str]:
        for row in res.get("pathways", []):
            genes = row.get("related_genes")
            if isinstance(genes, list) and genes:
                return [g for g in genes if isinstance(g, str)][:limit]
            if isinstance(genes, str) and genes:
                parts = re.split(r"[;,]", genes)
                return [p.strip() for p in parts if p.strip()][:limit]
        return []

    @staticmethod
    def _hubs(res: Dict, limit: int = 20) -> List[str]:
        return [h.get("gene") for h in res.get("top_hubs", [])
                if h.get("gene")][:limit]

    @staticmethod
    def _consensus_hits(res: Dict) -> List[str]:
        rows = (res.get("consensus_ranking") or res.get("results")
                or res.get("entities") or [])
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ch = row.get("consensus_hits", row.get("consensus"))
            if ch is not None:
                out.append(f"{row.get('entity', '?')}: {ch} hits")
            elif row.get("error"):
                out.append(f"{row.get('entity', '?')}: error")
        return out

    # -- decision points (auto-branching) ----------------------------------
    def _d1_branch(self, genes: List[str]) -> tuple:
        if len(genes) >= 15:
            return (f"D1: {len(genes)} genes >= 15 -> PPI network then "
                    "enrichment on hubs", "network")
        return (f"D1: {len(genes)} genes < 15 -> enrich the gene list "
                "directly, skip network", "direct")


# common TCM active-ingredient names for goal-text extraction (lowercase;
# matched as substrings in the goal; verified downstream by live lookups)
_KNOWN_COMPOUNDS = ("berberine", "baicalin", "quercetin", "wogonin",
                    "emodin", "chrysophanol", "curcumin", "artemisinin",
                    "paclitaxel", "ginsenoside", "astragaloside",
                    "salidroside", "glycyrrhizin", "paeoniflorin",
                    "tanshinone", "ferulic acid", "genistein",
                    "kaempferol", "rutin", "luteolin", "apigenin",
                    "catechin", "epigallocatechin", "silibinin",
                    "triptolide", "celastrol", "baicalein")


def _defer_full_herb_workflow(engine: "_Engine", disease: Optional[str]) -> None:
    """depth=full asks for the herb screen; the live engine only records it.

    disease_pathway_workflow.py run-all is a 30-60 minute checkpointed
    crawl. Running it inside this request would blow the tool budget and
    hide a long job. The session instead keeps an explicit deferred command.
    """
    depth = (engine.s.get("intent") or {}).get("depth", "standard")
    if depth != "full" or not disease:
        return
    if any(d.get("point") == "D3" for d in engine.s.get("decisions") or []):
        return
    cmd = (
        "python scripts/disease_pathway_workflow.py run-all "
        f'--disease "{disease}" --out <workdir>'
    )
    engine.s.setdefault("deferred_scripts", []).append({
        "id": "m1_four_step",
        "module": "M1",
        "status": "deferred",
        "reason": "30-60 min SymMap crawl with checkpoints; not run inside "
                  "tcm_smart_run",
        "command": cmd,
    })
    engine.s.setdefault("decisions", []).append({
        "point": "D3",
        "rule": "depth=full: herb -> OB ingredients -> hypergeometric FDR "
                f"is deferred. Run: {cmd}. Resume afterwards with the "
                "OB-filtered compounds.",
    })
    engine.save()


def _extract_compounds(goal: str) -> List[str]:
    """Extract known compound mentions from free-text goal (quick
    intent-level extraction; entities still verified live downstream)."""
    g = (goal or "").lower()
    return [c for c in _KNOWN_COMPOUNDS if c in g][:6]


async def run_session(params: SmartRunInput) -> str:
    t_start = time.time()
    # ---- resume or create -------------------------------------------------
    if params.session_id:
        p = _session_path(params.session_id)
        if not p.exists():
            return (f"Error: session '{params.session_id}' not found in "
                    f"{SESSIONS_DIR}")
        state = json.loads(p.read_text(encoding="utf-8"))
        if params.docking_inputs:
            state["docking_inputs"] = params.docking_inputs
        if params.depth == "full":
            state.setdefault("intent", {})["depth"] = "full"
        engine = _Engine(state)
        paused = state.get("paused_at")
        first_step = (paused["step"] if paused else
                      len(state.get("executed", [])) + 1)
    else:
        g = (params.goal or "").lower()
        has_disease = bool(params.disease) or any(
            k in g for k in ("disease", "colitis", "uc", "通路", "疾病",
                             "结肠炎", "溃疡", "cancer", "diabetes", "靶点筛选",
                             "inflammatory", "ibd"))
        has_comp = bool(params.compounds) or bool(_extract_compounds(
            params.goal)) or any(
            k in g for k in ("compound", "成分", "化合物", "berberine",
                             "quercetin", "分子库"))
        has_genes = bool(params.genes)
        want_dock = any(k in g for k in ("dock", "对接", "口袋", "结合",
                                         "binding"))
        if not any((has_disease, has_comp, has_genes, want_dock)):
            return ("Error: no recognizable intent; provide goal and/or "
                    "disease/compounds/genes/docking context.")
        sid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + \
            uuid.uuid4().hex[:6]
        extracted = _extract_compounds(params.goal)
        state = {
            "session_id": sid, "goal": params.goal,
            "disease": params.disease,
            "compounds": params.compounds or extracted,
            "genes": params.genes or [],
            "intent": {"disease": has_disease, "compounds": has_comp,
                       "genes": has_genes, "docking": want_dock,
                       "depth": params.depth},
            "docking_inputs": params.docking_inputs or {},
            "executed": [], "decisions": [], "status": "running",
            "created_at": _now_iso()}
        engine = _Engine(state)
        first_step = 1

    disease = state.get("disease") or _guess_disease(params.goal or "")
    budget = params.max_runtime
    _defer_full_herb_workflow(engine, disease)

    def step_done() -> bool:
        return (time.time() - t_start) > budget

    # ---- step 1: disease -> pathways (M1) ---------------------------------
    if first_step <= 1 and disease:
        res = await _invoke("tcm_kegg_disease_pathways", 240,
                            disease_name=disease, use_enrichment=True)
        n = res.get("n_pathways", "?")
        engine.done(1, "tcm_kegg_disease_pathways", "M1", res,
                    f"{n} pathways for '{disease}' "
                    f"(top: {(res.get('pathways') or [{}])[0].get('pathway_name')})")
        engine.save()
        genes = engine._genes_from_pathways(res)
        if not genes:
            genes = state.get("genes") or []
    else:
        genes = state.get("_last_genes") or state.get("genes") or []

    # ---- steps 2-3: network + enrichment (M3) with D1 auto-branch ----------
    if first_step <= 2 and genes:
        note, branch = engine._d1_branch(genes)
        engine.s["decisions"].append({"point": "D1", "rule": note})
        if branch == "network":
            res = await _invoke("tcm_network_analysis", 240, genes=genes)
            hubs = engine._hubs(res)
            engine.done(2, "tcm_network_analysis", "M3", res,
                         f"PPI {res.get('nodes', '?')} nodes / "
                         f"{res.get('edges', '?')} edges; hubs: "
                         f"{', '.join(hubs[:8])}")
            engine.save()
            enrich_genes = hubs or genes[:20]
        else:
            engine.done(2, "(skipped: D1 direct branch)", "M3",
                        {"status": "ok"}, note)
            engine.save()
            enrich_genes = genes[:20]
        if enrich_genes:
            res = await _invoke("tcm_multi_enrichment", 240,
                                genes=enrich_genes)
            cons = (res.get("consensus_terms") or
                    res.get("consensus_pathways") or [])[:5]
            engine.done(3, "tcm_multi_enrichment", "M3", res,
                        f"dual-engine consensus terms: "
                        f"{json.dumps(cons, ensure_ascii=False)[:300]}")
            engine.s["_last_genes"] = enrich_genes
            engine.save()

    # ---- step 4: hub cross-database evidence (M2) ---------------------------
    hubs_avail = (state.get("_last_genes") or genes or [])[:10]
    if first_step <= 4 and hubs_avail:
        res = await _invoke("tcm_cross_database_screen", 300,
                            mode="gene", entities=hubs_avail, concurrency=3)
        hits = engine._consensus_hits(res)
        engine.done(4, "tcm_cross_database_screen", "M2", res,
                    f"gene evidence: {('; '.join(hits[:6])) or 'see raw'}")
        tclin = [h for h in hits if "tclin" in h.lower() or
                 "tchem" in h.lower()]
        if tclin:
            engine.s["decisions"].append({
                "point": "D2",
                "rule": f"Tclin/Tchem priority targets: "
                        f"{'; '.join(tclin[:5])}"})
        engine.save()

    # ---- step 5: compound consensus (M2) ------------------------------------
    comps = state.get("compounds") or []
    if first_step <= 5 and comps:
        res = await _invoke("tcm_cross_database_screen", 300,
                            mode="compound", entities=comps, concurrency=3)
        hits = engine._consensus_hits(res)
        engine.done(5, "tcm_cross_database_screen", "M2", res,
                    f"compound consensus: {('; '.join(hits)) or 'see raw'}")
        engine.s["decisions"].append({
            "point": "D4",
            "rule": "compounds with consensus >= 2/3 advance to docking; "
                    "others need name/CID verification first"})
        engine.save()

    # ---- step 5.1: ADMET approximation panel (P1) --------------------------
    if first_step <= 5.1 and comps:
        res = await _invoke("tcm_admet_predict", 300, compounds=comps[:6])
        panel = res.get("panel") or []
        n_ok = sum(1 for r in panel if r.get("status") == "ok")
        esols = [f"{r.get('compound')}: {r.get('esol_log_mol_l')}"
                 for r in panel if r.get("esol_log_mol_l") is not None][:4]
        engine.done(5.1, "tcm_admet_predict", "P1", res,
                    f"local ADMET approximations for {n_ok}/{len(panel)} "
                    f"compounds (ESOL: {'; '.join(esols) or 'n/a'})")
        engine.save()

    # ---- step 5.2: literature co-occurrence (P0) -----------------------------
    hub_genes = (state.get("_last_genes") or genes or [])[:3]
    if first_step <= 5.2 and disease and (comps or hub_genes):
        comp_for_lit = comps[0] if comps else None
        if comp_for_lit:
            res = await _invoke(
                "tcm_literature_mine", 300, compound=comp_for_lit,
                genes=hub_genes[:3] or None, disease=disease,
                yearly_trend=False)
            counts = (res.get("counts") or {})
            ft = counts.get("full_triple")
            engine.done(5.2, "tcm_literature_mine", "P0", res,
                        f"PubMed co-occurrence for {comp_for_lit}: "
                        f"full_triple={ft}, "
                        f"compound_disease={counts.get('compound_disease')}"
                        f" (bibliometric signal, not causal proof)")
            engine.save()

    # ---- step 5.3: clinical trials context (P2) ------------------------------
    if first_step <= 5.3 and disease and hub_genes:
        res = await _invoke(
            "tcm_trials_match", 180, condition=disease,
            target_or_compound=hub_genes[0], page_size=5)
        total = res.get("total_trials")
        phases = res.get("phase_counts") or {}
        engine.done(5.3, "tcm_trials_match", "P2", res,
                    f"clinical trial context for {disease} x "
                    f"{hub_genes[0]}: {total} trials "
                    f"(translational context, not efficacy proof)")
        engine.save()

    # ---- docking branch (M5): auto-run only with complete prerequisites ----
    dock_in = state.get("docking_inputs") or {}
    needs = []
    if not dock_in.get("receptor_pdb"):
        needs.append("receptor_pdb (e.g. '2Z64' for MD-2)")
    if not dock_in.get("pocket_residues"):
        needs.append("pocket_residues FROM LITERATURE as numbers "
                     "(e.g. '46,52,53,...' or 'I46,I52'; never guessed)")
    if not dock_in.get("ligand") and not comps:
        needs.append("ligand (name or CID) or compounds in the goal")
    if needs:
        engine.s["paused_at"] = {"step": 6, "reason":
            "docking prerequisites missing", "needs": needs}
        engine.s["status"] = "paused"
        engine.save()
        return _fmt_session(engine.s, paused=True)
    if step_done():
        return _fmt_session(engine.s, paused=True,
                            note="runtime budget reached")

    # docking chain
    work = SESSIONS_DIR / f"{state['session_id']}_work"
    work.mkdir(parents=True, exist_ok=True)
    lig = dock_in.get("ligand") or (comps[0] if comps else None)
    rec = work / "receptor.pdb"
    lig_sdf = work / "ligand.sdf"
    res = await _invoke("dock_check_environment", 60)
    vina_path = _resolve_vina(dock_in.get("vina_path"))
    if not vina_path or not Path(vina_path).exists():
        engine.s["paused_at"] = {"step": 6, "reason":
            "vina executable not found (provide docking_inputs.vina_path "
            "or set VINA_EXE)"}
        engine.s["status"] = "paused"
        engine.save()
        return _fmt_session(engine.s, paused=True)
    env_ok = "vina" in json.dumps(res, default=str).lower()
    engine.done(6, "dock_check_environment", "M5", res,
                "environment verified" if env_ok else "see raw")
    engine.save()

    res = await _invoke("dock_fetch_structure", 120, source="pdb",
                        query=dock_in["receptor_pdb"],
                        drop_hetatm=True, out=str(rec))
    engine.done(7, "dock_fetch_structure", "M5", res,
                f"receptor {dock_in['receptor_pdb']} -> {rec}")
    engine.save()
    res = await _invoke("dock_fetch_structure", 120, source="pubchem",
                        query=str(lig), out=str(lig_sdf))
    engine.done(8, "dock_fetch_structure", "M5", res,
                f"ligand {lig} -> {lig_sdf}")
    engine.save()
    if not (rec.exists() and lig_sdf.exists()):
        engine.s["status"] = "paused"
        engine.s["paused_at"] = {"step": 9, "reason":
            "structure fetch incomplete (receptor/ligand file missing)"}
        engine.save()
        return _fmt_session(engine.s, paused=True)

    # pocket residues: accept 'I46,I52' or '46,52'; normalize to numbers
    pocket = dock_in["pocket_residues"]
    nums = []
    for tok in str(pocket).replace(";", ",").split(","):
        digits = re.findall(r"\d+", tok)
        if digits:
            nums.append(digits[-1])
    pocket_num = ",".join(nums) if nums else str(pocket)

    out_dir = work / "dock"
    res = await _invoke(
        "dock_vina_docking", 1200, receptor=str(rec), ligand=str(lig_sdf),
        pocket_chain=dock_in.get("pocket_chain", "C"),
        pocket_residues=pocket_num,
        exhaustiveness=dock_in.get("exhaustiveness", 8),
        seed=dock_in.get("seed", 42), vina_path=vina_path,
        workdir=str(out_dir))
    best_pose = out_dir / "best_pose.pdb"
    aff = res.get("best_affinity_kcal_mol")
    if aff is None:
        # dock tools return markdown: read the authoritative vina log product
        vlog = out_dir / "vina_log.txt"
        if vlog.exists():
            m = re.search(r"^\s+1\s+(-?[\d.]+)", vlog.read_text(
                encoding="utf-8", errors="ignore"), re.MULTILINE)
            if m:
                aff = float(m.group(1))
    n_modes = 0
    for line in ((out_dir / "vina_log.txt").read_text(
            encoding="utf-8", errors="ignore").splitlines()
            if (out_dir / "vina_log.txt").exists() else []):
        if re.match(r"^\s+\d+\s+-?[\d.]+", line):
            n_modes += 1
    engine.done(9, "dock_vina_docking", "M5", res,
                f"best affinity {aff} kcal/mol ({n_modes} modes); "
                f"best pose -> {best_pose}")
    if aff is not None:
        engine.s["decisions"].append({
            "point": "D5",
            "rule": (f"affinity {aff} kcal/mol: "
                     + ("<= -7 plausible binder, proceed to contacts"
                        if aff <= -7 else
                        "> -5 weak binder" if aff > -5 else
                        "intermediate; consider more candidates"))})
    engine.save()

    if best_pose.exists():
        res = await _invoke("dock_analyze_contacts", 300,
                            complex_pdb=str(rec), ligand_chain="L",
                            receptor_chains=dock_in.get(
                                "receptor_chains", "A,C"),
                            merge_ligand=str(best_pose),
                            workdir=str(work / "contacts"))
        # markdown output: read the authoritative contacts.json product
        residues = res.get("contact_residues") or res.get("residues") or []
        hbonds = res.get("hbond_candidates") or res.get("hbonds") or []
        cjson = work / "contacts" / "contacts.json"
        if cjson.exists() and not residues:
            try:
                cj = json.loads(cjson.read_text(encoding="utf-8"))
                residues = (cj.get("contacts") or cj.get("contact_residues")
                            or cj.get("residues") or [])
                hbonds = (cj.get("hbond_candidates")
                          or cj.get("hbonds") or cj.get("h_bonds")
                          or hbonds or [])
            except (json.JSONDecodeError, OSError):
                pass
        engine.done(10, "dock_analyze_contacts", "M5", res,
                    f"{len(residues)} contact residues, "
                    f"{len(hbonds)} H-bond candidates (proposed docking "
                    "model; predicted residues marked *)")
        engine.s["decisions"].append({
            "point": "D6",
            "rule": "predicted key residues require UniProt/literature "
                    "cross-check before claiming importance"})
        engine.save()

    engine.s["status"] = "done"
    engine.s.pop("paused_at", None)
    engine.s["finished_at"] = _now_iso()
    engine.save()
    return _fmt_session(engine.s, paused=False)


def _guess_disease(goal: str) -> Optional[str]:
    g = goal.lower()
    m = {"溃疡性结肠炎": "ulcerative colitis", "结肠炎": "colitis",
         "uc": "ulcerative colitis", "糖尿病": "diabetes",
         "哮喘": "asthma", "抑郁": "depression", "肝炎": "hepatitis"}
    for zh, en in m.items():
        if zh in g:
            return en
    for en in ("ulcerative colitis", "colitis", "diabetes", "asthma",
               "depression", "hepatitis", "cancer", "arthritis"):
        if en in g:
            return en
    return None


def _fmt_session(state: Dict, paused: bool, note: str = "") -> str:
    lines = ["# Smart run session", ""]
    lines.append(f"- **session_id**: {state['session_id']}")
    lines.append(f"- **goal**: {state.get('goal') or '(structured input)'}")
    lines.append(f"- **status**: {state.get('status')}")
    lines.append(f"- **created**: {state.get('created_at')}")
    lines.append("")
    lines.append("## executed steps")
    for e in state.get("executed", []):
        lines.append(f"- [{e['step']}] {e['module']} {e['tool']} -> "
                     f"{e['status']}: {e['summary']}")
        if e.get("source") or e.get("queried_at"):
            lines.append(f"    - source: {e.get('source') or 'n/a'}; "
                         f"queried_at: {e.get('queried_at')}")
    if state.get("decisions"):
        lines.append("")
        lines.append("## decision log (auto-branching)")
        for d in state["decisions"]:
            lines.append(f"- **{d['point']}**: {d['rule']}")
    deferred = state.get("deferred_scripts") or []
    if deferred:
        lines.append("")
        lines.append("## deferred scripts (not executed by this engine)")
        for item in deferred:
            lines.append(f"- **{item.get('id')}** ({item.get('status')}): "
                         f"{item.get('reason')}")
            lines.append(f"    - `{item.get('command')}`")
    if paused:
        pa = state.get("paused_at") or {}
        lines.append("")
        lines.append("## PAUSED - action required")
        lines.append(f"- reason: {pa.get('reason', note or 'runtime budget')}")
        for need in pa.get("needs", []):
            lines.append(f"- needs: {need}")
        lines.append("")
        lines.append("**RESUME**: call `tcm_smart_run` again with "
                     f"`session_id=\"{state['session_id']}\"` plus the "
                     "missing `docking_inputs` "
                     "(receptor_pdb / ligand / pocket_chain / "
                     "pocket_residues from literature / vina_path). "
                     "Executed steps are checkpointed and will NOT rerun.")
        lines.append("Then: `tcm_report_generate(session_id=...)` produces "
                     "the review-ready brief.")
    lines.append("")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)


@mcp.tool(
    name="tcm_smart_run",
    annotations={"title": "Autonomous Execution Engine (dynamic analysis "
                          "network driver)",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": True})
async def tcm_smart_run(params: SmartRunInput) -> str:
    '''Autonomously EXECUTE the dynamic analysis network for a research goal.

    Beyond tcm_smart_plan (which only plans): this engine runs the
    automatable chain end-to-end with live data flowing between steps -
    disease -> KEGG/SymMap pathways -> PPI network -> hubs -> dual-engine
    enrichment -> cross-database evidence -> compound consensus -> local
    ADMET -> PubMed co-occurrence -> ClinicalTrials.gov -> docking when
    prerequisites are supplied. depth=full records the herb/ingredient
    script as deferred step D3 and does not run it here. Every step is
    checkpointed to
    ~/.tcm_mcp_state/sessions/<id>.json; decision points D1-D6 branch
    automatically on observed results.

    PAUSES instead of guessing: docking requires literature pocket
    residues and a vina executable; when missing, the session pauses with
    explicit needs, and you resume with session_id + docking_inputs -
    completed steps never rerun.

    Args:
        params (SmartRunInput): goal (+ optional disease/compounds/genes),
            session_id to resume, docking_inputs for the M5 branch,
            max_runtime budget (default 1800 s).

    Returns:
        str: executed-step log with per-step provenance (source,
        queried_at), the decision log, and either the completion summary
        or precise PAUSED instructions.

    Examples:
        - "帮我执行溃疡性结肠炎的靶点网络分析" -> goal="..."
        - resume: session_id="20260101-120000-ab12cd",
          docking_inputs={"receptor_pdb": "2Z64", "ligand": "baicalin",
                          "pocket_chain": "C", "pocket_residues":
                          "I46,I52,...", "vina_path": "..."}
    '''
    return await run_session(params)


# ================= tool 6: tcm_report_generate =================

class ReportGenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., description="session to report on")
    out_path: Optional[str] = Field(
        None, description="optional markdown output path (default: "
                          "<state>/sessions/<id>_report.md)")


@mcp.tool(
    name="tcm_report_generate",
    annotations={"title": "Session Research Brief (review-ready markdown)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_report_generate(params: ReportGenerateInput) -> str:
    '''Turn a smart-run session checkpoint into a review-ready research brief.

    Structure: objective, status/timeline, per-step results with key data
    tables (top pathway, hubs, consensus, affinity, contact residues),
    the decision log (D1-D6 with the observed values that fired each
    branch), evidence-grading statements (docking output = 'proposed
    docking model'), and a full provenance table (source + queried_at per
    step). Designed for supervisor review of network-pharmacology runs.

    Args:
        params (ReportGenerateInput): session_id + optional out_path.

    Returns:
        str: the full markdown brief (also written to out_path if given).
    '''
    p = _session_path(params.session_id)
    if not p.exists():
        return (f"Error: session '{params.session_id}' not found. Run "
                "tcm_smart_run first (it checkpoints every session).")
    s = json.loads(p.read_text(encoding="utf-8"))
    L = [f"# Research brief: {s.get('goal') or params.session_id}", ""]
    L.append(f"- session: {params.session_id}")
    L.append(f"- status: {s.get('status')}")
    L.append(f"- timeline: {s.get('created_at')} -> "
             f"{s.get('finished_at') or s.get('updated_at')}")
    L.append(f"- intent: {json.dumps(s.get('intent', {}), ensure_ascii=False)}")
    L.append("")
    L.append("## Results by step")
    for e in s.get("executed", []):
        L.append(f"### Step {e['step']} - {e['tool']} ({e['module']})")
        L.append(f"- status: {e['status']}")
        L.append(f"- result: {e['summary']}")
        res = e.get("result") or {}
        for key in ("top_hubs", "consensus_terms", "poses", "pathways",
                    "contact_residues"):
            if res.get(key):
                L.append(f"- {key}: "
                         f"{json.dumps(res[key][:8], ensure_ascii=False)[:400]}")
        L.append("")
    if s.get("decisions"):
        L.append("## Decision log")
        for d in s["decisions"]:
            L.append(f"- **{d['point']}**: {d['rule']}")
        L.append("")
    if s.get("paused_at"):
        L.append("## Open items (paused)")
        L.append(f"- {s['paused_at'].get('reason')}")
        for need in s["paused_at"].get("needs", []):
            L.append(f"- needs: {need}")
        L.append("")
    L.append("## Evidence grading")
    L.append("- retrieval steps: live database queries (source/queried_at "
             "per step)")
    L.append("- network/enrichment: computational analysis of live data")
    L.append("- docking: **proposed docking model** - predicted contact "
             "residues are marked * and drawn dashed; pocket residues come "
             "from literature")
    L.append("- structure classification: rule-based prediction "
             "(computational tier)")
    L.append("")
    L.append("## Provenance")
    for e in s.get("executed", []):
        if e.get("source") or e.get("queried_at"):
            L.append(f"- step {e['step']} {e['tool']}: source="
                     f"{e.get('source') or 'n/a'}; queried_at="
                     f"{e.get('queried_at')}")
    L.append(f"- report generated: {_now_iso()}")
    md = "\n".join(L)
    out = Path(params.out_path) if params.out_path else \
        SESSIONS_DIR / f"{params.session_id}_report.md"
    try:
        tmp = out.with_suffix(".tmp")
        tmp.write_text(md, encoding="utf-8")
        os.replace(tmp, out)
        return md + f"\n\n(written to {out})"
    except OSError as e:
        return md + f"\n\n(warning: could not write {out}: {e})"
